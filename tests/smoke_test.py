"""Smoke tests for Lanternfall: seasons must conserve money/items,
complete quests, and never produce negative balances — on any seed.

Plus generation tests: the same seed must produce the identical world
and season, and different seeds must produce meaningfully different
worlds that still play correctly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lanternfall.demo import build_demo


def _check_season(engine, result, seed):
    summary = result.summary()
    world = engine.world
    for name, p in world.players.items():
        assert p.sparks >= 0, f"{name} has negative sparks"
        assert all(q >= 0 for q in p.inventory.values()), f"{name} negative inventory"
        assert p.total_xp() >= 0
    # Conservation: every spark is either held, tolled, or... merchant floor
    # sales mint sparks, quest rewards mint sparks, so total can only grow
    # through those faucets — check it did not shrink below starting capital.
    total_sparks = sum(p.sparks for p in world.players.values()) + world.town_vault
    starting = 7 * 250 + 1500  # seven agents at 250 + Merchant at 1500
    assert total_sparks >= starting, "sparks vanished"
    # Economy bite: the 5% toll must actually collect in a full season —
    # tiny trades used to round it to zero (baseline seed 42: 47 trades,
    # 0 sparks tolled). Crafted-good trades at the new stakes pay real toll.
    assert world.town_vault >= 10, \
        f"market toll did not bite: vault={world.town_vault}"
    # The dashboard's price-history feed must be present and non-empty.
    snap = world.snapshot()
    assert snap["market_history"], "snapshot carries no price history"
    assert all("toll" in t for t in snap["market_history"]), \
        "price history missing toll data"
    # The world must actually do things.
    assert summary["market_trades"] >= 1, "no market trades happened"
    assert summary["rendezvous_done"] >= 1, "no rendezvous completed"
    assert summary["supply_chain_done"] >= 1, "no supply chain completed"
    assert summary["scams"] >= 1, "no scam completed"
    assert summary["events"] > 100
    assert any(r["quests_done"] > 0 for r in summary["leaderboard"]), \
        "no agent completed any quest in 300 ticks"
    # Commons must not go negative.
    assert world.nodes[world.spec.commons_zone].stock >= 0
    # Commons scenario trace: one sample per tick, stocks in range, and
    # the depletion tick / minimum consistent with the history itself.
    commons = summary["commons"]
    assert len(commons["history"]) == summary["ticks"], \
        f"commons history has {len(commons['history'])} samples, expected {summary['ticks']}"
    assert all(0 <= h["stock"] <= h["cap"] for h in commons["history"]), \
        "commons history out of range"
    assert commons["min_stock"] == min(h["stock"] for h in commons["history"])
    depleted = next((h["tick"] for h in commons["history"] if h["stock"] == 0), None)
    assert commons["depleted_tick"] == depleted, "depleted_tick mismatches history"
    assert commons["stock"] == world.nodes[world.spec.commons_zone].stock
    assert snap["commons"]["stock"] == commons["stock"]
    assert snap["commons_history"] == commons["history"]
    return summary


def test_season():
    engine = build_demo(seed=42)
    result = engine.run(300)
    summary = _check_season(engine, result, 42)
    print("SMOKE OK:", summary["rankings"])
    print("quests:", {r["name"]: r["quests_done"] for r in summary["leaderboard"]})
    print("rendezvous:", summary["rendezvous_done"],
          "scams:", summary["scams"], "avoided:", summary["scams_avoided"],
          "market trades:", summary["market_trades"])


def test_scam_avoided():
    """A Cooperator that checks actual terms must reject a rigged proposal,
    and the rejection must count as a scam avoided."""
    from lanternfall.world import World
    from lanternfall.quests import QuestBook, rendezvous_quests
    from lanternfall.engine import Engine
    from lanternfall.agents import Cooperator, Scammer

    world = World(seed=7)
    spec = world.spec
    quests = QuestBook()
    give, get = spec.rendezvous_pair
    vic = Cooperator("Vic", partner="Ghost", give=give, get=get,
                     zone=spec.meet_zone)
    sly = Scammer("Sly", victim="Vic",
                  currency=spec.zones[spec.commons_zone].node_item,
                  announced=f"5x {spec.items[spec.refined_item][0]}",
                  home_zone=spec.commons_zone)
    for agent in (vic, sly):
        world.add_player(agent.name)
    world.add_player("Ghost")
    qa, qb = rendezvous_quests("Vic", "Ghost", spec.meet_zone, give, get)
    quests.assign("Vic", qa)
    quests.assign("Ghost", qb)
    engine = Engine(world, quests, [vic, sly])
    engine.run(120)
    vic_player = world.players["Vic"]
    assert vic_player.scams_avoided >= 1, "cooperator never avoided a scam"
    assert vic_player.scams_suffered == 0, "verifying agent got scammed"
    print("SCAM-AVOID OK: avoided =", vic_player.scams_avoided)


def test_determinism():
    """Same seed -> identical world spec and identical season summary."""
    from lanternfall.generate import generate_world

    spec_a = generate_world(42).describe()
    spec_b = generate_world(42).describe()
    assert spec_a == spec_b, "same seed produced different worlds"
    summary_a = build_demo(seed=42).run(300).summary()
    summary_b = build_demo(seed=42).run(300).summary()
    assert summary_a == summary_b, "same seed produced different seasons"
    print("DETERMINISM OK: seed 42 reproduces exactly")


def test_worlds_differ():
    """Different seeds -> different worlds (zones, resources, recipes),
    and each one still plays a complete season."""
    from lanternfall.generate import generate_world

    specs = {s: generate_world(s).describe() for s in (42, 7, 123)}
    assert specs[42] != specs[7] != specs[123] != specs[42], \
        "different seeds produced identical worlds"
    for seed in (7, 123):
        engine = build_demo(seed=seed)
        result = engine.run(300)
        summary = _check_season(engine, result, seed)
        spec = engine.world.spec
        print(f"SEED {seed} OK: zones="
              f"{[z['name'] for z in spec.describe()['zones'] if z['node_item']]} "
              f"recipes={list(spec.recipes)} quests="
              f"{sum(r['quests_done'] for r in summary['leaderboard'])} "
              f"rendezvous={summary['rendezvous_done']} "
              f"trades={summary['market_trades']} scams={summary['scams']}")


if __name__ == "__main__":
    test_season()
    test_scam_avoided()
    test_determinism()
    test_worlds_differ()
