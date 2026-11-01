"""Standard-library bridge and co-play tests; synthetic data only."""
import hashlib
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lanternfall.demo import build_demo


class BridgeTests(unittest.TestCase):
    def test_tick_hook_captures_market_and_model_chapters(self):
        from lanternfall.ledger_bridge import checkpoint
        from lanternfall.model_agent import ModelAgent, ModelDriver, ModelProvenance
        class Idle(ModelDriver):
            def complete(self, view_json):
                return "ACTION: idle"
        class Sink:
            def __init__(self):
                self.payloads = []
            def emit(self, payload):
                self.payloads.append(payload)
        engine = build_demo(42)
        sink = Sink()
        agent = ModelAgent("Model", Idle())
        engine.agents.append(agent)
        engine.world.add_player("Model")
        agent.change_model(ModelProvenance("test", "idle", "2"))
        engine.ledger_bridge = sink
        engine.step()
        self.assertEqual(sink.payloads[-1], checkpoint(engine))
        self.assertIn("model_change", [p["kind"] for p in sink.payloads])
        engine.run(299)
        self.assertIn("market_trade", [p["kind"] for p in sink.payloads])

    def test_bridge_rejects_partial_config_and_mismatched_acknowledgement(self):
        import tempfile
        from unittest.mock import patch
        from lanternfall.ledger_bridge import LedgerBridge, checkpoint
        audit = Path(__file__).resolve().parents[2] / ".steward-audit"
        audit.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=audit) as directory:
            key = Path(directory) / "synthetic.key"
            key.write_bytes(b"synthetic fixture, not a private key")
            key.chmod(0o600)
            with self.assertRaises(ValueError):
                LedgerBridge(key=key, actor="test")
            bridge = LedgerBridge(Path(__file__), key, "test")
            class Response:
                stdout = b'{"ok":true,"result":{"receipt_sha256":"wrong","status":"anchored"}}'
            with patch("lanternfall.ledger_bridge.subprocess.run", return_value=Response()):
                with self.assertRaises(RuntimeError):
                    bridge.emit(checkpoint(build_demo(42)))

    def test_written_artifact_bytes_match_receipt_hashes(self):
        import tempfile
        from lanternfall.ledger_bridge import checkpoint
        engine = build_demo(42)
        result = engine.run(3)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            result.write(directory)
            payload = checkpoint(engine, "season")
            for filename, field in (("events.jsonl", "events_jsonl_sha256"),
                                    ("world.json", "world_snapshot_sha256")):
                self.assertEqual(hashlib.sha256((Path(directory) / filename).read_bytes()).hexdigest(), payload[field])

    def test_model_response_ids_and_trajectory_hash_are_usable_by_engine(self):
        from lanternfall.model_agent import ModelAgent, ModelDriver, ModelProvenance, parse_action
        action, ok, _ = parse_action("ACTION: respond 12 yes", {})
        self.assertTrue(ok)
        self.assertEqual(action, ("respond", 12, True))
        class Idle(ModelDriver):
            def complete(self, view_json):
                return "ACTION: idle"
        agent = ModelAgent("M", Idle())
        before = agent.trajectory_hash()
        agent.change_model(ModelProvenance("test", "idle", "2"))
        self.assertNotEqual(before, agent.trajectory_hash())
        from lanternfall.ledger_bridge import digest
        self.assertEqual(agent.trajectory_hash(), digest(agent.trace))

    def test_headless_receipt_is_deterministic_and_does_not_change_season(self):
        from lanternfall.ledger_bridge import LedgerBridge, canonical, checkpoint
        first, second = build_demo(42), build_demo(42)
        first.run(10)
        second.run(10)
        bridge = LedgerBridge()
        payload = checkpoint(first, "season")
        self.assertEqual(payload, checkpoint(second, "season"))
        self.assertEqual(payload["events_jsonl_sha256"], hashlib.sha256(
            b"".join(canonical(e) + b"\n" for e in first.world.event_log)).hexdigest())
        self.assertEqual(bridge.emit(payload)["status"], "headless")
        self.assertEqual(first.world.snapshot(), second.world.snapshot())
        json.loads(canonical(payload))


