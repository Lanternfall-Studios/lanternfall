"""Demo season setup: eight scripted agents, quests, and starting resources.

Everything demo-specific is derived from the generated world spec, so the
same cast plays sensibly on any seed: the rendezvous pair, meet zone,
scam currency, and quest templates all come from the world itself.
"""

from __future__ import annotations

import copy

from .world import World
from .quests import QuestBook, delivery_quests, rendezvous_quests, supply_chain_quests
from .agents import (
    Grinder, Merchant, Cooperator, NaiveTrader, Scammer,
    ChainGatherer, ChainCrafter,
)
from .engine import Engine


def build_demo(seed: int = 42) -> Engine:
    world = World(seed=seed)
    spec = world.spec
    quests = QuestBook()

    give, get = spec.rendezvous_pair
    commons_item = spec.zones[spec.commons_zone].node_item
    # Split supply chain: the refined good's raw is gathered by Chain-A
    # and crafted by Chain-B; Chain-B pays with 1 unit of its home good.
    refined_base = next(iter(spec.recipes[spec.refined_item][0]))
    home_item = next(r for r in spec.raw_items() if r != refined_base)
    agents = [
        Grinder("Grinder"),
        Merchant("Merchant"),
        Cooperator("Coop-A", partner="Coop-B", give=give, get=get,
                   zone=spec.meet_zone),
        Cooperator("Coop-B", partner="Coop-A", give=get, get=give,
                   zone=spec.meet_zone),
        NaiveTrader("Naive", home_zone=spec.commons_zone),
        Scammer("Sly", victim="Naive", currency=commons_item,
                announced=f"5x {spec.items[spec.refined_item][0]}",
                home_zone=spec.commons_zone),
        ChainGatherer("Chain-A", partner="Chain-B", raw=refined_base,
                      want_item=home_item),
        ChainCrafter("Chain-B", partner="Chain-A", raw=refined_base,
                     refined=spec.refined_item, home_item=home_item),
    ]
    for agent in agents:
        player = world.add_player(agent.name)
        if agent.name == "Merchant":
            player.sparks = 1500  # market maker bankroll: big enough to
            # buy crafted goods in bulk, so the 5% toll actually bites

    templates = {q.quest_id: q for q in delivery_quests(spec)}
    quests.assign("Grinder", copy.deepcopy(templates["raw-run"]))
    quests.assign("Grinder", copy.deepcopy(templates["refined-run"]))
    quests.assign("Naive", copy.deepcopy(templates["raw-run"]))
    qa, qb = rendezvous_quests("Coop-A", "Coop-B", spec.meet_zone, give, get)
    quests.assign("Coop-A", qa)
    quests.assign("Coop-B", qb)
    sa, sb = supply_chain_quests("Chain-A", "Chain-B", refined_base,
                                 spec.refined_item)
    quests.assign("Chain-A", sa)
    quests.assign("Chain-B", sb)

    return Engine(world, quests, agents)
