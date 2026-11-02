"""The tick loop: builds each agent's view, applies its action, scores the
season, and writes run artifacts. Deterministic when seeded.

Scoring (all from server state):
* sparks, total level, quests completed, trades completed
* peak XP rate: best XP gained in any 10-tick window (the RuneBench-style
  metric — it rewards discovering better strategies, not just grinding)
* promises kept / made (rendezvous coordination) and scams suffered / avoided
"""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict

from .world import World
from .quests import QuestBook, delivery_quests


class Engine:
    def __init__(self, world: World, questbook: QuestBook, agents: list):
        self.world = world
        self.quests = questbook
        self.agents = agents  # list of Agent policy objects
        self.by_name = {a.name: a for a in agents}
        self.promises: list[dict] = []
        self._processed_events = 0
        self.delivery_templates = delivery_quests(world.spec)
        self.ledger_bridge = None  # opt-in; standalone simulations do no I/O
        self._bridge_market_count = 0
        self._bridge_chapters_hash = None
        self._bridge_orders_hash = None

    # -------------------------------------------------------------- view ---
    def build_view(self, agent, consume_chat: bool = True) -> dict:
        w = self.world
        player = w.players[agent.name]
        delivery_ready = False
        if player.zone == w.spec.hub:
            for q in self.quests.open_quests(player.name):
                if q.kind == "delivery" and all(
                    player.has(i, n) for i, n in q.deliver.items()
                ):
                    delivery_ready = True
        view = {
            "tick": w.tick,
            "world_seed": w.spec.seed,
            "recent_chat": [dict(message, id=index) for index, message in
                            enumerate(w.chat_log) if index >= len(w.chat_log) - 128],
            "me": {
                "zone": player.zone,
                "sparks": player.sparks,
                "inventory": dict(player.inventory),
                "xp": dict(player.xp),
                "total_level": player.total_level(),
            },
            # Generated-world tables: everything an agent needs to play any
            # seed without hardcoded items, zones, or recipes.
            "zones": {
                zid: {"name": z.name, "links": list(z.links), "rule": z.rule}
                for zid, z in w.spec.zones.items()
            },
            "items": {
                item: {"display": display, "floor": floor}
                for item, (display, floor) in w.spec.items.items()
            },
            "recipes": {
                out: {"inputs": dict(inputs), "output": out, "qty": qty}
                for out, (inputs, _o, qty, _s, _x) in w.spec.recipes.items()
            },
            "hub": w.spec.hub,
            "key_item": w.spec.key_item,
            "refined_item": w.spec.refined_item,
            "gated_zone": w.spec.gated_zone,
            "commons_zone": w.spec.commons_zone,
            "raw_items": w.spec.raw_items(),
            "nodes": {
                z: {"item": n.item, "stock": n.stock, "cap": n.cap,
                    "secondary": (
                        {"item": s.item, "stock": s.stock, "cap": s.cap}
                        if (s := w.secondary_nodes.get(z)) else None)}
                for z, n in w.nodes.items()
            },
            "bids": [
                {"order_id": o.order_id, "owner": o.owner, "item": o.item,
                 "price": o.price, "qty": o.qty, "tick": o.tick}
                for o in w.bids
            ],
            "asks": [
                {"order_id": o.order_id, "owner": o.owner, "item": o.item,
                 "price": o.price, "qty": o.qty, "tick": o.tick}
                for o in w.asks
            ],
            "chat": w.inbox(agent.name, agent.last_chat_tick),
            "proposals_to_me": [
                {"proposal_id": p.proposal_id, "proposer": p.proposer,
                 "give_item": p.give_item, "give_qty": p.give_qty,
                 "want_item": p.want_item, "want_qty": p.want_qty,
                 "announced": p.announced}
                for p in self.quests.proposals
                if p.target == agent.name and p.status == "open"
            ],
            "my_proposals": [
                {"proposal_id": p.proposal_id, "target": p.target,
                 "give_item": p.give_item, "give_qty": p.give_qty,
                 "want_item": p.want_item, "want_qty": p.want_qty}
                for p in self.quests.proposals
                if p.proposer == agent.name and p.status == "open"
            ],
            "quests": [
                {"quest_id": q.quest_id, "kind": q.kind,
                 "description": q.description, "partner": q.partner,
                 "zone": q.zone, "give_item": q.give_item,
                 "give_qty": q.give_qty, "get_item": q.get_item,
                 "get_qty": q.get_qty, "deliver": dict(q.deliver)}
                for q in self.quests.open_quests(agent.name)
            ],
            "delivery_ready": delivery_ready,
        }
        if consume_chat:
            agent.last_chat_tick = w.tick
        return view

    # ------------------------------------------------------------ action ---
    def apply(self, agent, action: tuple) -> None:
        w = self.world
        player = w.players[agent.name]
        from .sdk import validate_action
        try:
            action = validate_action(action)
        except ValueError:
            w.log("invalid", player=player.name, reason="invalid action shape")
            return
        kind = action[0]
        if kind in {"bid", "ask", "merchant_sell"} and action[1] not in w.spec.items:
            w.log("invalid", player=player.name, reason="unknown item")
            return
        try:
            if kind == "move":
                ok, msg = w.move(player, action[1])
                if not ok:
                    w.log("invalid", player=player.name, action=action, reason=msg)
            elif kind == "gather":
                item = action[1] if len(action) > 1 else None
                ok, msg = w.gather(player, item)
                if not ok:
                    w.log("invalid", player=player.name, action=action, reason=msg)
            elif kind == "craft":
                ok, msg = w.craft(player, action[1])
                if not ok:
                    w.log("invalid", player=player.name, action=action, reason=msg)
                else:
                    # Supply-chain quests complete on the craft half.
                    self.quests.note_craft(w, player, action[1])
            elif kind == "bid":
                _i, item, price, qty = action
                if qty <= 0 or price < 0:
                    w.log("invalid", player=player.name, action=action,
                          reason="non-positive quantity or negative price")
                elif player.sparks < price * qty:
                    w.log("invalid", player=player.name, action=action,
                          reason="cannot afford bid")
                else:
                    w.post_order(player, "bid", item, price, qty)
            elif kind == "ask":
                _i, item, price, qty = action
                if qty <= 0 or price < 0:
                    w.log("invalid", player=player.name, action=action,
                          reason="non-positive quantity or negative price")
                elif not player.has(item, qty):
                    w.log("invalid", player=player.name, action=action,
                          reason="does not own asked items")
                else:
                    w.post_order(player, "ask", item, price, qty)
            elif kind == "cancel_stale":
                max_age = action[1] if len(action) > 1 else 25
                for book in (list(w.bids), list(w.asks)):
                    for o in book:
                        if o.owner == player.name and w.tick - o.tick > max_age:
                            w.cancel_order(player, o.order_id)
            elif kind == "merchant_sell":
                ok, msg = w.merchant_floor_trade(player, action[1])
                if not ok:
                    w.log("invalid", player=player.name, action=action, reason=msg)
            elif kind == "say":
                text = action[1]
                w.say(player, text)
                if text.startswith("MEET"):
                    player.promises_made += 1
                    parts = text.split()
                    zone = parts[1] if len(parts) > 1 else ""
                    self.promises.append(
                        {"player": player.name, "zone": zone, "tick": w.tick})
            elif kind == "propose":
                (_i, target, give, gqty, want, wqty, announced) = action
                if target not in w.players:
                    w.log("invalid", player=player.name, action=action,
                          reason="unknown target")
                elif not player.has(give, gqty):
                    w.log("invalid", player=player.name, action=action,
                          reason="does not own offered items")
                else:
                    self.quests.propose(w, player, target, give, gqty, want, wqty, announced)
            elif kind == "respond":
                (_i, proposal_id, accept) = action
                prop = next((p for p in self.quests.proposals
                             if p.proposal_id == proposal_id), None)
                if prop is not None and not accept:
                    mismatch = prop.announced != f"{prop.give_qty}x {prop.give_item}"
                    if mismatch:
                        player.scams_avoided += 1
                        w.log("scam_avoided", player=player.name,
                              scammer=prop.proposer, announced=prop.announced)
                self.quests.respond(w, player, proposal_id, accept)
            elif kind == "idle":
                pass
            else:
                w.log("invalid", player=player.name, action=action, reason="unknown action")
        except Exception as exc:  # an agent must never crash the world
            w.log("error", player=player.name, action=repr(action), error=str(exc))

    # -------------------------------------------------------------- loop ---
    def step(self) -> "SeasonResult":
        """One tick through precisely the same path as a headless season."""
        return self.run(1)

    def _emit_checkpoint(self):
        if self.ledger_bridge is None:
            return
        from .ledger_bridge import checkpoint, digest
        payload = checkpoint(self)
        empty = digest({a.name: [] for a in self.agents if hasattr(a, "trace")})
        if len(self.world.market_history) > self._bridge_market_count:
            self.ledger_bridge.emit(dict(payload, kind="market_trade"))
        if payload["model_changes_sha256"] != (self._bridge_chapters_hash or empty):
            self.ledger_bridge.emit(dict(payload, kind="model_change"))
        if payload["work_orders_sha256"] != (self._bridge_orders_hash or digest([])):
            self.ledger_bridge.emit(dict(payload, kind="work_order"))
        self.ledger_bridge.emit(payload)
        self._bridge_market_count = len(self.world.market_history)
        self._bridge_chapters_hash = payload["model_changes_sha256"]
        self._bridge_orders_hash = payload["work_orders_sha256"]

    def run(self, ticks: int, verbose: bool = False) -> "SeasonResult":
        w = self.world
        for _ in range(ticks):
            for agent in self.agents:
                player = w.players[agent.name]
                # Deliveries complete automatically when in Haven.
                done = self.quests.try_complete_delivery(w, player)
                for _q in done:
                    template = self.world.rng.choice(self.delivery_templates)
                    self.quests.assign(player.name, copy.deepcopy(template))
                view = self.build_view(agent)
                action = agent.decide(view)
                self.apply(agent, action)
            # Expire stale proposals.
            for prop in self.quests.proposals:
                if prop.status == "open" and w.tick - prop.tick > 30:
                    prop.status = "expired"
            # Credit kept promises for any rendezvous completed this tick.
            for e in w.event_log[self._processed_events:]:
                if e["kind"] == "rendezvous_done":
                    for ply in e["players"]:
                        for pr in self.promises:
                            if (pr["player"] == ply and pr["zone"] == e["zone"]
                                    and not pr.get("counted")):
                                pr["counted"] = True
                                w.players[ply].promises_kept += 1
            self._processed_events = len(w.event_log)
            w.regrow()
            w.tick += 1
            # Commons scenario trace: one sample per tick, post-regrow.
            commons_node = w.nodes[w.spec.commons_zone]
            w.commons_history.append(
                {"tick": w.tick, "stock": commons_node.stock,
                 "cap": commons_node.cap})
            for p in w.players.values():
                p.xp_history.append((w.tick, p.total_xp()))
            self._emit_checkpoint()
            if verbose and w.tick % 50 == 0:
                print(f"--- tick {w.tick} ---")
                for p in w.players.values():
                    print(f"  {p.name}: {p.zone} sparks={p.sparks} "
                          f"lvl={p.total_level()} quests={p.quests_done} "
                          f"inv={dict(p.inventory)}")
        return SeasonResult(self)

    # ------------------------------------------------------------ scoring --
    def peak_xp_rate(self, player, window: int = 10) -> int:
        hist = player.xp_history
        best = 0
        for i in range(len(hist)):
            for j in range(i + 1, len(hist)):
                if hist[j][0] - hist[i][0] <= window:
                    best = max(best, hist[j][1] - hist[i][1])
                else:
                    break
        return best

    def leaderboard(self) -> list[dict]:
        rows = []
        for p in self.world.players.values():
            rows.append({
                "name": p.name,
                "zone": p.zone,
                "sparks": p.sparks,
                "total_level": p.total_level(),
                "xp": dict(p.xp),
                "quests_done": p.quests_done,
                "trades_done": p.trades_done,
                "peak_xp_rate_10t": self.peak_xp_rate(p),
                "promises": f"{p.promises_kept}/{p.promises_made}",
                "scams_suffered": p.scams_suffered,
                "scams_avoided": p.scams_avoided,
                "inventory": dict(p.inventory),
            })
        return rows


