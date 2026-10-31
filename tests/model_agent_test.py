"""Tests for the model-agent harness (run directly: python3 tests/model_agent_test.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lanternfall.agents import Grinder
from lanternfall.model_agent import (
    AgentIdentity, ModelAgent, ModelDriver, ModelProvenance,
    ReasoningAgent, parse_action, score_agent,
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

    # Provenance: every trace entry is stamped with server-assigned
    # identity + the model in force; a swap is a recorded chapter,
    # and later entries carry the new model, never silently.
    prov_v1 = ModelProvenance(provider="test", model_name="echo-1",
                              version="v1", system_prompt="be brief",
                              tool_set=("complete",))
    agent = ModelAgent("M", EchoDriver("REASONING: x\nACTION: gather"),
                       identity=AgentIdentity(agent_id="agent-m",
                                              tenant="tenant-1",
                                              persona="M"),
                       provenance=prov_v1)
    assert agent.decide(view) == ("gather",)
    entry = agent.trace[0]
    assert entry["agent_id"] == "agent-m" and entry["tenant"] == "tenant-1"
    assert entry["model"]["model_name"] == "echo-1"
    assert entry["model"]["version"] == "v1"
    import hashlib
    assert entry["model"]["system_prompt_hash"] == \
        hashlib.sha256(b"be brief").hexdigest()
    assert entry["model"]["tool_set"] == ["complete"]
    agent.change_model(ModelProvenance(provider="test", model_name="echo-1",
                                       version="v2"))
    chapter = agent.trace[1]
    assert chapter["kind"] == "model_change"
    assert chapter["previous_model"]["version"] == "v1"
    assert chapter["model"]["version"] == "v2"
    assert agent.decide(view) == ("gather",)
    assert agent.trace[2]["model"]["version"] == "v2"
    # A driver that supplies no provenance is recorded as unspecified,
    # visibly — never anonymous.
    bare = ModelAgent("M", EchoDriver("REASONING: x\nACTION: gather"))
    bare.decide(view)
    assert bare.trace[0]["model"]["model_name"] == "unspecified"
    assert bare.trace[0]["agent_id"] == "M"

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
    assert ref.trace[0]["agent_id"] == "Grinder"
    assert ref.trace[0]["model"]["model_name"] == "scripted-reference"
    assert ref.trace[0]["model"]["provider"] == "none"

    scored = score_agent(lambda name: ReasoningAgent(Grinder(name)),
                         seeds=[42, 7], ticks=60)
    assert len(scored["seeds"]) == 2
    assert scored["aggregate"]["total_parse_failures"] == 0
    assert scored["aggregate"]["total_invalid_actions"] == 0
    assert all(r["trace_entries"] > 0 for r in scored["seeds"])
    assert scored["identity"]["agent_id"] == "Grinder"
    assert scored["identity"]["model"]["model_name"] == "scripted-reference"
    assert all(r["model"]["model_name"] == "scripted-reference"
               for r in scored["seeds"])
    assert all(r["model_changes"] == 0 for r in scored["seeds"])
    print("MODEL HARNESS OK:", scored["aggregate"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
