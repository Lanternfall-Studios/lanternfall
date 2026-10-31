"""Scripted demo agents for Lanternfall.

Every agent implements one method: decide(view) -> action tuple. The view
is a plain dict of *observable* state — the agent never sees the true
world, only what the server exposes (its own inventory, zone stocks and
links, market books, recent chat, proposals addressed to it, its quests,
and the world's item/recipe tables). A real LLM agent could implement the
same interface later; the engine does not care what is behind it.

Nothing here is hardcoded to a particular generated world: agents read
item prices, recipes, zone links, and the key item from the view, and
find a resource's zone by looking at which node holds it. The same
policies play sensibly on any seed.

Action tuples:
    ("move", zone) | ("gather",) | ("craft", recipe)
    ("bid", item, price, qty) | ("ask", item, price, qty)
    ("cancel_stale", max_age) | ("merchant_sell", item)
    ("say", text) | ("propose", target, give, gqty, want, wqty, announced)
    ("respond", proposal_id, accept_bool) | ("idle",)
"""

from __future__ import annotations

from .generate import next_hop


class Agent:
    def __init__(self, name: str):
        self.name = name
        self.memory: dict = {}
        self.last_chat_tick = -1

    def decide(self, view: dict) -> tuple:
        raise NotImplementedError

    # helpers ------------------------------------------------------------
    def inv(self, view: dict, item: str) -> int:
        return view["me"]["inventory"].get(item, 0)

    def total_items(self, view: dict) -> int:
        return sum(view["me"]["inventory"].values())

    def floor(self, view: dict, item: str) -> int:
        return view["items"][item]["floor"]

    def has_key(self, view: dict) -> bool:
        return self.inv(view, view["key_item"]) >= 1

    def hop(self, view: dict, dest: str) -> str:
        links = {z: info["links"] for z, info in view["zones"].items()}
        rules = {z: info["rule"] for z, info in view["zones"].items()}
        return next_hop(links, rules, self.has_key(view),
                        view["me"]["zone"], dest)

    def gather_zones(self, view: dict) -> list[str]:
        """Zones with resource nodes the agent may enter (gated zone only
        with the key item)."""
        zones = []
        for zone, node in view["nodes"].items():
            if view["zones"][zone]["rule"] == "needs_key" and not self.has_key(view):
                continue
            zones.append(zone)
        return zones

    def zone_of(self, view: dict, item: str) -> str:
        for zone, node in view["nodes"].items():
            if node["item"] == item:
                return zone
        for zone, node in view["nodes"].items():
            sec = node.get("secondary")
            if sec and sec["item"] == item:
                return zone
        return view["hub"]

    def node_in_zone(self, view: dict, zone: str, item: str) -> dict | None:
        """The node holding `item` in `zone` (primary or secondary)."""
        node = view["nodes"].get(zone)
        if node is None:
            return None
        if node["item"] == item:
            return node
        sec = node.get("secondary")
        if sec and sec["item"] == item:
            return sec
        return None

    def gated_item(self, view: dict) -> str:
        return view["nodes"][view["gated_zone"]]["item"]

    def refined_base(self, view: dict) -> str:
        """The raw that refines into the world's refined good."""
        spec = view["recipes"][view["refined_item"]]
        return next(iter(spec["inputs"]))

    def best_stock_zone(self, view: dict) -> str:
        zones = self.gather_zones(view)
        return max(zones, key=lambda z: view["nodes"][z]["stock"])

    def node_depleted(self, view: dict) -> bool:
        """True when the agent's current zone has no gatherable stock on
        either node (multi-node zones stay useful while one node lives)."""
        node = view["nodes"].get(view["me"]["zone"])
        if node is None:
            return True
        if node["stock"] > 0:
            return False
        sec = node.get("secondary")
        return sec is None or sec["stock"] <= 0

    def gather_here(self, view: dict, want: str | None = None) -> tuple:
        """The gather action for the current zone: the wanted item's node
        when it lives here and has stock, else the primary node, else the
        secondary node. Falls back to a bare gather (which the engine
        resolves to the primary) when nothing applies."""
        node = view["nodes"].get(view["me"]["zone"])
        if node is None:
            return ("gather",)
        if want is not None:
            target = self.node_in_zone(view, view["me"]["zone"], want)
            if target is not None and target["stock"] > 0:
                return ("gather", want)
        if node["stock"] > 0:
            return ("gather",)
        sec = node.get("secondary")
        if sec and sec["stock"] > 0:
            return ("gather", sec["item"])
        return ("gather",)


