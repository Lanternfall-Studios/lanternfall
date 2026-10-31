"""Seeded procedural world generation for Lanternfall.

This is the product: from one integer seed, generate an entire open world
— zone graph, biomes, resource nodes, regrowth rates, and a crafting
chain derived from the generated resources. Same seed always produces the
identical world; different seeds produce meaningfully different worlds.

What the seed controls (drawn in a fixed order from one Random instance,
so generation is fully deterministic):
  * how many zones exist (hub + 3-6 gather zones + 1 gated zone)
  * each zone's biome, display name, and connections (a connected graph:
    a random spanning tree over the hub plus 0-2 extra links)
  * which raw resource each zone holds, its stock cap, and its regrow rate
    — plus a secondary node (a second resource with its own stock and
    regrow rate) when the biome has a fresh resource left
  * which zone is the shared commons (slow regrow, big cap — the
    tragedy-of-the-commons zone)
  * the crafting chain: a refined good, a key item that unlocks the gated
    zone, and an elite good made from the gated resource
  * the rendezvous trade pair (two distinct non-gated raw resources)
  * the supply-chain pair (gatherer raw -> crafter refined good)

What stays fixed regardless of seed (the rules, not the content):
  * one town/market hub ("Haven"), one gated late-game zone, one commons
  * the market (order book, 5% toll), quests, rendezvous, scams,
    peak-XP-rate scoring, and the decide(view) -> action agent interface
  * the shape of the crafting chain: raw -> refined, two raws -> key,
    gated raw + refined -> elite

All names, biomes, resources, and items are original to Lanternfall. No
names, assets, art, music, or lore from any other game appear anywhere.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

HUB_ID = "haven"
HUB_NAME = "Haven"

# biome -> (candidate zone display names, candidate raw resources)
BIOMES = {
    "forest": (
        ["Sunwood Grove", "Emberwood", "Thornwild", "Whisperwood"],
        ["timber", "resin", "mushroom"],
    ),
    "quarry": (
        ["Cinder Quarry", "Broken Shelf", "Graveldeep", "Stonefall Cut"],
        ["ore", "stone", "crystal"],
    ),
    "meadow": (
        ["Mistral Fields", "Goldgrass Plain", "Wrenmeadow", "Highpasture"],
        ["herb", "flax", "grain"],
    ),
    "marsh": (
        ["Stillfen Marsh", "Blackreed Bog", "Murmuring Fen"],
        ["reed", "clay", "mushroom"],
    ),
    "dunes": (
        ["Glass Dunes", "Amber Expanse", "Singing Sands"],
        ["salt", "amber", "sand"],
    ),
    "caverns": (
        ["Hollow Deeps", "Glimmer Caves", "Underhollow"],
        ["crystal", "ore", "glowcap"],
    ),
    "coast": (
        ["Saltbreak Shore", "Pearl Strand", "Tidewash"],
        ["pearl", "salt", "shell"],
    ),
    "highlands": (
        ["Cragmoor", "Windcut Ridge", "Stonestair Heights"],
        ["ore", "herb", "stone"],
    ),
    "tundra": (
        ["Frostfall Steppe", "Whitewaste", "Bitterfield"],
        ["frostroot", "icebloom", "stone"],
    ),
    "jungle": (
        ["Vinevault", "Canopy Deep", "Orchid Wilds"],
        ["vinefruit", "spice", "resin"],
    ),
    "volcanic": (
        ["Ashfield", "Cinder Maw", "Smokespire"],
        ["obsidian", "sulfur", "ore"],
    ),
}

# gated-zone display names (the late-game zone, one per world)
GATED_NAMES = [
    "Ember Ruins", "Sunken Vault", "Starfall Crater", "Silent Deep",
    "Ashen Citadel", "Veil of Echoes",
]

# raw resource id -> (display name, floor price in sparks, gather skill)
RAW_RESOURCES = {
    "timber": ("Timber", 3, "forage"),
    "resin": ("Resin", 4, "forage"),
    "mushroom": ("Cave Mushroom", 5, "forage"),
    "ore": ("Iron Ore", 4, "mine"),
    "stone": ("Rough Stone", 3, "mine"),
    "crystal": ("Crystal", 8, "mine"),
    "herb": ("Sunherb", 5, "forage"),
    "flax": ("Flax", 4, "forage"),
    "grain": ("Wild Grain", 3, "forage"),
    "reed": ("Marsh Reed", 3, "forage"),
    "clay": ("River Clay", 4, "mine"),
    "salt": ("Sea Salt", 4, "mine"),
    "amber": ("Amber", 9, "mine"),
    "sand": ("Glass Sand", 3, "mine"),
    "glowcap": ("Glowcap", 6, "forage"),
    "pearl": ("River Pearl", 10, "forage"),
    "shell": ("Tide Shell", 4, "forage"),
    "frostroot": ("Frostroot", 5, "forage"),
    "icebloom": ("Icebloom", 7, "forage"),
    "vinefruit": ("Vinefruit", 4, "forage"),
    "spice": ("Wild Spice", 8, "forage"),
    "obsidian": ("Obsidian", 9, "mine"),
    "sulfur": ("Sulfur", 5, "mine"),
}

# gated-zone raw resource id -> (display name, floor price, gather skill)
GATED_RESOURCES = {
    "shard": ("Ember Shard", 40, "mine"),
    "relic": ("Old Relic", 45, "mine"),
    "starmetal": ("Starmetal", 50, "mine"),
    "voidglass": ("Voidglass", 42, "forage"),
}

# key item id -> display name (the crafted item that unlocks the gated zone)
KEY_ITEMS = {
    "lantern": "Lantern",
    "waykey": "Waykey",
    "sigil": "Trail Sigil",
    "compass": "Ember Compass",
    "torch": "Everflame Torch",
}


@dataclass
class ZoneSpec:
    zone_id: str
    name: str
    biome: str
    links: list = field(default_factory=list)
    node_item: str | None = None  # raw resource id, None for the hub
    node_cap: int = 0
    regrow_every: int = 5  # ticks between +1 stock
    # Second node in the same zone (multi-node zones): a different raw
    # resource with its own stock, cap, and regrow rate. None when the
    # zone has only one node.
    node2_item: str | None = None
    node2_cap: int = 0
    node2_regrow: int = 5
    rule: str | None = None  # None | "commons" | "needs_key"


@dataclass
class WorldSpec:
    seed: int
    zones: dict  # zone_id -> ZoneSpec
    items: dict  # item id -> (display name, floor price)
    recipes: dict  # output item id -> (inputs, output, qty, skill, xp)
    hub: str = HUB_ID
    gated_zone: str = ""
    key_item: str = ""
    commons_zone: str = ""
    refined_item: str = ""
    gated_item: str = ""
    elite_item: str = ""
    rendezvous_pair: tuple = ("", "")  # (give_item, get_item)
    meet_zone: str = ""

    # -- derived helpers -------------------------------------------------
    def raw_items(self) -> list[str]:
        """Non-gated raw resources (merchant buys these at floor price).
        Primary nodes first, then secondary nodes, in zone order."""
        items = [z.node_item for z in self.zones.values()
                 if z.node_item and z.rule != "needs_key"]
        items += [z.node2_item for z in self.zones.values()
                  if z.node2_item and z.rule != "needs_key"]
        return items

    def zone_of(self, item: str) -> str:
        for z in self.zones.values():
            if z.node_item == item or z.node2_item == item:
                return z.zone_id
        return self.hub

    def describe(self) -> dict:
        """Human-readable world summary (used by --world, dashboard, tests)."""
        return {
            "seed": self.seed,
            "zones": [
                {"id": z.zone_id, "name": z.name, "biome": z.biome,
                 "links": list(z.links), "node_item": z.node_item,
                 "node_cap": z.node_cap, "regrow_every": z.regrow_every,
                 "node2_item": z.node2_item, "node2_cap": z.node2_cap,
                 "node2_regrow": z.node2_regrow,
                 "rule": z.rule}
                for z in self.zones.values()
            ],
            "items": {i: {"display": d, "floor": f}
                      for i, (d, f) in self.items.items()},
            "recipes": {
                out: {"inputs": dict(inp), "output": out, "qty": qty}
                for out, (inp, _o, qty, _s, _x) in self.recipes.items()
            },
            "hub": self.hub,
            "gated_zone": self.gated_zone,
            "key_item": self.key_item,
            "commons_zone": self.commons_zone,
            "refined_item": self.refined_item,
            "gated_item": self.gated_item,
            "elite_item": self.elite_item,
            "rendezvous_pair": list(self.rendezvous_pair),
            "meet_zone": self.meet_zone,
        }


def generate_world(seed: int) -> WorldSpec:
    rng = random.Random(seed)

    # --- zone count and biomes -------------------------------------------
    n_gather = rng.randint(3, 6)  # non-gated gather zones
    biomes = rng.sample(sorted(BIOMES), n_gather)
    gated_name = rng.choice(GATED_NAMES)

    zones: dict[str, ZoneSpec] = {
        HUB_ID: ZoneSpec(HUB_ID, HUB_NAME, "town"),
    }
    # Gated zone: connects only to the hub, so the key item is the only way in.
    gated_item = rng.choice(sorted(GATED_RESOURCES))
    gated_display, gated_floor, gated_skill = GATED_RESOURCES[gated_item]
    zones["gated"] = ZoneSpec(
        "gated", gated_name, "gated", links=[HUB_ID],
        node_item=gated_item, node_cap=rng.randint(20, 30),
        regrow_every=rng.randint(4, 6), rule="needs_key",
    )
    zones[HUB_ID].links.append("gated")

    # --- gather zones: name, resource, cap, regrow ------------------------
    # Each zone also gets a second node when its biome has a fresh
    # resource left over (multi-node zones: two gatherables per zone,
    # each with its own stock and regrow rate).
    used_resources: set[str] = set()
    used_secondary: set[str] = set()
    gather_ids: list[str] = []
    for biome in biomes:
        names, candidates = BIOMES[biome]
        zone_id = biome  # biomes are sampled without replacement, so unique
        name = rng.choice(names)
        fresh = [c for c in candidates if c not in used_resources]
        resource = rng.choice(fresh if fresh else candidates)
        used_resources.add(resource)
        fresh2 = [c for c in candidates
                  if c not in used_resources and c not in used_secondary]
        node2 = rng.choice(fresh2) if fresh2 else None
        if node2 is not None:
            used_secondary.add(node2)
        zones[zone_id] = ZoneSpec(
            zone_id, name, biome, links=[],
            node_item=resource, node_cap=rng.randint(40, 60),
            regrow_every=rng.randint(4, 6),
            node2_item=node2,
            node2_cap=rng.randint(25, 40) if node2 else 0,
            node2_regrow=rng.randint(4, 7) if node2 else 5,
        )
        gather_ids.append(zone_id)

    # --- commons: one gather zone gets a big, slow-regrowing stock --------
    commons_zone = rng.choice(gather_ids)
    zones[commons_zone].rule = "commons"
    zones[commons_zone].node_cap = rng.randint(80, 110)
    zones[commons_zone].regrow_every = rng.randint(8, 12)

    # --- graph: random spanning tree over the hub, plus 0-2 extra links ---
    connected = [HUB_ID]
    remaining = list(gather_ids)
    rng.shuffle(remaining)
    for zone_id in remaining:
        parent = rng.choice(connected)
        zones[parent].links.append(zone_id)
        zones[zone_id].links.append(parent)
        connected.append(zone_id)
    # Extra links (never to the gated zone — it stays hub-only).
    for _ in range(rng.randint(0, 2)):
        a, b = rng.sample(gather_ids, 2)
        if b not in zones[a].links:
            zones[a].links.append(b)
            zones[b].links.append(a)
    for z in zones.values():
        z.links = sorted(set(z.links))

    # --- items ------------------------------------------------------------
    items: dict[str, tuple[str, int]] = {}
    for res in used_resources | used_secondary:
        display, floor, _skill = RAW_RESOURCES[res]
        items[res] = (display, floor)
    items[gated_item] = (gated_display, gated_floor)

    # --- crafting chain, derived from the generated resources -------------
    # Primary and secondary nodes both feed the chain, so a world's
    # refined good or key item can come from a second node.
    raw_items = [z.node_item for z in zones.values()
                 if z.node_item and z.rule != "needs_key"]
    raw_items += [z.node2_item for z in zones.values()
                  if z.node2_item and z.rule != "needs_key"]
    mine_raws = [r for r in raw_items if RAW_RESOURCES[r][2] == "mine"]
    refined_base = rng.choice(mine_raws if mine_raws else raw_items)
    refined_item = f"{refined_base}_bar"
    # Crafted goods are priced well above their inputs: refining, keying,
    # and artifact-crafting are the high-value end of the economy, so
    # trading them moves real money — and pays a real 5% toll.
    refined_floor = RAW_RESOURCES[refined_base][1] * 4 + 5
    items[refined_item] = (f"{RAW_RESOURCES[refined_base][0]} Bar", refined_floor)

    key_item = rng.choice(sorted(KEY_ITEMS))
    key_inputs = rng.sample(raw_items, 2)
    key_floor = sum(RAW_RESOURCES[r][1] for r in key_inputs) + 25
    items[key_item] = (KEY_ITEMS[key_item], key_floor)

    elite_item = f"{gated_item}_artifact"
    elite_floor = gated_floor * 3 + refined_floor * 2 + 50
    items[elite_item] = (f"{gated_display} Artifact", elite_floor)

    recipes = {
        refined_item: ({refined_base: 2}, refined_item, 1, "craft", 25),
        key_item: ({key_inputs[0]: 2, key_inputs[1]: 1}, key_item, 1, "craft", 25),
        elite_item: ({gated_item: 2, refined_item: 1}, elite_item, 1, "craft", 40),
    }

    # --- rendezvous pair: two distinct non-gated raws ---------------------
    give, get = rng.sample(raw_items, 2)
    meet_zone = next(z.zone_id for z in zones.values()
                     if z.node_item == give or z.node2_item == give)

    return WorldSpec(
        seed=seed, zones=zones, items=items, recipes=recipes,
        gated_zone="gated", key_item=key_item, commons_zone=commons_zone,
        refined_item=refined_item, gated_item=gated_item,
        elite_item=elite_item, rendezvous_pair=(give, get),
        meet_zone=meet_zone,
    )


def next_hop(links: dict, rules: dict, has_key: bool,
             from_zone: str, to_zone: str) -> str:
    """One step along the shortest path from_zone -> to_zone (BFS over
    zone links). The gated zone is blocked unless the traveler has the key
    item. Returns from_zone itself when already there or unreachable.

    `links` maps zone id -> adjacent zone ids; `rules` maps zone id ->
    rule (None, "commons", or "needs_key"). Agents call this with the
    links/rules from their observable view; the engine never hands them
    the true spec.
    """
    if from_zone == to_zone:
        return from_zone

    def blocked(zone: str) -> bool:
        return rules.get(zone) == "needs_key" and not has_key

    prev: dict[str, str | None] = {from_zone: None}
    queue = [from_zone]
    while queue:
        cur = queue.pop(0)
        for nxt in links.get(cur, []):
            if nxt not in prev and not blocked(nxt):
                prev[nxt] = cur
                if nxt == to_zone:
                    step = nxt
                    while prev[step] != from_zone:
                        step = prev[step]  # type: ignore[assignment]
                    return step
                queue.append(nxt)
    return from_zone