class SeasonResult:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.world = engine.world
        self.leaderboard = engine.leaderboard()
        self.market_history = list(self.world.market_history)
        self.events = list(self.world.event_log)

    def summary(self) -> dict:
        w = self.world
        by_sparks = sorted(self.leaderboard, key=lambda r: -r["sparks"])
        by_level = sorted(self.leaderboard, key=lambda r: -r["total_level"])
        by_quests = sorted(self.leaderboard, key=lambda r: -r["quests_done"])
        by_peak = sorted(self.leaderboard, key=lambda r: -r["peak_xp_rate_10t"])
        return {
            "ticks": w.tick,
            "leaderboard": self.leaderboard,
            "rankings": {
                "by_sparks": [r["name"] for r in by_sparks],
                "by_total_level": [r["name"] for r in by_level],
                "by_quests": [r["name"] for r in by_quests],
                "by_peak_xp_rate": [r["name"] for r in by_peak],
            },
            "market_trades": len(self.market_history),
            "town_vault_tolls": w.town_vault,
            "events": len(self.events),
            "rendezvous_done": sum(1 for e in self.events if e["kind"] == "rendezvous_done"),
            "supply_chain_done": sum(1 for e in self.events if e["kind"] == "supply_chain_done"),
            "scams": sum(1 for e in self.events if e["kind"] == "scam"),
            "scams_avoided": sum(1 for e in self.events if e["kind"] == "scam_avoided"),
            "commons_zone": w.spec.commons_zone,
            "commons_stock": w.nodes[w.spec.commons_zone].stock,
            # Commons scenario: the shared-stock meter, in data form.
            # depleted_tick is the first tick the stock hit zero, or None
            # if the commons survived the season.
            "commons": {
                "zone": w.spec.commons_zone,
                "name": w.spec.zones[w.spec.commons_zone].name,
                "item": w.nodes[w.spec.commons_zone].item,
                "stock": w.nodes[w.spec.commons_zone].stock,
                "cap": w.nodes[w.spec.commons_zone].cap,
                "min_stock": min(
                    (h["stock"] for h in w.commons_history),
                    default=w.nodes[w.spec.commons_zone].stock),
                "depleted_tick": next(
                    (h["tick"] for h in w.commons_history
                     if h["stock"] == 0),
                    None),
                "history": list(w.commons_history),
            },
            "world": w.spec.describe(),
        }

    def write(self, out_dir: str) -> dict:
        os.makedirs(out_dir, exist_ok=True)
        summary = self.summary()
        with open(os.path.join(out_dir, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        from .ledger_bridge import canonical
        with open(os.path.join(out_dir, "events.jsonl"), "wb") as f:
            for e in self.events:
                f.write(canonical(e) + b"\n")
        with open(os.path.join(out_dir, "world.json"), "wb") as f:
            f.write(canonical(self.world.snapshot()))
        return summary


def default_run_dir() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join("runs", stamp)
