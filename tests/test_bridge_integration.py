"""Opt-in real Ed25519/fsync/replay integration against a fresh sibling core.

LANTERNFALL_CORE_SOURCE=../lanternfall-core python3 -B -m unittest discover -s tests -p 'test_bridge*.py' -v
No original keys, daemon, or live ledger is used. Only stdlib + core's OpenSSL.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(os.environ.get("LANTERNFALL_CORE_SOURCE"), "optional sibling core integration")
class AnchoredTests(unittest.TestCase):
    def test_enrolled_actor_receipts_are_signed_durable_and_replayable(self):
        from lanternfall.demo import build_demo
        from lanternfall.ledger_bridge import LedgerBridge, checkpoint
        core = Path(os.environ["LANTERNFALL_CORE_SOURCE"]).resolve(strict=True)
        spec = importlib.util.spec_from_file_location("core_dev", core / "tools/dev.py")
        dev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dev)
        audit = Path(__file__).resolve().parents[2] / ".steward-audit"
        audit.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bridge-e2e-", dir=audit) as directory:
            base = Path(directory)
            world = dev.create_world(base / "cradle")
            wrapper = world / "pasture/verifier.sh"
            def cli(*args):
                return json.loads(subprocess.check_output([str(wrapper), *args], timeout=20))
            process = subprocess.Popen([str(wrapper), "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for _ in range(100):
                    if (world / "pasture/.verifier.sock").exists():
                        break
                    if process.poll() is not None:
                        self.fail("disposable verifier exited")
                    time.sleep(0.05)
                key, public = base / "actor.key", base / "actor.pub"
                subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)], check=True)
                key.chmod(0o600)
                subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)], check=True)
                registered = cli("request", "--key", str(world / "progenitor.key"),
                                 "--action", "agent.register", "--params",
                                 json.dumps({"public_key": public.read_text()}))
                actor = registered["result"]["actor"]
                bridge = LedgerBridge(wrapper, key, actor)
                engine = build_demo(42)
                engine.run(10)
                payload = checkpoint(engine, "season")
                anchored = bridge.emit(payload)
                self.assertEqual(anchored["status"], "anchored")
                self.assertEqual(anchored["verifier_response"]["result"]["receipt_sha256"], anchored["receipt_sha256"])
                verified = cli("verify")
                self.assertEqual(verified["events"], 3)
                self.assertEqual(verified["head"], anchored["verifier_response"]["head"])
                before = (world / "pasture/.world_ledger.log").read_bytes()
                with self.assertRaises(subprocess.CalledProcessError):
                    bridge.emit(payload)
                self.assertEqual(before, (world / "pasture/.world_ledger.log").read_bytes())
                # Old signature no longer covers a changed checkpoint.
                lines = before.splitlines()
                record = json.loads(lines[-1])
                record["event"]["request"]["body"]["params"]["commons_stock"] += 1
                lines[-1] = json.dumps(record).encode()
                (world / "pasture/.world_ledger.log").write_bytes(b"\n".join(lines) + b"\n")
                with self.assertRaises(subprocess.CalledProcessError):
                    cli("verify")
                (world / "pasture/.world_ledger.log").write_bytes(before)
            finally:
                # Only the child started by this test is stopped; no process matching.
                process.terminate()
                try:
                    process.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
