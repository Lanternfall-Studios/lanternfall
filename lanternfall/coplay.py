"""Local shared-room tick owner. HTTP and policies enqueue identical primitives.

Roster names are local slots, not cryptographically authenticated human identities.
No shell, dynamic plugins, repository credentials, or model spend is available.
"""
from __future__ import annotations

from collections import deque
import copy
import os
from pathlib import Path
import threading

from .agents import Agent
from .engine import Engine
from .quests import QuestBook
from .model_agent import AgentIdentity, ModelProvenance
from .sdk import validate_action, path_between
from .work_orders import WorkOrders
from .world import World

PULSE_NS = 314159265
ROSTER = ("chris", "jon", "steward", "engineer", "steve", "tom", "thales")


class QueuedActor(Agent):
    def __init__(self, name):
        super().__init__(name)
        self.actions = deque()
        self.identity = AgentIdentity(name)
        self.provenance = ModelProvenance("none", "queued-local-slot", "1", tool_set=("game-actions",))
        self.trace = []

    def decide(self, view):
        action = self._decide(view)
        self.trace.append({**self.identity.to_dict(), "tick": view["tick"],
                           "model": self.provenance.to_dict(), "action": action})
        return action

    def _decide(self, view):
        return self.actions.popleft() if self.actions else ("idle",)

    def trajectory_hash(self):
        from .ledger_bridge import digest
        return digest(self.trace)


class WorkActor(QueuedActor):
    def __init__(self, name, orders):
        super().__init__(name)
        self.orders = orders
        self.chat_cursor = -1
        self.provenance = ModelProvenance("none", "scripted-work-coordinator", "1", tool_set=("game-actions",))

    def _decide(self, view):
        if self.actions:
            return super()._decide(view)
        for message in view["recent_chat"]:
            if message["id"] <= self.chat_cursor:
                continue
            self.chat_cursor = message["id"]
            prefix = f"@{self.name} "
            if not message["text"].lower().startswith(prefix) or message["from"] == self.name:
                continue
            try:
                order = self.orders.submit(message["from"], self.name,
                    message["text"][len(prefix):].strip(), view["tick"],
                    message["id"], view["world_seed"])
            except ValueError:
                return ("say", "work order rejected: queue limit or invalid directive")
            return ("say", f"work order {order['id'][:12]} queued for {order['station']}")
        for order in self.orders.orders.values():
            if order["target"] == self.name and order["status"] == "queued":
                route = path_between(view, view["hub"])
                if route:
                    return route[0]
                self.orders.start(order["id"])
                return ("say", f"work order {order['id'][:12]} in_progress at {order['station']}; awaiting authorized worker")
        return ("idle",)


class Room:
    def __init__(self, engine, work_orders, events_path=None):
        self.engine = engine
        self.work_orders = work_orders
        self.work_orders.on_status = self._work_status
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.error = None
        self.thread = None
        self.events_path = Path(events_path) if events_path else None
        self._written_events = 0
        if self.events_path:
            fd = os.open(self.events_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)

    def _work_status(self, order):
        self.engine.world.log("work_order", order_id=order["id"],
                              status=order["status"], target=order["target"],
                              station=order["station"],
                              completion_sha256=order.get("completion_sha256"))

    def complete_work(self, identifier, artifact, verification):
        """Trusted local worker API, intentionally unavailable to HTTP/game actors."""
        with self.lock:
            if self.error:
                raise ValueError("room paused")
            order = self.work_orders.orders.get(identifier)
            if not order or order["status"] != "in_progress" or not artifact or not verification:
                raise ValueError("active work and external evidence required")
            try:
                order = self.work_orders.complete(identifier, artifact, verification)
                self._flush_events()
                self.engine._emit_checkpoint()
                return order
            except Exception:
                self.error = "completion persistence failure; operator inspection required"
                self.stopped.set()
                raise

    def submit(self, actor, action):
        with self.lock:
            if self.error:
                raise ValueError("room paused after persistence failure")
            if actor not in self.engine.by_name:
                raise ValueError("unknown actor")
            queue = self.engine.by_name[actor].actions
            if len(queue) >= 16:
                raise ValueError("actor queue full")
            queue.append(validate_action(action))
            return {"status": "queued", "actor": actor,
                    "queue_depth": len(queue), "tick": self.engine.world.tick}

    def view(self, actor):
        with self.lock:
            if actor not in self.engine.by_name:
                raise ValueError("unknown actor")
            return copy.deepcopy(self.engine.build_view(self.engine.by_name[actor], consume_chat=False))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(dict(self.engine.world.snapshot(), room_error=self.error,
                                      work_orders=[{k: v for k, v in order.items() if k != "directive"}
                                                   for order in self.work_orders.orders.values()],
                                      workstations={"Library": self.engine.world.spec.hub,
                                                    "Forge": self.engine.world.spec.hub}))

    def _flush_events(self):
        if self.events_path:
            from .ledger_bridge import canonical
            with open(os.open(self.events_path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW), "wb") as output:
                for event in self.engine.world.event_log[self._written_events:]:
                    output.write(canonical(event) + b"\n")
                output.flush()
                os.fsync(output.fileno())
            self._written_events = len(self.engine.world.event_log)

    def step(self):
        with self.lock:
            if self.error:
                raise ValueError("room paused; inspect persistence before retry")
            try:
                self.work_orders.poll_completions()
                result = self.engine.step()
                self._flush_events()
                return result
            except Exception:
                self.error = "tick/persistence failure; operator inspection required"
                self.stopped.set()
                raise

    def start(self):
        if self.thread is not None:
            raise ValueError("room already started")
        def loop():
            # Wait one pulse before the first turn; overload never triggers a catch-up burst.
            while not self.stopped.wait(PULSE_NS / 1_000_000_000):
                try:
                    self.step()
                except Exception:
                    with self.lock:
                        self.error = "tick/persistence failure; operator inspection required"
                    self.stopped.set()
        self.thread = threading.Thread(target=loop, daemon=True, name="lanternfall-ticks")
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=20)


def build_coplay(seed=42, outbox=None, events_path=None):
    world = World(seed=seed)
    orders = WorkOrders(outbox)
    actors = [WorkActor(name, orders) if name in {"steward", "engineer"}
              else QueuedActor(name) for name in ROSTER]
    for actor in actors:
        world.add_player(actor.name)
    return Room(Engine(world, QuestBook(), actors), orders, events_path)
