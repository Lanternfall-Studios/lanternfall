"""Tests for the model-agent harness (run directly: python3 tests/model_agent_test.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lanternfall.agents import Grinder
from lanternfall.model_agent import (
    ModelDriver, ModelAgent, ReasoningAgent, parse_action, score_agent,
)


def fake_view() -> dict:
    return {
        "me": {"zone": "hub", "inventory": {}},
        "zones": {"hub": {}, "forest": {}},
        "recipes": {"salt_bar": {}},
        "items": {"salt": {}, "salt_bar": {}},
    }


class EchoDriver(ModelDriver):
    def __init__(self, text: str):
        self.text = text

    def complete(self, view_json: str) -> str:
        assert "hub" in view_json  # the view actually reaches the driver
        return self.text


def main() -> int:
    view = fake_view()
    action, ok, reason = parse_action(
        "REASONING: go gather\nACTION: move forest", view)
    assert (action, ok) == (("move", "forest"), True), action
    assert reason == "go gather"
    assert parse_action("ACTION: move nowhere", view)[1] is False
    assert parse_action("ACTION: dance", view)[1] is False
    assert parse_action("no action here", view)[1] is False
    assert parse_action("ACTION: bid salt 10 5", view)[0] == ("bid", "salt", 10, 5)
    assert parse_action("ACTION: bid salt ten 5", view)[1] is False

    agent = ModelAgent("M", EchoDriver("REASONING: x\nACTION: gather"))
    assert agent.decide(view) == ("gather",)
    assert agent.trace[0]["parsed_ok"] is True

    class BoomDriver(ModelDriver):
        def complete(self, view_json: str) -> str:
            raise RuntimeError("model down")

    agent = ModelAgent("M", BoomDriver())
    assert agent.decide(view) == ("idle",)
    assert agent.trace[0]["parsed_ok"] is False

    ref = ReasoningAgent(Grinder("Grinder"))
    from lanternfall.demo import build_demo
    demo_engine = build_demo(42)
    demo_grinder = next(a for a in demo_engine.agents if a.name == "Grinder")
    real_view = demo_engine.build_view(demo_grinder)
    act = ref.decide(real_view)
    assert isinstance(act, tuple) and ref.trace[0]["reasoning"]

    scored = score_agent(lambda name: ReasoningAgent(Grinder(name)),
                         seeds=[42, 7], ticks=60)
    assert len(scored["seeds"]) == 2
    assert scored["aggregate"]["total_parse_failures"] == 0
    assert scored["aggregate"]["total_invalid_actions"] == 0
    assert all(r["trace_entries"] > 0 for r in scored["seeds"])
    print("MODEL HARNESS OK:", scored["aggregate"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
