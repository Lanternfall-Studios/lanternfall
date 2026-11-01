# Lanternfall — an MMO for agents

## New: opt-in local human + agent co-play

```sh
python3 -B run.py --serve --interactive --seed 42 --port 8765
```

Open the printed **chris session** / **jon session** URL at `127.0.0.1:8765`.
All seven roster slots share the same tick/action path. Chat to `@steward` or
`@engineer` queues an observable, inert work order; it never executes chat as code.
[Co-play, SDK, worker handoff, optional signed ledger bridge, and limitations](docs/coplay-and-ledger-bridge.md).

A small, working, **original** persistent world where software agents quest,
level skills, gather, craft, and trade in a player-driven market. Built in a
day as a prototype for Jon and Chris's $5k Muse-to-Muse business experiment,
inspired by Max Bittker's "RuneScape Bench: MMOs for Agents" talk and by
Chris's QuestBench project — but it is its own game, with its own setting,
names, and rules. No RuneScape names, assets, art, music, or lore anywhere.

## Run it

Python 3.10+ standard library only. No dependencies, no accounts, no network.

```bash
cd quest-game
python3 run.py                        # 300-tick demo season, prints leaderboard
python3 run.py --ticks 600 --verbose  # longer season, progress every 50 ticks
python3 run.py --seed 7               # repeatable: same seed, same season
python3 run.py --seed 7 --world       # inspect the generated world, no season
python3 run.py --serve                # + live dashboard at http://127.0.0.1:8765/
python3 tests/smoke_test.py           # invariants + behavior checks
```

A finished season writes `runs/<timestamp>/summary.json`, `events.jsonl`
(every server event, one JSON object per line), and `world.json` (final
snapshot). A sample run is already in `runs/demo-300/`.

## The game loop

One tick = every agent acts once. Agents only ever see an *observable view*
(their inventory, zone stocks, market books, recent chat, proposals addressed
to them, their quests) — never the true world state. Any agent, scripted or
LLM-driven, plugs in by implementing `decide(view) -> action`.

**World.** The world is *dynamically generated from a seed* (see below):
a town/market hub, 3–6 gather zones across biomes (forest, quarry, marsh,
dunes, caverns, coast, highlands, tundra, jungle, volcanic…), one gated
late-game zone, and one shared commons zone. Most gather zones hold two
resource nodes (a primary and a secondary, each with its own stock and
regrow rate). Resource nodes regrow over time.

**Skills.** Forage, Mine, Craft, Trade. XP levels up skills; higher levels
gather more. Scoring includes a RuneBench-style metric: **peak XP rate**,
the best XP gained in any 10-tick window — it rewards discovering better
strategies, not just grinding longer.

**Crafting chain.** Derived from the generated resources, always the same
shape: a raw refines into a bar; two raws craft the key item that unlocks
the gated zone; the gated resource plus a bar crafts an elite artifact.
Seed 42's chain, for example: crystal → crystal bar, timber + mushroom →
ember compass, shard + crystal bar → shard artifact. Chain ingredients
can come from secondary nodes too — seed 42's crystal is Underhollow's
second node.

**Market.** A real order book, not a shop. Agents post bids and asks;
matching trades at the resting price with a 5% toll that sinks coins into
the town vault. A town merchant posts floor bids for basics so agents always
have an outlet. Prices move with supply and demand, not with a script.
Bankrolls are sized so the toll actually bites (agents start at 250 sparks,
the market-maker at 1500; crafted goods are priced well above their inputs
— an elite artifact lists around 200+ sparks, so one trade pays ~10 sparks
of toll). The live dashboard (`run.py --serve`) draws a price-history chart
per traded item from the completed-trade feed.

**Quests.** Solo deliveries to the Steward in Haven (timber, lanterns, iron
bars, shards), re-dealt when completed. Plus the co-op centerpiece:

**Rendezvous.** Two agents must agree in chat on a meeting zone, both show
up there, and complete a direct trade. Chat promises are tracked
(kept vs broken), but only the server-verified trade pays out — talk is
data, not reward.

**Supply chain.** The second co-op scenario: one agent gathers the
refined good's raw and trades it to a partner, who crafts the refined
good. The quest pays both only when both server facts hold — the
qualifying trade AND the craft. Either half alone pays nothing.

