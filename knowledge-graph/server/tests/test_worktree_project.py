#!/usr/bin/env python3
"""A linked git worktree belongs to its main checkout's project.

    cd knowledge-graph/server && ./venv/bin/python tests/test_worktree_project.py

Builds real repositories with `git worktree add` in temp folders under
~/.cache (project paths must lie within home); submodule and bare layouts are
written by hand in the shape git gives them. Uses a temp KG_STORAGE_ROOT.
"""

import os
os.environ["KG_BUDGET_NOTICES"] = "0"
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-worktree-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name

from core.checkout import main_checkout_path
from core.constants import memory_project_root, project_namespace, resolve_project_root
from mcp_http import ambient, file_recall
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore

STORAGE = Path(_STORAGE.name)


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=cwd, check=True, capture_output=True)


class Layout(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="kg-worktree-", dir=Path.home() / ".cache")
        self.addCleanup(folder.cleanup)
        self.base = str(Path(folder.name).resolve())
        self.main = os.path.join(self.base, "app")
        os.makedirs(os.path.join(self.main, "src"))
        Path(self.main, "src", "app.py").write_text("x = 1\n")
        git("init", "-q", cwd=self.main)
        git("add", "-A", cwd=self.main)
        git("commit", "-q", "-m", "init", cwd=self.main)
        self.wt = os.path.join(self.base, "app-fix")
        git("worktree", "add", "-q", self.wt, "-b", "fix", cwd=self.main)


class CheckoutTests(Layout):
    def test_a_linked_worktree_maps_to_its_main_checkout(self):
        self.assertEqual(main_checkout_path(self.wt), self.main)
        self.assertEqual(main_checkout_path(os.path.join(self.wt, "src")),
                         os.path.join(self.main, "src"))
        self.assertEqual(main_checkout_path(os.path.join(self.wt, "src", "app.py")),
                         os.path.join(self.main, "src", "app.py"))

    def test_a_worktree_inside_the_main_checkout_maps_too(self):
        inner = os.path.join(self.main, ".claude", "worktrees", "agent-1")
        git("worktree", "add", "-q", inner, "-b", "agent", cwd=self.main)
        self.assertEqual(main_checkout_path(inner), self.main)

    def test_main_checkout_and_plain_folders_are_unchanged(self):
        self.assertEqual(main_checkout_path(self.main), self.main)
        self.assertEqual(main_checkout_path(os.path.join(self.main, "src")),
                         os.path.join(self.main, "src"))
        plain = os.path.join(self.base, "plain")
        os.makedirs(plain)
        self.assertEqual(main_checkout_path(plain), plain)

    def test_a_submodule_stays_its_own_project(self):
        sub = os.path.join(self.main, "vendor", "lib")
        modules = os.path.join(self.main, ".git", "modules", "lib")
        os.makedirs(sub)
        os.makedirs(modules)
        Path(sub, ".git").write_text("gitdir: ../../.git/modules/lib\n")
        self.assertEqual(main_checkout_path(sub), sub)

    def test_a_worktree_of_a_bare_repository_is_unchanged(self):
        bare = os.path.join(self.base, "bare.git")
        git("clone", "-q", "--bare", self.main, bare, cwd=self.base)
        wt = os.path.join(self.base, "bare-wt")
        git("worktree", "add", "-q", wt, "fix", cwd=bare)
        self.assertEqual(main_checkout_path(wt), wt)

    def test_a_removed_main_checkout_leaves_the_worktree_alone(self):
        Path(self.wt, ".git").write_text("gitdir: /nonexistent/.git/worktrees/x\n")
        self.assertEqual(main_checkout_path(self.wt), self.wt)

    def test_a_git_file_pointing_outside_home_is_not_followed(self):
        outside = tempfile.TemporaryDirectory(prefix="kg-outside-home-")
        self.addCleanup(outside.cleanup)
        git_dir = os.path.join(outside.name, "worktrees", "x")
        os.makedirs(git_dir)
        Path(git_dir, "commondir").write_text(os.path.join(self.main, ".git") + "\n")
        Path(self.wt, ".git").write_text(f"gitdir: {git_dir}\n")
        self.assertEqual(main_checkout_path(self.wt), self.wt)

    def test_a_worktree_outside_home_is_left_alone(self):
        outside = tempfile.TemporaryDirectory(prefix="kg-outside-home-")
        self.addCleanup(outside.cleanup)
        main = os.path.join(outside.name, "app")
        os.makedirs(main)
        git("init", "-q", cwd=main)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=main)
        wt = os.path.join(outside.name, "app-fix")
        git("worktree", "add", "-q", wt, "-b", "fix", cwd=main)
        opened = []
        real_open = open

        def recording_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)
        with mock.patch("builtins.open", recording_open):
            self.assertEqual(main_checkout_path(wt), os.path.realpath(wt))
        self.assertEqual(opened, [], "nothing outside home is read")

    def test_a_commondir_pointing_outside_home_is_not_followed(self):
        outside = tempfile.TemporaryDirectory(prefix="kg-outside-home-")
        self.addCleanup(outside.cleanup)
        elsewhere = os.path.join(outside.name, "other")
        os.makedirs(os.path.join(elsewhere, ".git"))
        git_dir = os.path.join(self.base, "fake-git-dir")
        os.makedirs(git_dir)
        Path(git_dir, "commondir").write_text(os.path.join(elsewhere, ".git") + "\n")
        Path(self.wt, ".git").write_text(f"gitdir: {git_dir}\n")
        self.assertEqual(main_checkout_path(self.wt), self.wt)

    def test_project_roots_resolve_to_the_main_checkout(self):
        self.assertEqual(resolve_project_root(self.wt), self.main)
        self.assertEqual(memory_project_root(self.wt), self.main)
        with self.assertRaises(ValueError):
            resolve_project_root("/etc")

    def test_file_keys_from_a_worktree_are_project_relative(self):
        self.assertEqual(ambient.file_key(os.path.join(self.wt, "src", "app.py"), self.main),
                         os.path.join("src", "app.py"))
        other = os.path.join(self.base, "elsewhere.py")
        self.assertEqual(ambient.file_key(other, self.main), other)


