#!/usr/bin/env python3
"""Run a Lanternfall season.

Examples:
    python3 run.py                  # 300-tick demo season, prints leaderboard
    python3 run.py --ticks 600 --verbose
    python3 run.py --serve          # also serve a live dashboard on :8765
    python3 run.py --seed 7         # repeatable season (same seed, same result)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lanternfall.demo import build_demo
from lanternfall.dashboard import serve_in_background
from lanternfall.engine import default_run_dir, SeasonResult
from lanternfall.ledger_bridge import LedgerBridge, checkpoint


def interactive(args, bridge):
    from pathlib import Path
    import tempfile
    from lanternfall.coplay import build_coplay
    boundary = Path(__file__).resolve().parent.parent
    staging = boundary / ".steward-audit" / "work_orders"
    staging.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix="session-", dir=staging))
    outbox = Path(args.work_outbox).absolute() if args.work_outbox else session / "outbox"
    out = Path(args.out).absolute() if args.out else session / "artifacts"
    # User-selected artifact paths are still constrained to this workspace.
    relative = out.relative_to(boundary)
    current = boundary
    for part in relative.parts:
        if part == "..":
            raise ValueError("unsafe artifact directory")
        current = current / part
        if current.is_symlink():
            raise ValueError("artifact symlink forbidden")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    room = build_coplay(args.seed, outbox=outbox, events_path=out / "events.jsonl")
    room.engine.ledger_bridge = bridge
    server = serve_in_background(room.engine, args.port, room=room)
    origin = f"http://127.0.0.1:{server.server_port}"
    print(f"Interactive dashboard: {origin}/", flush=True)
    for actor in server.actor_tokens:
        print(f"{actor} session: {origin}/?actor={actor}#token={server.actor_tokens[actor]}", flush=True)
    print(f"Outbox: {outbox}\nArtifacts: {out}", flush=True)
    print("Co-play ready", flush=True)
    room.start()
    try:
        while not room.stopped.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        room.stop()
        server.shutdown()
        server.server_close()
        with room.lock:
            SeasonResult(room.engine).write(str(out))
    if room.error:
        print(room.error, file=sys.stderr)
        return 1
    bridge.emit(checkpoint(room.engine, "season"))
    return 0


def print_world(engine) -> None:
    """Print the generated world for a seed and exit (no season run)."""
    spec = engine.world.spec
    desc = spec.describe()
    print(f"== Lanternfall world, seed {spec.seed} ==")
    print(f"Hub: {spec.zones[spec.hub].name} · Gated zone: "
          f"{spec.zones[spec.gated_zone].name} (needs a "
          f"{spec.items[spec.key_item][0].lower()}) · Commons: "
          f"{spec.zones[spec.commons_zone].name} (slow regrow)")
    print(f"\n{'zone':<12} {'name':<20} {'biome':<10} {'resource':<14} "
          f"{'cap':>4} {'regrow':>7}  links")
    for z in desc["zones"]:
        print(f"{z['id']:<12} {z['name']:<20} {z['biome']:<10} "
              f"{str(z['node_item']):<14} {z['node_cap']:>4} "
              f"{z['regrow_every']:>7}  {', '.join(z['links'])}")
        if z["node2_item"]:
            print(f"{'':<12} {'':<20} {'':<10} "
                  f"{'2nd: ' + str(z['node2_item']):<14} {z['node2_cap']:>4} "
                  f"{z['node2_regrow']:>7}")
    print("\nCrafting chain:")
    for out, r in desc["recipes"].items():
        ins = " + ".join(f"{q}x {i}" for i, q in r["inputs"].items())
        print(f"  {ins} -> {r['qty']}x {out}")
    print(f"\nRendezvous trade: {spec.rendezvous_pair[0]} <-> "
          f"{spec.rendezvous_pair[1]} in {spec.meet_zone}")
    # refined_base is derived here the same way demo.py derives it.
    refined_base = next(iter(spec.recipes[spec.refined_item][0]))
    print(f"Supply chain: Chain-A gathers {refined_base} -> trades 5 to "
          f"Chain-B, who crafts {spec.refined_item}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Lanternfall — an MMO for agents")
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--serve", action="store_true",
                        help="serve a live dashboard while the season runs")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interactive", action="store_true", help="shared-room pulse loop; requires --serve")
    parser.add_argument("--work-outbox", help="fresh owner-only directory for inert work orders")
    parser.add_argument("--ledger-bridge", default=os.environ.get("LANTERNFALL_LEDGER_BRIDGE"),
                        metavar="VERIFIER", help="opt-in trusted Cradle verifier.sh path")
    parser.add_argument("--bridge-key", default=os.environ.get("LANTERNFALL_BRIDGE_KEY"),
                        help="external owner-only enrolled signing key; never stored in source")
    parser.add_argument("--bridge-actor", default=os.environ.get("LANTERNFALL_BRIDGE_ACTOR"),
                        help="enrolled Cradle actor id")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out", default=None, help="run artifact directory")
    parser.add_argument("--world", action="store_true",
                        help="print the generated world for the seed and exit")
    parser.add_argument("--score", default=None,
                        help="score the reference reasoning-trace agent on "
                             "comma-separated seeds, e.g. --score 42,7,123")
    parser.add_argument("--score-ticks", type=int, default=300)
    args = parser.parse_args()
    if args.interactive and (not args.serve or args.score or args.world):
        parser.error("--interactive requires --serve and cannot combine with --world/--score")
    if not 0 <= args.seed <= 2**53:
        parser.error("seed must be an integer in [0, 2**53]")
    bridge = LedgerBridge(args.ledger_bridge, args.bridge_key, args.bridge_actor)
    if args.interactive:
        return interactive(args, bridge)

    if args.score:
        from lanternfall.agents import Grinder
        from lanternfall.model_agent import ReasoningAgent, score_agent
        seeds = [int(s) for s in args.score.split(",") if s.strip()]
        report = score_agent(
            lambda name: ReasoningAgent(Grinder(name)),
            seeds, ticks=args.score_ticks)
        print(f"\n== Model-agent harness score: {args.score_ticks} ticks ==")
        print(f"{'seed':>6} {'sparks':>7} {'level':>6} {'quests':>7} "
              f"{'trades':>7} {'peakXP':>7} {'parseFail':>9} {'invalid':>8}")
        for row in report["seeds"]:
            print(f"{row['seed']:>6} {row['sparks']:>7} "
                  f"{row['total_level']:>6} {row['quests_done']:>7} "
                  f"{row['trades_done']:>7} {row['peak_xp_rate_10t']:>7} "
                  f"{row['parse_failures']:>9} {row['invalid_actions']:>8}")
        print(f"\nAggregate: {report['aggregate']}")
        return 0

    engine = build_demo(seed=args.seed)
    if args.ledger_bridge:
        engine.ledger_bridge = bridge
    if args.world:
        print_world(engine)
        return 0
    server = None
    if args.serve:
        server = serve_in_background(engine, args.port)
        print(f"Live dashboard: http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")

    result = engine.run(args.ticks, verbose=args.verbose)
    out_dir = args.out or default_run_dir()
    summary = result.write(out_dir)
    bridge.emit(checkpoint(engine, "season"))

    print(f"\n== Lanternfall season complete: {args.ticks} ticks, seed {args.seed} ==")
    print(f"Artifacts: {out_dir}/summary.json, events.jsonl, world.json")
    print(f"Market trades: {summary['market_trades']}  "
          f"Town vault (tolls): {summary['town_vault_tolls']} sparks  "
          f"Rendezvous completed: {summary['rendezvous_done']}  "
          f"Scams: {summary['scams']} (avoided: {summary['scams_avoided']})  "
          f"Commons ({summary['commons_zone']}) stock: {summary['commons_stock']}  "
          f"(min {summary['commons']['min_stock']}, "
          f"{'depleted at tick ' + str(summary['commons']['depleted_tick']) if summary['commons']['depleted_tick'] is not None else 'never depleted'})")
    print(f"\n{'agent':<10} {'sparks':>7} {'level':>6} {'quests':>7} "
          f"{'trades':>7} {'peakXP/10t':>10} {'promises':>9} {'scammed':>8}")
    for row in sorted(summary["leaderboard"], key=lambda r: -r["sparks"]):
        print(f"{row['name']:<10} {row['sparks']:>7} {row['total_level']:>6} "
              f"{row['quests_done']:>7} {row['trades_done']:>7} "
              f"{row['peak_xp_rate_10t']:>10} {row['promises']:>9} "
              f"{row['scams_suffered']:>8}")
    print(f"\nRankings: {summary['rankings']}")

    if server is not None:
        print("Dashboard still serving the final state. Press Ctrl-C to exit.")
        try:
            import time
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