**Commons.** One zone per world is shared and regrows slowly. In the seed
42 demo season it gets drained to zero — the tragedy of the commons,
now visible, not just in the data: every tick samples the commons stock
into `commons_history` (on the world snapshot and in `summary.json` as
`commons`, with `min_stock` and the first `depleted_tick`), and the live
dashboard shows a shared-stock meter — stock/cap bar, healthy/low/
depleted status, first-depletion tick, and a stock-vs-tick chart. Steve's
QC note on the harness chunk (commons stock ends at 0 after 60 ticks)
is answered by the meter: depletion is now a first-class, timestamped
observation instead of a number you have to go looking for.

**Scams.** Direct trades are two-step: propose exact terms, then accept.
The proposal carries the *actual* terms; what anyone claimed in chat may
differ. An agent that accepts without checking can be scammed; an agent
that checks and rejects records a scam avoided. Both are scored.

## Dynamic generation: the product

Jon's direction (Oct 30): **the game is an open world that is dynamically
generated — and that is the company's product.** The world is not a map
someone drew; it is output. One integer seed produces a whole playable
world, and a new seed produces a genuinely different one: different zone
layouts, different resource mixes, different crafting paths — while the
rules, the market, the quests, and the agent interface stay exactly the
same. That is what makes Lanternfall a benchmark instead of a demo: an
agent can be scored across many unseen worlds, not one memorized map.

**What the seed controls** (drawn in a fixed order from one `Random`,
so generation is fully deterministic):
- zone count (hub + 3–6 gather zones + 1 gated zone) and the zone graph
  — a connected spanning tree over the hub, plus 0–2 extra links; the
  gated zone connects only to the hub, so the key item is the only way in
- each zone's biome, display name, primary resource, stock cap, and
  regrow rate — plus a secondary node (a second resource with its own
  stock and regrow rate) when the biome has a fresh resource left
- which zone is the commons (big cap, slow regrow)
- the crafting chain: which raw refines, which two raws make the key item,
  and the gated resource behind it
- the rendezvous trade pair and meeting zone, and the supply-chain pair
  (gatherer raw → crafter refined good)

**What stays fixed** (the rules, not the content):
- one town/market hub, one gated late-game zone, one commons zone
- the market (order book, 5% toll), delivery quests, rendezvous, scams,
  peak-XP-rate scoring, and the `decide(view) -> action` interface
- the shape of the crafting chain: raw → refined, two raws → key,
  gated raw + refined → elite
- the demo cast's policies — they read items, recipes, zone links, and
  the key item from the view, so the same six agents play sensibly on
  every seed

**Try it:**

```bash
python3 run.py --seed 42 --world    # Haven + caverns/highlands/forest, compass-gated vault
python3 run.py --seed 7 --world     # bigger map, lantern-gated citadel, tundra rendezvous
python3 run.py --seed 123 --world   # small map, torch-gated crater, salt-bar supply chain
python3 run.py --seed 7             # play a full season on that world
```

Same seed, same world, same season — byte for byte (the smoke test
asserts it). Different seeds, different worlds: the test suite also runs
full seasons on seeds 7 and 123 and checks that quests, a rendezvous,
market trades, and a scam all still occur there.

## The demo cast (all scripted)

| Agent | Strategy |
| --- | --- |
| Grinder | Gather → haul → sell/craft loop. The baseline. Pursues delivery quests: never floor-sells a quest item, and walks past intermediate zones to reach a quest item's node. |
| Merchant | Market maker: buys low asks, sells higher. |
| Coop-A / Coop-B | Rendezvous partners: gather, agree a meeting in chat, travel, trade. They check actual trade terms. |
| Chain-A / Chain-B | Supply-chain partners: A gathers the refined raw and trades 5 to B; B crafts the refined good. Both share the reward, paid only on both server facts. |
| Naive | Home-zone gatherer; accepts any trade proposal without checking. The scam victim. |
| Sly | Announces the refined good, actually offers 1 unit of the commons resource for 10. |

Demo season (300 ticks, seed 42): Grinder finishes 2 quests, the
cooperators complete their rendezvous, the chain partners complete their
supply chain, 7 market trades clear (38 sparks tolled), Naive gets
scammed 2 times, and the commons ends depleted.
See `runs/demo-300/summary.json` for the full table (an older sample —
re-run for current numbers).

## What came from where

**From the video** ("RuneScape Bench: MMOs for Agents", Max Bittker):
persistent shared world as an agent testbed; long horizons and compounding
consequences; agents driving the game through code/APIs rather than clicks;
a player-driven market with faucets and sinks; trading, trust, and scams as
first-class phenomena; benchmarking by peak XP rate; multi-agent
collusion/betrayal as an experimental frame.

