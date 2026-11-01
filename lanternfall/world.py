"""World state for Lanternfall: zones, resource nodes, inventories, and the
player-driven market (order book).

The world itself is procedurally generated (see generate.py): one seed
fixes the zone graph, biomes, resource nodes, regrowth rates, and the
crafting chain. Everything in this module reads that spec — nothing about
a particular world's content is hardcoded here.

Design notes
------------
* Every reward in the game comes from server state (inventories, positions,
  completed trades), never from what an agent *says*. Chat claims are just
  data — an agent that believes a chat claim without checking the actual
  trade terms can be scammed. This mirrors the "rewards only from server
  state" principle of the QuestBench benchmark this game was inspired by.
* The market is a real order book, not a shop with fixed prices. A small
  town merchant posts floor bids so agents always have an outlet, and a 5%
  toll on completed trades acts as a coin sink.
* One zone per world is a shared commons: its stock regrows slowly, so
  over-harvesting by everyone collapses it for everyone.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .generate import (
    GATED_RESOURCES,
    HUB_ID,
    RAW_RESOURCES,
    WorldSpec,
    generate_world,
)

# Bankrolls are deliberately large: with a 5% toll that rounds down to
# whole sparks, tiny trades toll to zero and the vault never fills. At
# these stakes a crafted-good trade costs real sparks in toll, so the
# market toll is a genuine economic force instead of a rounding error.
STARTING_SPARK = 250


def gather_skill(item: str) -> str:
    if item in RAW_RESOURCES:
        return RAW_RESOURCES[item][2]
    return GATED_RESOURCES[item][2]


@dataclass
class Order:
    order_id: int
    owner: str
    side: str  # "bid" or "ask"
    item: str
    price: int
    qty: int
    tick: int


@dataclass
class Player:
    name: str
    zone: str = HUB_ID
    sparks: int = STARTING_SPARK
    inventory: dict = field(default_factory=dict)
    xp: dict = field(default_factory=lambda: {"forage": 0, "mine": 0, "craft": 0, "trade": 0})
    quests_done: int = 0
    trades_done: int = 0
    promises_made: int = 0
    promises_kept: int = 0
    scams_suffered: int = 0
    scams_avoided: int = 0
    xp_history: list = field(default_factory=list)  # (tick, total_xp) snapshots

    def level(self, skill: str) -> int:
        return 1 + self.xp.get(skill, 0) // 100

    def total_xp(self) -> int:
        return sum(self.xp.values())

    def total_level(self) -> int:
        return sum(self.level(s) for s in self.xp)

    def add(self, item: str, qty: int) -> None:
        # Negative adds used to mint items through take(): a negative
        # take subtracts a negative, i.e. adds. Never accept one here.
        if qty < 0:
            raise ValueError("cannot add a negative quantity")
        if qty == 0:
            return
        self.inventory[item] = self.inventory.get(item, 0) + qty

    def has(self, item: str, qty: int = 1) -> bool:
        # A non-positive quantity is not something a player can "have":
        # without this, has(item, -3) is True on an empty inventory and
        # every gate built on has() (ask, propose, respond) passes it.
        if qty <= 0:
            return False
        return self.inventory.get(item, 0) >= qty

    def take(self, item: str, qty: int) -> bool:
        if qty <= 0:
            return False
        if not self.has(item, qty):
            return False
        self.inventory[item] -= qty
        if self.inventory[item] <= 0:
            del self.inventory[item]
        return True


@dataclass
class Node:
    zone: str
    item: str
    stock: int
    cap: int
    regrow_every: int  # ticks between +1 stock
    skill: str


class World:
    """The shared, persistent world. One tick = every agent acts once.

    The world content comes from a seeded spec (generate.generate_world);
    the season's randomness (re-dealt quests) uses a separate Random with
    the same seed, so a seed fully determines a season.
    """

    def __init__(self, seed: int = 42):
        self.spec: WorldSpec = generate_world(seed)
        self.rng = random.Random(seed)
        self.tick = 0
        self.players: dict[str, Player] = {}
        self.nodes: dict[str, Node] = {}  # zone -> primary node
        self.secondary_nodes: dict[str, Node] = {}  # zone -> second node
        for zone in self.spec.zones.values():
            if zone.node_item is None:
                continue
            self.nodes[zone.zone_id] = Node(
                zone.zone_id, zone.node_item, zone.node_cap, zone.node_cap,
                zone.regrow_every, gather_skill(zone.node_item),
            )
            if zone.node2_item is not None:
                self.secondary_nodes[zone.zone_id] = Node(
                    zone.zone_id, zone.node2_item, zone.node2_cap,
                    zone.node2_cap, zone.node2_regrow,
                    gather_skill(zone.node2_item),
                )
        self.bids: list[Order] = []
        self.asks: list[Order] = []
        self.next_order_id = 1
        self.town_vault = 0  # coin sink: market tolls accumulate here
        # Commons scenario trace: one {tick, stock, cap} sample per tick,
        # appended by the engine loop. The dashboard draws the shared-stock
        # meter and stock chart from this; the summary derives the
        # depletion tick and minimum stock from it.
        self.commons_history: list[dict] = []
        self.chat_log: list[dict] = []
        self.event_log: list[dict] = []
        self.market_history: list[dict] = []  # completed trades
        # Town merchant floor bids: always buys non-gated basics, never sells.
        self.merchant_floor = {
            item: floor for item, (_display, floor) in self.spec.items.items()
            if item in self.spec.raw_items()
        }

    # ------------------------------------------------------------ events ---
    def log(self, kind: str, **data) -> None:
        entry = {"tick": self.tick, "kind": kind, **data}
        self.event_log.append(entry)

    # ----------------------------------------------------------- players ---
    def add_player(self, name: str) -> Player:
        p = Player(name=name)
        self.players[name] = p
        self.log("join", player=name, zone=HUB_ID)
        return p

    # ------------------------------------------------------------ zones ---
    def move(self, player: Player, dest: str) -> tuple[bool, str]:
        if dest not in self.spec.zones:
            return False, f"unknown zone {dest}"
        if dest not in self.spec.zones[player.zone].links and dest != player.zone:
            return False, f"{dest} is not adjacent to {player.zone}"
        rule = self.spec.zones[dest].rule
        if rule == "needs_key" and not player.has(self.spec.key_item):
            return False, f"{self.spec.zones[dest].name} requires a {self.spec.key_item}"
        player.zone = dest
        self.log("move", player=player.name, zone=dest)
        return True, "moved"

    # ----------------------------------------------------------- gather ---
    def gather(self, player: Player, item: str | None = None) -> tuple[bool, str]:
        """Gather from the zone's node. With `item`, gather from the node
        holding that item (primary or secondary); without it, the primary
        node — the default for agents that don't care which node they use."""
        if item is not None:
            node = self.nodes.get(player.zone)
            if node is None or node.item != item:
                node = self.secondary_nodes.get(player.zone)
            if node is None or node.item != item:
                return False, f"no {item} node in {player.zone}"
        else:
            node = self.nodes.get(player.zone)
            if node is None:
                return False, f"nothing to gather in {player.zone}"
        if node.stock <= 0:
            return False, f"{node.item} node in {player.zone} is depleted"
        lvl = player.level(node.skill)
        qty = 2 + (1 if lvl >= 3 else 0)
        qty = min(qty, node.stock)
        node.stock -= qty
        player.add(node.item, qty)
        xp = 10
        player.xp[node.skill] += xp
        self.log(
            "gather",
            player=player.name,
            zone=player.zone,
            item=node.item,
            qty=qty,
            stock_left=node.stock,
        )
        return True, f"gathered {qty} {node.item}"

    # ------------------------------------------------------------ craft ---
    def craft(self, player: Player, recipe: str) -> tuple[bool, str]:
        if recipe not in self.spec.recipes:
            return False, f"unknown recipe {recipe}"
        inputs, output, out_qty, skill, xp = self.spec.recipes[recipe]
        if not all(player.has(i, q) for i, q in inputs.items()):
            return False, "missing ingredients"
        for i, q in inputs.items():
            player.take(i, q)
        player.add(output, out_qty)
        player.xp[skill] += xp
        self.log("craft", player=player.name, recipe=recipe, output=output)
        return True, f"crafted {output}"

    # ------------------------------------------------------------ market ---
    def post_order(self, player: Player, side: str, item: str, price: int, qty: int) -> Order:
        # The book must never hold a non-positive quantity or a negative
        # price: either corrupts matching (min() goes negative, negative
        # cost pays the buyer) and, for asks, mints via take().
        if side not in ("bid", "ask"):
            raise ValueError(f"unknown order side {side!r}")
        if qty <= 0:
            raise ValueError(f"order quantity must be positive, got {qty}")
        if price < 0:
            raise ValueError(f"order price must not be negative, got {price}")
        order = Order(self.next_order_id, player.name, side, item, price, qty, self.tick)
        self.next_order_id += 1
        if side == "bid":
            self.bids.append(order)
        else:
            if not player.take(item, qty):
                raise ValueError(f"{player.name} cannot ask {qty}x {item} they do not own")
            self.asks.append(order)
        self.log("order", player=player.name, side=side, item=item, price=price, qty=qty)
        self._match(item)
        return order

    def _match(self, item: str) -> None:
        """Match bids against asks for one item. Trade at the resting price."""
        while True:
            bids = sorted(
                [o for o in self.bids if o.item == item],
                key=lambda o: (-o.price, o.tick),
            )
            asks = sorted(
                [o for o in self.asks if o.item == item],
                key=lambda o: (o.price, o.tick),
            )
            if not bids or not asks or bids[0].price < asks[0].price:
                return
            bid, ask = bids[0], asks[0]
            price = ask.price  # resting ask sets the price
            qty = min(bid.qty, ask.qty)
            buyer = self.players[bid.owner]
            seller = self.players[ask.owner]
            cost = price * qty
            if buyer.sparks < cost:
                # buyer cannot pay: drop the bid, keep the ask
                self.bids.remove(bid)
                self.log("bid_dropped_unfunded", player=buyer.name, item=item)
                continue
            toll = cost * 5 // 100
            buyer.sparks -= cost
            seller.sparks += cost - toll
            self.town_vault += toll
            buyer.add(item, qty)
            buyer.xp["trade"] += 15
            seller.xp["trade"] += 15
            buyer.trades_done += 1
            seller.trades_done += 1
            bid.qty -= qty
            ask.qty -= qty
            if bid.qty <= 0:
                self.bids.remove(bid)
            if ask.qty <= 0:
                self.asks.remove(ask)
                # escrowed remainder: ask qty already taken at posting, so
                # nothing to return for filled asks; partial fills keep the
                # remainder escrowed in the book.
            self.market_history.append(
                {"tick": self.tick, "item": item, "price": price, "qty": qty,
                 "buyer": buyer.name, "seller": seller.name, "toll": toll}
            )
            self.log("trade", buyer=buyer.name, seller=seller.name, item=item,
                     price=price, qty=qty, toll=toll)

    def merchant_floor_trade(self, player: Player, item: str) -> tuple[bool, str]:
        """Town merchant buys basics at a fixed floor price (coin faucet)."""
        if item not in self.merchant_floor:
            return False, "merchant does not buy that"
        qty = player.inventory.get(item, 0)
        if qty <= 0:
            return False, f"no {item} to sell"
        price = self.merchant_floor[item]
        player.take(item, qty)
        player.sparks += price * qty
        player.xp["trade"] += 5
        player.trades_done += 1
        self.log("merchant_sale", player=player.name, item=item, price=price, qty=qty)
        return True, f"sold {qty} {item} to the merchant for {price * qty} sparks"

    def cancel_order(self, player: Player, order_id: int) -> tuple[bool, str]:
        """Cancel your own order; escrowed ask items return to inventory."""
        for book in (self.bids, self.asks):
            for order in book:
                if order.order_id == order_id and order.owner == player.name:
                    book.remove(order)
                    if order.side == "ask":
                        player.add(order.item, order.qty)
                    self.log("cancel", player=player.name, order_id=order_id)
                    return True, "cancelled"
        return False, "order not found"

    # -------------------------------------------------------------- chat ---
    def say(self, player: Player, text: str) -> None:
        self.chat_log.append({"tick": self.tick, "from": player.name, "text": text})
        self.log("chat", player=player.name, text=text)

    def inbox(self, name: str, since_tick: int) -> list[dict]:
        return [m for m in self.chat_log if m["tick"] >= since_tick and m["from"] != name]

    # -------------------------------------------------------------- tick ---
    def regrow(self) -> None:
        for node in list(self.nodes.values()) + list(self.secondary_nodes.values()):
            if self.tick % node.regrow_every == 0 and node.stock < node.cap:
                node.stock += 1

    def snapshot(self) -> dict:
        return {
            "tick": self.tick,
            "players": {
                name: {
                    "zone": p.zone,
                    "sparks": p.sparks,
                    "inventory": dict(p.inventory),
                    "xp": dict(p.xp),
                    "total_level": p.total_level(),
                    "quests_done": p.quests_done,
                    "trades_done": p.trades_done,
                }
                for name, p in self.players.items()
            },
            "zones": {
                zid: {"name": z.name, "biome": z.biome, "links": list(z.links),
                      "rule": z.rule}
                for zid, z in self.spec.zones.items()
            },
            "nodes": {
                zone: {"item": n.item, "stock": n.stock, "cap": n.cap,
                       "secondary": (
                           {"item": s.item, "stock": s.stock, "cap": s.cap}
                           if (s := self.secondary_nodes.get(zone)) else None)}
                for zone, n in self.nodes.items()
            },
            "bids": [
                {"owner": o.owner, "item": o.item, "price": o.price, "qty": o.qty}
                for o in self.bids
            ],
            "asks": [
                {"owner": o.owner, "item": o.item, "price": o.price, "qty": o.qty}
                for o in self.asks
            ],
            "town_vault": self.town_vault,
            "commons": {
                "zone": self.spec.commons_zone,
                "name": self.spec.zones[self.spec.commons_zone].name,
                "item": self.nodes[self.spec.commons_zone].item,
                "stock": self.nodes[self.spec.commons_zone].stock,
                "cap": self.nodes[self.spec.commons_zone].cap,
            },
            # Shared-stock meter feed: stock per tick, most recent last.
            "commons_history": self.commons_history[-1000:],
            # Completed trades, most recent last — the dashboard draws a
            # price-history chart from these (capped so state stays small).
            "market_history": self.market_history[-200:],
            "key_item": self.spec.key_item,
            "gated_zone": self.spec.gated_zone,
            "commons_zone": self.spec.commons_zone,
            "recent_events": self.event_log[-30:],
        }
