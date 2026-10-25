#!/usr/bin/env python3
"""Updates that speak: the daily PyPI read, the update line once a day per
project, upgrade notes once per machine, and `kg update --check`.

    cd knowledge-graph/server && ./venv/bin/python tests/test_update_channel.py

Uses a temp KG_STORAGE_ROOT, HOME for the CLI and notes folder; never
reaches PyPI (the fetch is passed in).
"""

import os
os.environ["KG_BUDGET_NOTICES"] = "0"
os.environ.pop("KG_UPDATE_CHECK", None)
os.environ.pop("KG_HTTP_PORT", None)
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-updates-storage-")
_STATE = tempfile.TemporaryDirectory(prefix="kg-updates-state-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name
os.environ["XDG_STATE_HOME"] = _STATE.name

from fastapi.testclient import TestClient

from core import updates
from mcp_http import chore_dispatch
from mcp_http.read_format import build_bootstrap
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager

PLUGIN = Path(__file__).resolve().parents[2]
STATE_FILE = Path(_STORAGE.name) / "updates.json"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fresh(unittest.TestCase):
    def setUp(self):
        STATE_FILE.unlink(missing_ok=True)
        notes = tempfile.TemporaryDirectory(prefix="kg-updates-notes-")
        self.addCleanup(notes.cleanup)
        self.notes = Path(notes.name)
        self._notes_dir, updates.NOTES_DIR = updates.NOTES_DIR, self.notes
        self.addCleanup(setattr, updates, "NOTES_DIR", self._notes_dir)

    def seed(self, **data):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data))


class ReleaseCheckTests(Fresh):
    def test_reads_pypi_at_most_once_a_day(self):
        calls = []
        fetch = lambda: calls.append(1) or "0.99.0"
        start = 1e9
        self.assertTrue(updates.refresh(now=start, fetch=fetch))
        self.assertFalse(updates.refresh(now=start + 3600, fetch=fetch))
        self.assertTrue(updates.refresh(now=start + 86400, fetch=fetch))
        self.assertEqual(len(calls), 2)
        self.assertEqual(updates.read_state()["latest"], "0.99.0")

    def test_a_failed_read_waits_a_day_and_keeps_the_last_answer(self):
        self.seed(latest="0.98.0", checked_at=0)
        self.assertFalse(updates.refresh(now=100000.0, fetch=lambda: None))
        self.assertEqual(updates.read_state()["latest"], "0.98.0")
        calls = []
        updates.refresh(now=100001.0, fetch=lambda: calls.append(1))
        self.assertEqual(calls, [])

    def test_the_switch_turns_the_read_off(self):
        os.environ["KG_UPDATE_CHECK"] = "0"
        self.addCleanup(os.environ.pop, "KG_UPDATE_CHECK", None)
        calls = []
        self.assertFalse(updates.refresh(now=1e9, fetch=lambda: calls.append(1)))
        self.assertEqual(calls, [])
        self.seed(latest="9.0.0", notes_seen="1.0.0")
        self.assertEqual(updates.session_notice("p", "1.0.0", today="2026-10-25"), "")


class UpdateLineTests(Fresh):
    def test_once_a_day_per_project(self):
        self.seed(latest="1.1.0", notes_seen="1.0.0")
        first = updates.session_notice("/a", "1.0.0", today="2026-10-25")
        self.assertIn("kg-memory 1.1.0 is available (this machine runs 1.0.0)", first)
        self.assertIn("kg update --check", first)
        self.assertEqual(updates.session_notice("/a", "1.0.0", today="2026-10-25"), "")
        self.assertIn("1.1.0", updates.session_notice("/b", "1.0.0", today="2026-10-25"))
        self.assertIn("1.1.0", updates.session_notice("/a", "1.0.0", today="2026-10-26"))
        # Only today's entries are kept.
        self.assertEqual(updates.read_state()["announced"], {"/a": "2026-10-26"})

    def test_a_plugin_ahead_of_the_server_is_named(self):
        self.seed(notes_seen="1.0.0")
        line = updates.session_notice("/a", "1.0.0", plugin="1.1.0", today="2026-10-25")
        self.assertIn("plugin here is 1.1.0 but the memory server runs 1.0.0", line)

    def test_nothing_when_current(self):
        self.seed(latest="1.0.0", notes_seen="1.0.0")
        self.assertEqual(updates.session_notice("/a", "1.0.0", plugin="1.0.0"), "")
        self.assertNotIn("announced", updates.read_state())


class UpgradeNotesTests(Fresh):
    def write_notes(self, *versions):
        for v in versions:
            (self.notes / f"{v}.md").write_text(f"note for {v}")

    def test_a_first_run_gets_only_the_current_note(self):
        self.write_notes("1.0.0", "1.1.0")
        text = updates.session_notice("/a", "1.1.0", today="2026-10-25")
        self.assertIn("[1.1.0]\nnote for 1.1.0", text)
        self.assertNotIn("1.0.0", text)
        self.assertEqual(updates.read_state()["notes_seen"], "1.1.0")

    def test_skipped_releases_arrive_in_order_once(self):
        self.write_notes("1.0.0", "1.1.0", "1.2.0", "1.10.0", "2.0.0")
        self.seed(notes_seen="1.0.0")
        text = updates.session_notice("/a", "1.10.0", today="2026-10-25")
        self.assertLess(text.index("[1.1.0]"), text.index("[1.2.0]"))
        self.assertLess(text.index("[1.2.0]"), text.index("[1.10.0]"))
        self.assertNotIn("[1.0.0]", text)
        self.assertNotIn("[2.0.0]", text)
        self.assertEqual(updates.session_notice("/b", "1.10.0", today="2026-10-25"), "")

    def test_a_downgrade_keeps_the_record(self):
        self.write_notes("1.0.0")
        self.seed(notes_seen="1.1.0")
        self.assertEqual(updates.session_notice("/a", "1.0.0"), "")
        self.assertEqual(updates.read_state()["notes_seen"], "1.1.0")

    def test_long_notes_are_cut_with_a_pointer(self):
        (self.notes / "1.1.0.md").write_text("line\n" * 2000)
        self.seed(notes_seen="1.0.0")
        text = updates.session_notice("/a", "1.1.0")
        self.assertLess(len(text), updates.NOTES_CHAR_CAP + 500)
        self.assertIn(str(self.notes), text)

    def test_the_shipped_notes_are_named_by_version(self):
        for path in self._notes_dir.glob("*.md"):
            self.assertTrue(all(part.isdigit() for part in path.stem.split(".")), path.name)
            self.assertLess(len(path.read_text()), updates.NOTES_CHAR_CAP, path.name)