**From QuestBench** (Camaraterie/questbench, public repo, read-only):
rewards only from server state, never from claims; the rendezvous problem
(can two agents agree, show up, and trade?) as the first cooperative test;
commons dilemmas; hidden-role trade / scam resistance; scripted partner
agents; controlled, repeatable (seeded) runs; a watch-it-live studio view.

**Invented here:** the entire setting (Lanternfall, zones, items, sparks,
key items, gated resources), the seeded generation system, the zone graph
and crafting-chain derivation, the order-book market design, the quest
list, the agent policies, the scoring table, and all code. Nothing was
copied from QuestBench's or rs-sdk's code.

## Gaps, stated plainly

* **No full video transcript was retrievable.** The talk was studied from
  its published description and chapter list, plus the earlier research
  brief. If the talk's detailed findings (e.g. exact benchmark progress
  numbers, collusion experiment results) matter, they still need the video
  itself or Steve/Chris.
* **The questbench `scrolls-idea` branch yielded no additional detail.**
  Its public page shows the same README as main, and branch-specific
  documents were not retrievable by search. This prototype therefore follows
  the README's direction, not any branch-specific design.
* **No LLM agents yet.** The cast is scripted policies behind the same
  `decide(view)` interface a model agent would use. Wiring a real model in
  is the obvious next step, not a done one.
* **Simplifications:** one action per agent per tick; movement is one zone
  per tick; combat, PvP, and guilds do not exist; the dashboard is
  read-only (no operator controls).

## Next steps (concrete)

1. **Plug in a real model agent.** Implement `decide(view)` on top of an
   LLM call (with a token budget per tick) and run it against the scripted
   cast on the same seeded seasons — that turns Lanternfall from a demo
   into the benchmark it is modeled on. The harness should score one
   model agent across many seeds, with a reference reasoning-trace agent
   and no wired-up API key.
2. ~~**A commons scenario with a visible shared-stock meter**~~ — done:
   per-tick `commons_history`, `depleted_tick` / `min_stock` in the
   summary, and a live meter + stock chart on the dashboard.
2a. ~~**Identity + model provenance on harness trajectories**~~ —
   done (Chris's constraint, Oct 31): every trace entry is stamped
   with the server-assigned agent identity (persistent id, tenant,
   persona) and the model provenance in force (provider, model name,
   exact version, system-prompt hash, tool set); a model swap records
   a `model_change` chapter, never a silent swap. The reference agent's
   provenance plainly says provider `none`, model `scripted-reference`.
3. ~~**Make the economy bite**~~ — done: bigger bankrolls, repriced
   crafted goods, toll actually collects, price-history chart live.
4. ~~**Grow the generator + second co-op scenario**~~ — done: 3 new
   biomes, multi-node zones, split supply chain with server-verified
   shared reward.

## Layout

```
run.py                      CLI: run a season, optionally serve the dashboard
                            (--world prints the generated world for a seed)
lanternfall/
  generate.py               seeded procedural generation: zones, resources,
                            crafting chain, rendezvous pair; next_hop pathing
  world.py                  zones, nodes, inventories, order-book market
  quests.py                 deliveries, rendezvous, direct trades, scam checks
  agents.py                 Agent interface + six scripted demo policies
  engine.py                 tick loop, views, scoring, run artifacts
  dashboard.py              stdlib HTTP live view (/, /state.json)
  demo.py                   the six-agent demo season setup
  model_agent.py            model-driver interface, action parser,
                            reasoning-trace reference agent, multi-seed scoring
tests/
  smoke_test.py             conservation invariants + behavior checks +
                            determinism and cross-seed world checks
  model_agent_test.py       parser + driver-failure + harness scoring checks
runs/demo-300/              sample season artifacts (seed 42)
```

## Model-agent harness

An external model plugs in behind `ModelDriver.complete(view_json)`.
Replies are parsed by `parse_action` into validated action tuples;
anything unusable becomes `("idle",)` and is counted, never fatal.
`ReasoningAgent` wraps a scripted policy and emits a reasoning trace
per action, so the harness is verifiable with no model attached.
Every trace entry carries identity + model provenance (see
`AgentIdentity` / `ModelProvenance` in `model_agent.py`); a model swap
goes through `ModelAgent.change_model`, which records a `model_change`
chapter in the trace. Score a candidate across seeds (it takes the
Grinder slot):

```
python3 run.py --score 42,7,123 --score-ticks 300
```

No model driver is wired up and no credentials are needed; a driver
is injected by the caller when one exists.
