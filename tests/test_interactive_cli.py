"""Launch the actual CLI, use an actor session, and stop only our child process."""
import json
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]


class InteractiveCliTests(unittest.TestCase):
    def test_cli_serves_advancing_world_and_writes_artifacts_on_interrupt(self):
        help_text = subprocess.check_output([sys.executable, "-B", "run.py", "--help"], cwd=ROOT).decode()
        self.assertIn("--interactive", help_text)
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            base = Path(directory)
            process = subprocess.Popen(
                [sys.executable, "-u", "-B", "run.py", "--serve", "--interactive", "--port", "0",
                 "--out", str(base / "artifacts"), "--work-outbox", str(base / "outbox")],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                # Byte reads avoid buffered readline hiding already-read readiness lines.
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    output = b""
                    deadline = time.monotonic() + 10
                    while b"Co-play ready\n" not in output and time.monotonic() < deadline:
                        if selector.select(0.1):
                            import os
                            chunk = os.read(process.stdout.fileno(), 4096)
                            if not chunk:
                                break
                            output += chunk
                self.assertIn(b"Co-play ready", output)
                chris_line = next(line for line in output.decode().splitlines() if line.startswith("chris session: "))
                link = chris_line.split(": ", 1)[1]
                url = urlsplit(link)
                origin = f"{url.scheme}://{url.netloc}"
                token = parse_qs(url.fragment)["token"][0]
                request = urllib.request.Request(origin + "/api/chat",
                    data=json.dumps({"actor": "chris", "text": "@engineer scaffold quest helper"}).encode(),
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 202)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with urllib.request.urlopen(origin + "/state.json", timeout=3) as response:
                        state = json.load(response)
                    if state["work_orders"] and state["work_orders"][0]["status"] == "in_progress":
                        break
                    time.sleep(0.1)
                self.assertGreaterEqual(state["tick"], 2)
                self.assertEqual(state["work_orders"][0]["status"], "in_progress")
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertTrue((base / "artifacts" / "events.jsonl").exists())
                self.assertTrue((base / "artifacts" / "summary.json").exists())
                self.assertTrue(list((base / "outbox").glob("*.json")))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=8)
