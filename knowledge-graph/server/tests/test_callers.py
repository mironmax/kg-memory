#!/usr/bin/env python3
"""Callers outside the hook: a module registers its own session, naming
itself and the nodes it already put in front of the model.

    cd knowledge-graph/server && ./venv/bin/python tests/test_callers.py

Uses a temp KG_STORAGE_ROOT and a temp project under ~/.cache.
"""

import os
os.environ["KG_BUDGET_NOTICES"] = "0"
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-callers-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name

from fastapi.testclient import TestClient

from mcp_http import chore_dispatch
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager

STORAGE = Path(_STORAGE.name)


class RegisterTests(unittest.TestCase):
    def setUp(self):
        (STORAGE / "sessions.json").unlink(missing_ok=True)
        folder = tempfile.TemporaryDirectory(prefix="kg-callers-project-", dir=Path.home() / ".cache")
        self.addCleanup(folder.cleanup)
        self.root = str(Path(folder.name).resolve())
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        self.client = TestClient(create_rest_api(self.store, self.sm, ConnectionManager(), "test"))

    def test_the_editor_registers_by_query_as_before(self):
        reply = self.client.post("/api/sessions/register", params={"project_path": self.root})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["project_path"], self.root)
        self.assertNotIn("agent", self.sm._sessions[reply.json()["session_id"]])

    def test_a_module_names_itself_and_its_preload(self):
        reply = self.client.post("/api/sessions/register", json={
            "project_path": self.root, "agent": "reviewer bot",
            "preloaded_ids": ["team-charter", "deploy-steps"]})
        self.assertEqual(reply.status_code, 200)
        sid = reply.json()["session_id"]
        data = self.sm._sessions[sid]
        self.assertEqual(data["agent"], "reviewer bot")
        self.assertEqual(set(data["preloaded_ids"]), {"team-charter", "deploy-steps"})
        self.assertTrue({"team-charter", "deploy-steps"} <= set(self.sm.get_seen(sid)))

    def test_malformed_input_is_refused_and_registers_nothing(self):
        before = len(self.sm._sessions)
        for body in ({"agent": "../../x"}, {"agent": "x" * 65}, {"preloaded_ids": ["no/slash"]}):
            with self.subTest(body=body):
                reply = self.client.post("/api/sessions/register",
                                         json={"project_path": self.root, **body})
                self.assertEqual(reply.status_code, 400)
        self.assertEqual(len(self.sm._sessions), before)

    def test_a_module_session_is_not_proof_of_hooks(self):
        self.client.post("/api/sessions/register", json={
            "project_path": self.root, "preloaded_ids": ["team-charter"]})
        self.assertFalse(self.sm.hooks_seen(self.root, "claude-code"))


class HooksOffTests(unittest.TestCase):
    """KG_HOOKS=off: every plugin hook exits at once, says nothing and
    starts nothing. The port is a listener that counts connections: a hook
    that went on would reach it."""

    HOOKS = Path(__file__).resolve().parents[2] / "hooks"

    def setUp(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.2)
        self.addCleanup(self.listener.close)
        self.port = self.listener.getsockname()[1]

    def connections(self) -> int:
        count = 0
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return count
            conn.close()
            count += 1

    def run_hook(self, *argv):
        env = dict(os.environ, KG_HOOKS="off", KG_HTTP_PORT=str(self.port), KG_CHORE="")
        payload = '{"cwd": "/tmp", "prompt": "hi", "conversationId": "c1"}'
        return subprocess.run([*argv], input=payload, env=env,
                              capture_output=True, text=True, timeout=10)

    def test_every_hook_stays_silent(self):
        for name in ("kg-autostart.sh", "kg-remind.sh", "kg-tool-event.sh"):
            with self.subTest(hook=name):
                done = self.run_hook("bash", str(self.HOOKS / name))
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
                self.assertEqual(self.connections(), 0)
        for event in ("SessionStart", "PreInvocation", "PostToolUse"):
            with self.subTest(hook="kg-agy.py", event=event):
                done = self.run_hook(sys.executable, str(self.HOOKS / "kg-agy.py"), event)
                self.assertEqual(done.returncode, 0)
                self.assertEqual(self.connections(), 0)

    def test_a_chore_run_turns_the_hooks_off(self):
        env = chore_dispatch.chore_env({"PATH": "/usr/bin"})
        self.assertEqual((env["KG_HOOKS"], env["KG_CHORE"]), ("off", "1"))


if __name__ == "__main__":
    unittest.main()