class SessionTests(Layout):
    def setUp(self):
        super().setUp()
        (STORAGE / "sessions.json").unlink(missing_ok=True)
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)
        file_recall.reset_throttle()

    def test_a_worktree_session_shares_the_main_project_graph(self):
        main_sid = self.sm.register(self.main)["session_id"]
        wt = self.sm.register(self.wt, claude_sid="cc-wt")
        self.assertEqual(wt["project_path"], self.main)
        self.store.put_node(level="project", node_id="app-entry", gist="WRITTEN_FROM_WORKTREE",
                            session_id=wt["session_id"])
        self.assertIn("app-entry", self.store.graphs[project_namespace(self.main)]["nodes"])
        self.assertEqual(self.sm.find_by_project_path(self.wt)[0], wt["session_id"])
        self.assertNotEqual(main_sid, wt["session_id"])
        self.assertFalse((STORAGE / "projects" / "app-fix").exists())

    def test_file_recall_works_inside_a_worktree(self):
        writer = self.sm.register(self.main)["session_id"]
        self.store.put_node(level="project", node_id="app-entry", gist="APP_MEMORY",
                            touches=["src/app.py"], session_id=writer)
        self.sm.register(self.wt, claude_sid="cc-wt")
        text = ambient.handle_tool_event(self.store, self.sm, {
            "cwd": self.wt, "tool_name": "Read", "session_id": "cc-wt",
            "tool_input": {"file_path": os.path.join(self.wt, "src", "app.py")}})
        self.assertIn("APP_MEMORY", text or "")
        self.assertFalse((STORAGE / "projects" / "app-fix").exists())


if __name__ == "__main__":
    unittest.main()