class Grinder(Agent):
    """Gather -> haul to Haven -> sell/craft -> repeat. The baseline.

    It also pursues its delivery quests: quest-target items are never
    floor-sold (selling the 8 glowcap you hold is how a 10-glowcap quest
    never completes), and when it leaves Haven it heads for a quest
    item's zone when one is outstanding."""

    def quest_need(self, view: dict) -> str | None:
        """An item from an open delivery quest that this agent does not
        yet hold enough of, or None when no delivery quest is outstanding.
        Open delivery quests at decide time are always unfulfilled — the
        engine completes fulfilled ones at the start of the turn."""
        for q in view["quests"]:
            if q["kind"] != "delivery":
                continue
            for item, qty in q.get("deliver", {}).items():
                if self.inv(view, item) < qty:
                    return item
        return None

    def needs_bar_ingredient(self, view: dict) -> bool:
        """True while gated loot waits on a bar: holds gated stock, holds
        no refined bar yet, and still lacks enough of the bar's raw
        ingredient for one bar. Once the ingredient is in hand, the
        detour is over — time to go home and craft."""
        if self.inv(view, self.gated_item(view)) < 1:
            return False
        if self.inv(view, view["refined_item"]) >= 1:
            return False
        base = self.refined_base(view)
        need = view["recipes"][view["refined_item"]]["inputs"][base]
        return self.inv(view, base) < need

    def decide(self, view: dict) -> tuple:
        me = view["me"]
        zone = me["zone"]
        if zone == view["hub"]:
            # Complete deliveries first, then craft, then sell, then leave.
            if view["delivery_ready"]:
                return ("idle",)  # engine completes deliveries in Haven
            # Elite artifacts first: they need a gated resource plus a bar,
            # and the generic loop below would otherwise keep consuming
            # bars into more keys/bars before the elite window ever opens.
            elite = next((r for r in view["recipes"]
                          if r.endswith("_artifact")), None)
            if elite:
                spec = view["recipes"][elite]
                if all(self.inv(view, i) >= q
                       for i, q in spec["inputs"].items()):
                    return ("craft", elite)
            for recipe, spec in view["recipes"].items():
                if all(self.inv(view, i) >= q for i, q in spec["inputs"].items()):
                    return ("craft", recipe)
            # Cancel asks that have sat too long, then re-sell to the floor.
            for ask in view["asks"]:
                if ask["owner"] == self.name and view["tick"] - ask["tick"] > 25:
                    return ("cancel_stale", 25)
            # Post surplus on the player market (Merchant buys these):
            # raw stock, the refined good, and the elite artifact — but
            # never the key item, since selling that would lock the agent
            # out of the gated zone.
            for item in view["raw_items"] + [view["refined_item"]]:
                qty = self.inv(view, item)
                if qty >= 10:
                    return ("ask", item, self.floor(view, item) + 2, qty)
            # Elite artifacts are rare and high-value: sell even one.
            elite = next((i for i in view["items"]
                          if i.endswith("_artifact")), None)
            if elite and self.inv(view, elite) >= 1:
                return ("ask", elite, self.floor(view, elite) + 2,
                        self.inv(view, elite))
            quest_targets = {i for q in view["quests"]
                             if q["kind"] == "delivery"
                             for i in q.get("deliver", {})}
            for item in view["raw_items"]:
                if self.inv(view, item) >= 1:
                    # Never floor-sell a delivery-quest target: selling
                    # the 8 glowcap you hold is how a 10-glowcap quest
                    # never completes.
                    if item in quest_targets:
                        continue
                    # Don't sell the bar ingredient while gated loot waits
                    # for a bar: a single salt sold to the floor is a bar
                    # (and then an elite) that never gets crafted.
                    if (item == self.refined_base(view)
                            and self.inv(view, self.gated_item(view)) >= 1
                            and self.inv(view, view["refined_item"]) < 1):
                        continue
                    return ("merchant_sell", item)
            # Head out: if we hold the key AND a refined bar (the other
            # elite ingredient), the gated zone is worth the trip — its
            # resource is the most valuable raw in the world. Gated trips
            # without a bar in hand trap the agent: it keeps topping up
            # gated stock and never goes to gather bar ingredients, so
            # elite artifacts never get crafted.
            if self.has_key(view) and self.inv(view, view["refined_item"]) >= 1:
                gated = view["gated_zone"]
                if view["nodes"][gated]["stock"] > 0:
                    return ("move", self.hop(view, gated))
            # Carrying gated loot but no bar to pair it with? Go fetch the
            # bar ingredient next, not the fullest node — otherwise the
            # gated haul sits unusable and the elite never gets crafted.
            if self.needs_bar_ingredient(view):
                return ("move", self.hop(view,
                                        self.zone_of(view, self.refined_base(view))))
            # An outstanding delivery quest beats the best-stock heuristic:
            # head for the quest item's zone even when its node is nearly
            # dry — stock regrows, and the quest only completes by going.
            # (A hop that goes nowhere — e.g. the gated zone without the
            # key — is not a move: fall through to the stock heuristic.)
            need = self.quest_need(view)
            if need is not None:
                step = self.hop(view, self.zone_of(view, need))
                if step != zone:
                    return ("move", step)
            # Otherwise take one step toward the reachable zone whose node
            # has most stock (the graph is no longer hub-to-everywhere).
            return ("move", self.hop(view, self.best_stock_zone(view)))
        # Out in the world: gather until loaded, then head home. If the
        # local node is dry, move on instead of gathering air forever.
        # En route first: when headed for a quest item's zone (or the bar
        # ingredient's zone), keep walking through intermediate zones
        # instead of stopping to gather filler there — the old bug was
        # one hop toward a two-hop zone, then gathering in the middle
        # forever and never reaching the quest node.
        if self.total_items(view) < 24:
            need = self.quest_need(view)
            if need is not None:
                target = self.zone_of(view, need)
                if target != view["hub"] and target != zone:
                    step = self.hop(view, target)
                    if step != zone:
                        return ("move", step)
            if self.needs_bar_ingredient(view):
                base_zone = self.zone_of(view, self.refined_base(view))
                if base_zone != zone:
                    step = self.hop(view, base_zone)
                    if step != zone:
                        return ("move", step)
        if self.total_items(view) >= 24:
            # Exception: a gated haul without its bar partner is not a
            # finished load — detour to the bar ingredient's zone and keep
            # gathering there instead of ping-ponging home loaded and
            # unable to unload.
            if self.needs_bar_ingredient(view):
                base_zone = self.zone_of(view, self.refined_base(view))
                if me["zone"] != base_zone:
                    return ("move", self.hop(view, base_zone))
                # Wait at the node for regrow instead of commuting home:
                # the bar ingredient often lives in the contested commons,
                # and a commuter loses every regrow race to whoever camps
                # the zone. Nodes always regrow, so the wait is bounded.
                if self.node_depleted(view):
                    return ("idle",)
                return self.gather_here(view, self.refined_base(view))
            return ("move", self.hop(view, view["hub"]))
        if self.node_depleted(view):
            target = self.best_stock_zone(view)
            if view["nodes"][target]["stock"] <= 0:
                return ("move", self.hop(view, view["hub"]))
            return ("move", self.hop(view, target))
        # Gather the quest item's node when it lives here; otherwise the
        # primary (or secondary) node as usual.
        return self.gather_here(view, self.quest_need(view))