class CoplayTests(unittest.TestCase):
    def test_persistence_failure_pauses_room_and_rejects_further_actions(self):
        from unittest.mock import patch
        from lanternfall.coplay import build_coplay
        room = build_coplay(42)
        room.submit("chris", ["say", "@steward audit"])
        with patch.object(room.work_orders, "_publish", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(OSError):
                room.step()
        self.assertIsNotNone(room.error)
        with self.assertRaises(ValueError):
            room.submit("jon", ["gather"])
        with self.assertRaises(ValueError):
            room.step()

    def test_roster_trajectories_have_honest_server_assigned_provenance(self):
        from lanternfall.coplay import build_coplay
        from lanternfall.sdk import submit_work_order
        room = build_coplay(42)
        room.submit("chris", submit_work_order("engineer", "audit"))
        room.step()
        for name in ("chris", "engineer", "steve"):
            entry = room.engine.by_name[name].trace[0]
            self.assertEqual(entry["agent_id"], name)
            self.assertEqual(entry["model"]["provider"], "none")
            self.assertIn("scripted" if name in {"engineer", "steward"} else "queued", entry["model"]["model_name"])

    def test_unknown_market_items_are_rejected_for_humans_and_agents(self):
        from lanternfall.coplay import build_coplay
        room = build_coplay(42)
        for actor in ("chris", "steve"):
            room.submit(actor, ["bid", "<img src=x onerror=alert(1)>", 1, 1])
        room.step()
        self.assertEqual(room.engine.world.bids, [])
        self.assertEqual(sum(e["kind"] == "invalid" for e in room.engine.world.event_log), 2)

    def test_worker_refuses_a_tampered_persisted_request(self):
        import tempfile
        from lanternfall.coplay import build_coplay
        from lanternfall.work_orders import write_completion
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            base = Path(directory)
            room = build_coplay(42, outbox=base / "outbox")
            room.submit("jon", ["say", "@engineer audit"])
            room.step()
            room.step()
            order = next(iter(room.work_orders.orders.values()))
            path = base / "outbox" / (order["id"] + ".json")
            modified = json.loads(path.read_text())
            modified["directive"] = "different unapproved request"
            path.write_text(json.dumps(modified))
            artifact = base / "artifact"
            artifact.write_bytes(b"synthetic")
            report = base / "report.json"
            report.write_text('{"passed":true,"exit_code":0}')
            with self.assertRaises(ValueError):
                write_completion(base / "outbox", order["id"], artifact, report)

    def test_external_worker_completion_is_authenticated_and_observed_on_turn(self):
        import tempfile
        from lanternfall.coplay import build_coplay
        from lanternfall.work_orders import write_completion
        from lanternfall.sdk import submit_work_order
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            base = Path(directory)
            room = build_coplay(42, outbox=base / "outbox")
            room.submit("chris", submit_work_order("steward", "audit commons"))
            room.step()
            room.step()
            order = next(iter(room.work_orders.orders.values()))
            artifact, evidence = base / "artifact.txt", base / "verification.json"
            artifact.write_text("synthetic patch")
            evidence.write_text('{"exit_code":0,"passed":true}')
            completion = write_completion(base / "outbox", order["id"], artifact, evidence)
            completion_path = base / "outbox" / (order["id"] + ".completion.json")
            forged = dict(completion, signature="0" * 64)
            completion_path.write_text(json.dumps(forged))
            room.step()
            self.assertEqual(order["status"], "in_progress")
            completion_path.write_text(json.dumps(completion))
            room.step()
            self.assertEqual(order["status"], "verified")
            self.assertTrue(any(e["kind"] == "work_order" and e.get("status") == "verified"
                                for e in room.engine.world.event_log))

    def test_live_event_stream_is_canonical_and_includes_work_status(self):
        import tempfile
        from lanternfall.coplay import build_coplay
        from lanternfall.ledger_bridge import canonical
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "events.jsonl"
            room = build_coplay(42, events_path=path)
            try:
                room.submit("jon", ["say", "@steward audit commons"])
                room.step()
                expected = b"".join(canonical(e) + b"\n" for e in room.engine.world.event_log)
                self.assertEqual(path.read_bytes(), expected)
            finally:
                room.stop()

    def test_work_order_ack_movement_durable_queue_and_explicit_completion(self):
        import tempfile
        from lanternfall.coplay import build_coplay
        from lanternfall.sdk import submit_work_order
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            room = build_coplay(42, outbox=Path(directory) / "outbox")
            worker = room.engine.world.players["engineer"]
            worker.zone = room.engine.world.spec.zones[room.engine.world.spec.hub].links[0]
            room.submit("chris", submit_work_order("engineer", "scaffold quest helper"))
            room.step()
            order = next(iter(room.work_orders.orders.values()))
            self.assertEqual(order["status"], "queued")
            room.step()
            self.assertEqual(worker.zone, room.engine.world.spec.hub)
            room.step()
            self.assertEqual(order["status"], "in_progress")
            self.assertTrue((Path(directory) / "outbox" / (order["id"] + ".json")).exists())
            self.assertTrue(any("queued" in m["text"] for m in room.engine.world.chat_log))
            self.assertFalse(any(e.get("status") == "verified" for e in room.engine.world.event_log))
            room.complete_work(order["id"], b"synthetic artifact", b"synthetic passing test report")
            self.assertEqual(order["status"], "verified")
            self.assertEqual(len(order["completion_sha256"]), 64)
            self.assertEqual(room.engine.world.event_log[-1]["kind"], "work_order")
            with self.assertRaises(ValueError):
                room.complete_work(order["id"], b"again", b"again")

    def test_http_auth_origin_bounds_and_queued_actions(self):
        import urllib.request
        import urllib.error
        from lanternfall.coplay import build_coplay
        from lanternfall.dashboard import serve_in_background
        room = build_coplay(42)
        server = serve_in_background(room.engine, 0, room=room)
        base = f"http://127.0.0.1:{server.server_port}"
        def request(path, body=None, token=None, origin=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            if origin:
                headers["Origin"] = origin
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                error.close()
                raise
        try:
            with self.assertRaises(urllib.error.HTTPError) as exc:
                request("/api/action", {"actor": "chris", "action": ["gather"]})
            self.assertEqual(exc.exception.code, 403)
            token = server.actor_tokens["chris"]
            with self.assertRaises(urllib.error.HTTPError) as exc:
                request("/api/view?actor=chris", token="nonascii-é")
            self.assertEqual(exc.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as exc:
                request("/api/chat", {"actor": "chris", "text": "hi"}, token, "http://evil.invalid")
            self.assertEqual(exc.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError):
                request("/api/chat", {"actor": "jon", "text": "hi"}, token)
            self.assertEqual(request("/api/chat", {"actor": "chris", "text": "@steward audit commons"}, token)["status"], "queued")
            self.assertEqual(room.engine.world.tick, 0)
            self.assertEqual(request("/api/view?actor=chris", token=token)["tick"], 0)
            room.step()
            state = request("/state.json")
            self.assertEqual(state["tick"], 1)
            self.assertEqual(state["work_orders"][0]["status"], "queued")
            with self.assertRaises(urllib.error.HTTPError):
                request("/api/action", {"actor": "chris", "action": ["say", "x" * 5000]}, token)
        finally:
            server.shutdown()
            server.server_close()

    def test_humans_and_agents_share_one_tick_action_path_and_readonly_views(self):
        from lanternfall.coplay import build_coplay
        room = build_coplay(42)
        self.assertEqual(set(room.engine.by_name),
                         {"chris", "jon", "steward", "engineer", "steve", "tom", "thales"})
        self.assertEqual(room.engine.world.tick, 0)
        destination = room.engine.world.spec.zones[room.engine.world.spec.hub].links[0]
        for name in ("chris", "steward"):
            room.submit(name, ["move", destination])
        before = room.engine.by_name["chris"].last_chat_tick
        room.view("chris")
        self.assertEqual(before, room.engine.by_name["chris"].last_chat_tick)
        room.step()
        for name in ("chris", "steward"):
            self.assertEqual(room.engine.world.players[name].zone, destination)
        self.assertEqual(room.engine.world.tick, 1)
        with self.assertRaises(ValueError):
            room.submit("chris", ["bid", "ember", -1, True])


class SdkTests(unittest.TestCase):
    def test_helpers_expand_to_legal_inspectable_primitives(self):
        from lanternfall.coplay import build_coplay
        from lanternfall.sdk import (path_to_resource, propose_safe_trade,
                                    submit_work_order, audit_commons_health, validate_action)
        room = build_coplay(42)
        item = room.engine.world.spec.raw_items()[0]
        plan = path_to_resource(room.view("chris"), item)
        for action in plan:
            self.assertEqual(validate_action(action), action)
            room.submit("chris", action)
            room.step()
        view = room.view("chris")
        self.assertGreater(view["me"]["inventory"].get(item, 0), 0)
        offer = propose_safe_trade(view, "jon", item, 1, item, 1)
        self.assertEqual(offer, ("propose", "jon", item, 1, item, 1, f"1x {item}"))
        self.assertEqual(audit_commons_health(view)["stock"],
                         room.engine.world.nodes[view["commons_zone"]].stock)
        self.assertEqual(submit_work_order("steward", "audit"), ("say", "@steward audit"))
        with self.assertRaises(ValueError):
            propose_safe_trade(view, "jon", item, 1_000_000, item, 1)
        for action in ({}, [[], "x"], ["gather", None], ["say", "x" * 501],
                       ["bid", item, 1, 0], ["bid", item, True, 1], ["respond", 1, "yes"]):
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate_action(action)

    def test_queues_are_bounded_and_outbox_symlinks_are_rejected(self):
        import tempfile
        from lanternfall.coplay import build_coplay
        from lanternfall.work_orders import WorkOrders
        room = build_coplay(42)
        for _ in range(16):
            room.submit("chris", ["idle"])
        with self.assertRaises(ValueError):
            room.submit("chris", ["idle"])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            base = Path(directory)
            (base / "target").mkdir()
            (base / "link").symlink_to(base / "target", target_is_directory=True)
            with self.assertRaises(ValueError):
                WorkOrders(base / "link" / "outbox")
        queue = WorkOrders()
        queue.submit("chris", "steward", "audit", 0, 0, 42)
        with self.assertRaises(ValueError):
            queue.submit("chris", "engineer", "spam", 0, 1, 42)
        self.assertEqual(len(queue.orders), 1)


if __name__ == "__main__":
    unittest.main()
