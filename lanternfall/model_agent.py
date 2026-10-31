"""Model-agent harness for Lanternfall.

This is the seam where an external model driver plugs into the game.
Agents in Lanternfall already implement ``decide(view) -> action``; this
module adds:

* :class:`ModelDriver` — the interface a real model driver implements.
  It takes the observable view as JSON and returns raw text. NO driver
  is wired up here: drivers are supplied by the caller, never invented,
  and no credentials, signups, or spending are involved.
* :func:`parse_action` — turns a model's text reply into a validated
  engine action tuple. Anything unparseable or invalid becomes
  ``("idle",)`` and is counted, so a confused model can never crash
  the world or take an illegal action.
* :class:`ModelAgent` — an :class:`Agent` that drives through a
  ``ModelDriver`` and keeps a reasoning trace.
* :class:`ReasoningAgent` — the reference reasoning-trace agent. It has
  no model behind it: it wraps a proven scripted policy and emits a
  short reason for every action, in the exact trace format a model
  driver would produce. It exists so the harness, the scoring, and the
  trace format can be verified without any model at all.
* :func:`score_agent` — runs a candidate agent across many seeds and
  reports per-seed results plus aggregates, so models (and policies)
  can be compared on identical worlds.

Reply format a driver is expected to emit::

    REASONING: <one line of reasoning>
    ACTION: <verb> [args]

Verbs: move <zone> | gather | craft <recipe> | bid <item> <price> <qty>
| ask <item> <price> <qty> | cancel_stale <max_age>
| merchant_sell <item> | say <text> | respond <proposal_id> yes|no | idle
"""

from __future__ import annotations

import json

from .agents import Agent


class ModelDriver:
    """A model behind the harness. Implement complete() and inject it."""

    def complete(self, view_json: str) -> str:
        raise NotImplementedError


def _int(arg: str) -> int | None:
    try:
        return int(arg)
    except (TypeError, ValueError):
        return None


def parse_action(text: str, view: dict) -> tuple[tuple, bool, str]:
    """Parse model text into (action, parsed_ok, reasoning).

    Returns ("idle",) with parsed_ok False on anything unusable.
    """
    reasoning = ""
    action_line = ""
    for line in (text or "").splitlines():
        line = line.strip()
        if line.upper().startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()
        elif line.upper().startswith("ACTION:"):
            action_line = line.split(":", 1)[1].strip()
    if not action_line:
        return ("idle",), False, reasoning
    parts = action_line.split()
    verb = parts[0].lower()
    args = parts[1:]
    zones = view.get("zones", {})
    recipes = view.get("recipes", {})
    items = view.get("items", {})

    def fail() -> tuple[tuple, bool, str]:
        return ("idle",), False, reasoning

    if verb == "idle" and not args:
        return ("idle",), True, reasoning
    if verb == "gather" and not args:
        return ("gather",), True, reasoning
    if verb == "move" and len(args) == 1 and args[0] in zones:
        return ("move", args[0]), True, reasoning
    if verb == "craft" and len(args) == 1 and args[0] in recipes:
        return ("craft", args[0]), True, reasoning
    if verb == "merchant_sell" and len(args) == 1 and args[0] in items:
        return ("merchant_sell", args[0]), True, reasoning
    if verb == "cancel_stale" and len(args) == 1 and _int(args[0]) is not None:
        return ("cancel_stale", _int(args[0])), True, reasoning
    if verb in ("bid", "ask") and len(args) == 3 and args[0] in items:
        price, qty = _int(args[1]), _int(args[2])
        if price is not None and qty is not None:
            return (verb, args[0], price, qty), True, reasoning
    if verb == "say" and args:
        return ("say", " ".join(args)), True, reasoning
    if verb == "respond" and len(args) == 2 and args[1].lower() in ("yes", "no"):
        return ("respond", args[0], args[1].lower() == "yes"), True, reasoning
    return fail()


