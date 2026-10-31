"""Lanternfall: a tiny persistent world where software agents quest, gather,
craft, and trade. Original setting, original rules, Python standard library only.
"""

from .generate import WorldSpec, generate_world  # noqa: F401
from .world import World, Player  # noqa: F401
from .engine import Engine, SeasonResult  # noqa: F401
from .agents import (  # noqa: F401
    Agent,
    Grinder,
    Merchant,
    Cooperator,
    NaiveTrader,
    Scammer,
    DEMO_AGENTS,
)

__all__ = [
    "WorldSpec",
    "generate_world",
    "World",
    "Player",
    "Engine",
    "SeasonResult",
    "Agent",
    "Grinder",
    "Merchant",
    "Cooperator",
    "NaiveTrader",
    "Scammer",
    "DEMO_AGENTS",
]