class PreloadTests(Fresh):
    def setUp(self):
        super().setUp()
        (Path(_STORAGE.name) / "sessions.json").unlink(missing_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="kg-updates-project-", dir=Path.home() / ".cache")
        self.addCleanup(folder.cleanup)
        self.root = str(Path(folder.name).resolve())
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        self.client = TestClient(create_rest_api(self.store, self.sm, ConnectionManager(), "1.0.0"))

    def test_the_notice_opens_the_preload_and_counts_against_its_budget(self):
        graphs = {"user": {"nodes": {}, "edges": {}}, "project": {"nodes": {}, "edges": {}}}
        plain = build_bootstrap(graphs, {}, "s1", budget=10000)
        noticed = build_bootstrap(graphs, {}, "s1", budget=10000, notice="KG UPDATE: x\n\n")
        self.assertTrue(noticed["context"].startswith("KG UPDATE: x\n\nKG MEMORY PRELOADED"))
        self.assertEqual(noticed["context"], "KG UPDATE: x\n\n" + plain["context"])

    def test_bootstrap_says_it_once_and_a_resume_says_nothing(self):
        self.seed(latest="1.1.0", notes_seen="1.0.0")
        params = {"project_path": self.root, "claude_session_id": "c1", "plugin_version": "1.0.0"}
        first = self.client.get("/api/session_bootstrap", params=params).json()
        self.assertIn("kg-memory 1.1.0 is available", first["context"])
        resumed = self.client.get("/api/session_bootstrap", params={**params, "source": "resume"}).json()
        self.assertTrue(resumed["reused"])
        self.assertNotIn("KG UPDATE", resumed["context"])
        other = self.client.get("/api/session_bootstrap",
                                params={**params, "claude_session_id": "c2"}).json()
        self.assertNotIn("KG UPDATE", other["context"])

    def test_bootstrap_names_a_plugin_ahead_of_the_server(self):
        self.seed(notes_seen="1.0.0")
        reply = self.client.get("/api/session_bootstrap", params={
            "project_path": self.root, "claude_session_id": "c3", "plugin_version": "1.2.0"}).json()
        self.assertIn("plugin here is 1.2.0 but the memory server runs 1.0.0", reply["context"])


class ReadinessFactsTests(unittest.TestCase):
    def test_recent_sessions_and_a_running_chore(self):
        sm = HTTPSessionManager()
        a = sm.register(None)["session_id"]
        b = sm.register(None)["session_id"]
        sm._sessions[a]["last_activity"] = 0
        self.assertEqual(sm.count_active_since(1.0), 1)
        self.assertFalse(chore_dispatch.running())
        chore_dispatch._running.set()
        try:
            self.assertTrue(chore_dispatch.running())
        finally:
            chore_dispatch._running.clear()
        self.assertIn(b, sm._sessions)


class HookTests(Fresh):
    def test_the_hook_sends_its_plugin_version(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path == "/api/session_bootstrap":
                    seen.update(urllib.parse.parse_qs(url.query))
                    body = {"session_id": "s", "context": "ctx", "text": "", "stats": {}}
                else:
                    body = {"status": "ok"}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        port = free_port()
        server = HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = {"PATH": os.environ["PATH"], "HOME": _STATE.name, "KG_HTTP_PORT": str(port),
               "CLAUDE_PLUGIN_ROOT": str(PLUGIN), "CLAUDE_PROJECT_DIR": _STATE.name}
        out = subprocess.run([str(PLUGIN / "hooks" / "kg-autostart.sh")], input="{}",
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertIn("ctx", out.stdout, out.stderr)
        want = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
        self.assertEqual(seen.get("plugin_version"), [want])


class CheckCommandTests(Fresh):
    def run_kg(self, *args):
        env = {"PATH": os.environ["PATH"], "HOME": _STATE.name,
               "XDG_STATE_HOME": _STATE.name, "KG_HTTP_PORT": str(free_port()),
               "KG_STORAGE_ROOT": _STORAGE.name}
        return subprocess.run([sys.executable, str(PLUGIN / "cli" / "kg.py"), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_check_names_the_release_and_changes_nothing(self):
        self.seed(latest="99.0.0")
        before = STATE_FILE.read_text()
        out = self.run_kg("update", "--check")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("99.0.0", out.stdout)
        self.assertIn("nothing: the server is not running", out.stdout)
        self.assertIn("Verdict: safe now.", out.stdout)
        self.assertEqual(STATE_FILE.read_text(), before)

    def test_nothing_to_update_without_a_newer_release(self):
        out = self.run_kg("update", "--check")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Nothing to update.", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