class ModelAgent(Agent):
    """An agent driven by an injected ModelDriver, with a trace."""

    def __init__(self, name: str, driver: ModelDriver):
        super().__init__(name)
        self.driver = driver
        self.trace: list[dict] = []

    def decide(self, view: dict) -> tuple:
        view_json = json.dumps(view, default=str)
        try:
            text = self.driver.complete(view_json)
        except Exception as exc:  # a driver must never crash the world
            self.trace.append({"reasoning": "", "raw": "",
                               "action": ("idle",), "parsed_ok": False,
                               "error": str(exc)})
            return ("idle",)
        action, ok, reasoning = parse_action(text, view)
        self.trace.append({"reasoning": reasoning, "raw": text,
                           "action": action, "parsed_ok": ok})
        return action


def explain(view: dict, action: tuple) -> str:
    """A short reason for a scripted action, in trace format."""
    kind = action[0]
    me = view["me"]
    if kind == "idle":
        return "waiting in place; no better move available"
    if kind == "gather":
        return f"gathering in {me['zone']}; node stock here"
    if kind == "move":
        return f"moving toward {action[1]} to reach its node"
    if kind == "craft":
        return f"crafting {action[1]}; inputs on hand"
    if kind == "say":
        return "talking to coordinate with another agent"
    if kind == "propose":
        return "proposing a direct trade"
    if kind == "respond":
        return "answering a trade proposal"
    if kind in ("bid", "ask"):
        return f"posting a market {kind} for {action[1]}"
    if kind == "merchant_sell":
        return f"floor-selling {action[1]} to the town merchant"
    return f"acting: {kind}"


class ReasoningAgent(Agent):
    """Reference reasoning-trace agent: proven policy + emitted reasons.

    Wraps any scripted agent and records, for every decision, the same
    trace entry a model driver would produce: the reasoning line and
    the action. No model is involved; this verifies the harness end
    to end on behavior already known to play sensibly.
    """

    def __init__(self, inner: Agent):
        super().__init__(inner.name)
        self.inner = inner
        self.trace: list[dict] = []

    def decide(self, view: dict) -> tuple:
        action = self.inner.decide(view)
        reason = explain(view, action)
        self.trace.append({"reasoning": reason,
                           "raw": f"REASONING: {reason}\nACTION: "
                                  f"{action[0]}"
                                  f"{' ' + ' '.join(str(a) for a in action[1:]) if len(action) > 1 else ''}",
                           "action": action, "parsed_ok": True})
        return action


def score_agent(factory, seeds, ticks: int = 300) -> dict:
    """Score a candidate agent across seeds.

    ``factory(name)`` must return an agent named ``name``; it replaces
    the demo cast's Grinder so the candidate faces identical quests,
    markets, and opponents on every seed. Returns per-seed rows plus
    aggregates. Importing here keeps demo construction out of the
    module top level (demo imports agents, not the reverse).
    """
    from .demo import build_demo

    rows = []
    for seed in seeds:
        engine = build_demo(seed)
        candidate = factory("Grinder")
        engine.agents = [candidate if a.name == "Grinder" else a
                         for a in engine.agents]
        result = engine.run(ticks)
        summary = result.summary()
        row = next(r for r in summary["leaderboard"] if r["name"] == "Grinder")
        trace = getattr(candidate, "trace", [])
        rows.append({
            "seed": seed,
            "sparks": row["sparks"],
            "total_level": row["total_level"],
            "quests_done": row["quests_done"],
            "trades_done": row["trades_done"],
            "peak_xp_rate_10t": row["peak_xp_rate_10t"],
            "scams_suffered": row["scams_suffered"],
            "scams_avoided": row["scams_avoided"],
            "trace_entries": len(trace),
            "parse_failures": sum(1 for t in trace if not t["parsed_ok"]),
            "invalid_actions": sum(1 for e in result.events
                                   if e["kind"] == "invalid"
                                   and e.get("player") == "Grinder"),
            "market_trades": summary["market_trades"],
            "rendezvous_done": summary["rendezvous_done"],
        })
    n = len(rows) or 1
    agg = {k: round(sum(r[k] for r in rows) / n, 2)
           for k in ("sparks", "total_level", "quests_done", "trades_done",
                     "peak_xp_rate_10t", "market_trades")}
    agg["total_parse_failures"] = sum(r["parse_failures"] for r in rows)
    agg["total_invalid_actions"] = sum(r["invalid_actions"] for r in rows)
    return {"candidate": "Grinder-slot", "ticks": ticks,
            "seeds": rows, "aggregate": agg}