class Merchant(Agent):
    """Pure market maker: buys low from asks, sells higher, cancels stale
    orders. Starts with extra sparks (set by the demo runner). Buys the
    whole affordable quantity of a cheap ask, not a single unit — bulk
    trades are what make the 5% market toll actually bite."""

    def decide(self, view: dict) -> tuple:
        me = view["me"]
        # Sell what we hold at a markup — but never stack a second open
        # ask for an item we already list. (Stacking duplicate asks used
        # to wedge the merchant: it posted a new ask every tick and never
        # got back to buying, freezing the market after its first bulk buy.)
        listed = {o["item"] for o in view["asks"] if o["owner"] == self.name}
        for item in view["items"]:
            qty = self.inv(view, item)
            if qty >= 5 and item not in listed:
                return ("ask", item, self.floor(view, item) + 4, qty)
        # Buy cheap asks, in bulk while the bankroll allows.
        for ask in view["asks"]:
            if ask["owner"] == self.name:
                continue
            if ask["price"] <= self.floor(view, ask["item"]) + 2 \
                    and me["sparks"] > ask["price"]:
                qty = min(ask["qty"], me["sparks"] // ask["price"])
                return ("bid", ask["item"], ask["price"], qty)
        return ("cancel_stale", 25)


class Cooperator(Agent):
    """Rendezvous partner. State machine: gather both trade goods, propose
    a meeting in chat, travel there, trade. Checks actual trade terms
    before accepting — it cannot be scammed by a mismatched announcement.

    `give`/`get` are this agent's own quest terms (the partner's are
    mirrored); `meet_zone` is the zone they agree to meet in.
    """

    def __init__(self, name: str, partner: str, give: str, get: str, zone: str):
        super().__init__(name)
        self.partner = partner
        self.give = give
        self.get = get
        self.meet_zone = zone
        self.phase = "gather"
        self.proposed_tick = -1

    def decide(self, view: dict) -> tuple:
        me = view["me"]
        quest = next((q for q in view["quests"] if q["kind"] == "rendezvous"), None)
        if quest is None:
            return ("idle",)
        # Answer incoming proposals first. Accept only a partner proposal
        # whose terms match the quest AND whose announcement matches the
        # actual terms; reject everything else (a mismatched announcement
        # is a scam attempt, and rejecting it counts as avoiding one).
        for prop in view["proposals_to_me"]:
            if prop["proposer"] == self.partner:
                ok = {prop["give_item"], prop["want_item"]} == {self.give, self.get}
                ok = ok and prop["give_qty"] == 5 and prop["want_qty"] == 5
                ok = ok and prop["announced"] == f"{prop['give_qty']}x {prop['give_item']}"
                return ("respond", prop["proposal_id"], ok)
            return ("respond", prop["proposal_id"], False)
        if self.phase == "gather":
            have_give = self.inv(view, self.give) >= 5
            have_get = self.inv(view, self.get) >= 5
            if have_give and have_get:
                self.phase = "propose"
            else:
                need = self.give if not have_give else self.get
                # Buy it in Haven if the market offers it at a fair price.
                if me["zone"] == view["hub"]:
                    for ask in view["asks"]:
                        if (ask["item"] == need
                                and ask["price"] <= self.floor(view, need) + 4
                                and me["sparks"] >= ask["price"] * 5):
                            return ("bid", need, ask["price"], 5)
                zone = self.zone_of(view, need)
                if me["zone"] != zone:
                    return ("move", self.hop(view, zone))
                if self.node_depleted(view):
                    return ("idle",)  # wait for the node to regrow
                return self.gather_here(view, need)
        if self.phase == "propose":
            # Only one partner proposes (alphabetical first) to avoid crosstalk.
            if self.name < self.partner:
                self.phase = "travel"
                self.proposed_tick = view["tick"]
                return ("say", f"MEET {self.meet_zone} — {self.partner}, let's trade there")
            self.phase = "wait"
            return ("idle",)
        if self.phase == "wait":
            # Wait for the partner's proposal, then travel.
            for msg in view["chat"]:
                if msg["from"] == self.partner and msg["text"].startswith("MEET"):
                    self.phase = "travel"
                    return ("move", self.hop(view, self.meet_zone))
            return ("idle",)
        if self.phase == "travel":
            if me["zone"] != self.meet_zone:
                return ("move", self.hop(view, self.meet_zone))
            self.phase = "trade"
            return ("idle",)
        if self.phase == "trade":
            # Partner should be here too; offer the trade (honest terms).
            if self.name < self.partner:
                return ("propose", self.partner, self.give, 5, self.get, 5,
                        f"5x {self.give}")
            return ("idle",)
        return ("idle",)


class ChainGatherer(Agent):
    """Supply-chain gatherer: gathers the refined raw, trades 5 of it to
    the crafter for 1 unit of the crafter's home good. No meeting needed
    — direct trades are addressed, not co-located — but the quest still
    pays only on server facts: the trade AND the crafter's craft. After
    the chain completes, falls back to hauling its raw to Haven."""

    def __init__(self, name: str, partner: str, raw: str, want_item: str):
        super().__init__(name)
        self.partner = partner
        self.raw = raw
        self.want_item = want_item

    def decide(self, view: dict) -> tuple:
        me = view["me"]
        quest = next((q for q in view["quests"] if q["kind"] == "supply_chain"), None)
        if quest is None:
            return self._haul(view)
        if view["my_proposals"]:
            return ("idle",)  # wait for the crafter to answer
        if self.inv(view, self.raw) >= 5:
            return ("propose", self.partner, self.raw, 5, self.want_item, 1,
                    f"5x {self.raw}")
        zone = self.zone_of(view, self.raw)
        if me["zone"] != zone:
            return ("move", self.hop(view, zone))
        if self.node_depleted(view):
            return ("idle",)  # wait for the node to regrow
        return self.gather_here(view, self.raw)

    def _haul(self, view: dict) -> tuple:
        """Post-quest fallback: gather the raw zone, floor-sell in Haven."""
        me = view["me"]
        if me["zone"] == view["hub"]:
            for item in view["raw_items"]:
                if self.inv(view, item) >= 1:
                    return ("merchant_sell", item)
            return ("move", self.hop(view, self.zone_of(view, self.raw)))
        if self.total_items(view) >= 20:
            return ("move", self.hop(view, view["hub"]))
        zone = self.zone_of(view, self.raw)
        if me["zone"] != zone:
            return ("move", self.hop(view, zone))
        if self.node_depleted(view):
            return ("idle",)
        return self.gather_here(view, self.raw)


class ChainCrafter(Agent):
    """Supply-chain crafter: gathers its home good (the gatherer's
    payment), accepts the gatherer's raw trade only when the terms check
    out and the payment is in hand, then crafts the refined good. An
    open-but-unacceptable proposal does not stall it — it keeps gathering
    while the proposal expires and the gatherer re-proposes."""

    def __init__(self, name: str, partner: str, raw: str, refined: str,
                 home_item: str):
        super().__init__(name)
        self.partner = partner
        self.raw = raw
        self.refined = refined
        self.home_item = home_item

    def decide(self, view: dict) -> tuple:
        me = view["me"]
        quest = next((q for q in view["quests"] if q["kind"] == "supply_chain"), None)
        if quest is None:
            return self._haul(view)
        for prop in view["proposals_to_me"]:
            if prop["proposer"] == self.partner:
                ok = (prop["give_item"] == self.raw and prop["give_qty"] >= 5
                      and self.inv(view, prop["want_item"]) >= prop["want_qty"])
                if ok:
                    return ("respond", prop["proposal_id"], True)
        # Craft the refined good as soon as the raw is in hand — the
        # craft half of the quest, verified server-side.
        spec = view["recipes"][self.refined]
        if all(self.inv(view, i) >= q for i, q in spec["inputs"].items()):
            return ("craft", self.refined)
        if me["zone"] == view["hub"]:
            for item in view["raw_items"]:
                if self.inv(view, item) >= 10:
                    return ("merchant_sell", item)
            return ("move", self.hop(view, self.zone_of(view, self.home_item)))
        if self.total_items(view) >= 20:
            return ("move", self.hop(view, view["hub"]))
        zone = self.zone_of(view, self.home_item)
        if me["zone"] != zone:
            return ("move", self.hop(view, zone))
        if self.node_depleted(view):
            return ("idle",)
        return self.gather_here(view, self.home_item)

    def _haul(self, view: dict) -> tuple:
        """Post-quest fallback: gather the home zone, floor-sell in Haven."""
        me = view["me"]
        if me["zone"] == view["hub"]:
            for item in view["raw_items"]:
                if self.inv(view, item) >= 1:
                    return ("merchant_sell", item)
            return ("move", self.hop(view, self.zone_of(view, self.home_item)))
        if self.total_items(view) >= 20:
            return ("move", self.hop(view, view["hub"]))
        zone = self.zone_of(view, self.home_item)
        if me["zone"] != zone:
            return ("move", self.hop(view, zone))
        if self.node_depleted(view):
            return ("idle",)
        return self.gather_here(view, self.home_item)


class NaiveTrader(Agent):
    """Accepts any direct-trade proposal without checking the terms.
    The scam victim of the demo. Gathers in one home zone (the commons),
    like the old grove gatherer — so it accumulates a pile of one resource,
    which is exactly what the scammer preys on."""

    def __init__(self, name: str, home_zone: str = ""):
        super().__init__(name)
        self.home_zone = home_zone

    def decide(self, view: dict) -> tuple:
        for prop in view["proposals_to_me"]:
            return ("respond", prop["proposal_id"], True)
        # Otherwise just gather in the home zone and sell like a grinder.
        me = view["me"]
        if me["zone"] == view["hub"]:
            for item in view["raw_items"]:
                if self.inv(view, item) >= 10:
                    return ("merchant_sell", item)
            return ("move", self.hop(view, self.home_zone))
        if self.total_items(view) >= 20:
            return ("move", self.hop(view, view["hub"]))
        if me["zone"] != self.home_zone:
            return ("move", self.hop(view, self.home_zone))
        if self.node_depleted(view):
            return ("idle",)  # wait for the home node to regrow
        return ("gather",)


class Scammer(Agent):
    """The Shadows-track demo: announces a valuable crafted good, actually
    offers a pittance of a common raw in exchange for a pile of it.

    The scam: announce "5x <refined good>" but actually offer 1 unit of the
    commons resource in exchange for 10 of it. An agent that checks actual
    terms rejects it; an agent that accepts on the announcement loses 9.
    Proposals are spaced 25 ticks apart so the victim can still progress
    between scams. The scammer gathers in the commons zone, where its
    victim (a home-zone gatherer) also spends its time.
    """

    def __init__(self, name: str, victim: str, currency: str, announced: str,
                 home_zone: str):
        super().__init__(name)
        self.victim = victim
        self.currency = currency
        self.announced = announced
        self.home_zone = home_zone
        self.announced_once = False
        self.last_propose_tick = -100

    def decide(self, view: dict) -> tuple:
        me = view["me"]
        # Never pile up proposals: wait for the open one to resolve.
        if view["my_proposals"]:
            return ("idle",)
        if self.inv(view, self.currency) >= 1 and not self.announced_once:
            self.announced_once = True
            return ("say", f"{self.victim}, selling {self.announced} cheap — interested?")
        if (self.inv(view, self.currency) >= 1
                and view["tick"] - self.last_propose_tick >= 25):
            self.last_propose_tick = view["tick"]
            return ("propose", self.victim, self.currency, 1,
                    self.currency, 10, self.announced)
        if me["zone"] != self.home_zone:
            return ("move", self.hop(view, self.home_zone))
        if self.node_depleted(view):
            return ("idle",)  # commons regrows slowly; wait for stock
        return ("gather",)


DEMO_AGENTS = [
    ("Grinder", Grinder),
    ("Merchant", Merchant),
    ("Coop-A", Cooperator),
    ("Coop-B", Cooperator),
    ("Naive", NaiveTrader),
    ("Sly", Scammer),
    ("Chain-A", ChainGatherer),
    ("Chain-B", ChainCrafter),
]
