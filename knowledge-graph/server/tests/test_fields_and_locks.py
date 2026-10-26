#!/usr/bin/env python3
"""Extension fields and locked nodes: what a storage root declares, who may
write it, and that nothing an agent or the compactor does removes a rule the
person wrote.

    cd knowledge-graph/server && ./venv/bin/python tests/test_fields_and_locks.py

Uses a temp KG_STORAGE_ROOT; fields.json is written there per test.
"""

import asyncio
import json
import os
os.environ["KG_BUDGET_NOTICES"] = "0"
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_STORAGE = tempfile.TemporaryDirectory(prefix="kg-fields-storage-")
os.environ["KG_STORAGE_ROOT"] = _STORAGE.name

from fastapi.testclient import TestClient
import mcp.types as types

from core.chores import candidates_by_kind
from core.compactor import Compactor
from core.estimator import CharEstimator
from core.exceptions import KGError, NodeLockedError
from core.fields import FIELDS_FILE, load_schema
from core.render import render_active_line
from core.scorer import NodeScorer
from mcp_http.read_format import format_node_full
from mcp_http.rest import create_rest_api
from mcp_http.session_manager import HTTPSessionManager
from mcp_http.store import GraphConfig, MultiProjectGraphStore
from mcp_http.websocket import ConnectionManager
import mcp_streamable_server as srv

STORAGE = Path(_STORAGE.name)
TEAM_SCHEMA = {
    "tags": {"agent": True},
    "kind": {"type": "enum", "values": ["fact", "decision"], "agent": True},
    "review_at": {"type": "number", "min": 0, "agent": True},
    "util": {"type": "number"},
    "pinned": {"type": "bool", "agent": True},
    "source": {"type": "string", "max": 10, "agent": True},
}


def write_schema(schema):
    path = STORAGE / FIELDS_FILE
    path.write_text(json.dumps(schema) if not isinstance(schema, str) else schema)
    # A rewrite inside one mtime tick would hit the cache.
    os.utime(path, ns=(time.time_ns(), time.time_ns()))


class Fixture(unittest.TestCase):
    def setUp(self):
        (STORAGE / FIELDS_FILE).unlink(missing_ok=True)
        (STORAGE / "user.json").unlink(missing_ok=True)
        self.addCleanup(lambda: (STORAGE / FIELDS_FILE).unlink(missing_ok=True))
        self.sm = HTTPSessionManager()
        self.store = MultiProjectGraphStore(GraphConfig(save_interval=9999), self.sm)
        self.addCleanup(self.store.shutdown)

    def node(self, node_id):
        return self.store.graphs["user"]["nodes"][node_id]

    def rule(self, node_id="team-charter"):
        self.store.put_node("user", node_id, "Never deploy on Fridays",
                            fields={"locked": True}, admin=True)


class SchemaTests(Fixture):
    def test_no_file_declares_nothing(self):
        schema = load_schema(STORAGE)
        self.assertEqual(schema.specs, {})
        with self.assertRaisesRegex(KGError, "declares: none"):
            schema.validate({"kind": "fact"}, admin=True)

    def test_values_are_checked_by_type(self):
        write_schema(TEAM_SCHEMA)
        schema = load_schema(STORAGE)
        good = {"tags": ["general", "area:ops", "general"], "kind": "fact",
                "review_at": 5, "pinned": True, "source": "web"}
        self.assertEqual(schema.validate(good, admin=False)["tags"], ["general", "area:ops"])
        for bad in ({"tags": ["Has Space"]}, {"tags": "general"}, {"kind": "rumour"},
                    {"review_at": -1}, {"review_at": True}, {"pinned": "yes"},
                    {"source": "x" * 11}):
            with self.subTest(bad=bad), self.assertRaises(KGError):
                schema.validate(bad, admin=False)

    def test_admin_only_fields_and_lock_refuse_agents(self):
        write_schema(TEAM_SCHEMA)
        schema = load_schema(STORAGE)
        with self.assertRaisesRegex(KGError, "admin surface"):
            schema.validate({"util": 0.4}, admin=False)
        with self.assertRaisesRegex(KGError, "set by a person"):
            schema.validate({"locked": True}, admin=False)
        self.assertEqual(schema.validate({"util": 0.4, "locked": False}, admin=True),
                         {"util": 0.4, "locked": None})

    def test_a_broken_file_is_named_in_every_refusal(self):
        for broken in ("{not json", json.dumps({"gist": {"type": "string"}}),
                       json.dumps({"kind": {"type": "enum"}}), json.dumps({"x": {"type": "date"}})):
            with self.subTest(broken=broken):
                write_schema(broken)
                schema = load_schema(STORAGE)
                self.assertEqual(schema.specs, {})
                with self.assertRaisesRegex(KGError, "fields.json not loaded"):
                    schema.validate({"kind": "fact"}, admin=True)


class StoreFieldTests(Fixture):
    def test_fields_are_top_level_keys_and_null_removes(self):
        write_schema(TEAM_SCHEMA)
        self.store.put_node("user", "a-decision", "g", fields={"kind": "decision", "util": 0.3},
                            admin=True)
        self.assertEqual((self.node("a-decision")["kind"], self.node("a-decision")["util"]),
                         ("decision", 0.3))
        self.store.put_node("user", "a-decision", "g", fields={"util": None}, admin=True)
        self.assertNotIn("util", self.node("a-decision"))
        self.assertEqual(self.node("a-decision")["kind"], "decision")

    def test_unknown_stored_keys_survive_writes_without_fields(self):
        self.store.put_node("user", "imported", "g")
        self.node("imported")["trust"] = "trusted"
        self.store.put_node("user", "imported", "g2")
        self.assertEqual(self.node("imported")["trust"], "trusted")

    def test_a_field_change_alone_is_a_write(self):
        write_schema(TEAM_SCHEMA)
        self.store.put_node("user", "a-fact", "g")
        before = self.node("a-fact")["_written"]["ts"]
        time.sleep(0.01)
        self.store.put_node("user", "a-fact", "g", fields={"kind": "fact"})
        self.assertGreater(self.node("a-fact")["_written"]["ts"], before)


class LockTests(Fixture):
    def test_agents_cannot_rewrite_delete_or_rename_a_locked_node(self):
        self.rule()
        for op, attempt in (
            ("rewrite", lambda: self.store.put_node("user", "team-charter", "Deploy any day")),
            ("delete", lambda: self.store.delete_node("team-charter", level="user")),
            ("rename", lambda: self.store.rename_node("team-charter", "other-name", level="user")),
        ):
            with self.subTest(op=op), self.assertRaises(NodeLockedError):
                attempt()
        self.assertEqual(self.node("team-charter")["gist"], "Never deploy on Fridays")
        log = self.store.get_progress("rejections", level="user")["entries"]
        self.assertEqual([e["op"] for e in log], ["rewrite", "delete", "rename"])

    def test_a_person_edits_and_unlocks_it(self):
        self.rule()
        self.store.put_node("user", "team-charter", "Never deploy on Friday after noon", admin=True)
        self.store.put_node("user", "team-charter", "g", fields={"locked": False}, admin=True)
        self.assertNotIn("locked", self.node("team-charter"))
        self.store.put_node("user", "team-charter", "an agent may now edit it")

    def test_edges_to_a_locked_node_stay_writable(self):
        self.rule()
        self.store.put_node("user", "release-plan", "g")
        self.store.put_edge("user", "release-plan", "team-charter", "relied-on")
        self.assertEqual(self.node("team-charter")["gist"], "Never deploy on Fridays")

    def test_the_rejection_log_is_bounded(self):
        self.rule()
        for _ in range(55):
            with self.assertRaises(NodeLockedError):
                self.store.put_node("user", "team-charter", "x")
        self.assertEqual(len(self.store.get_progress("rejections", level="user")["entries"]), 50)


class CompactionTests(unittest.TestCase):
    def graph(self):
        old = time.time() - 400 * 86400
        nodes = {nid: {"id": nid, "gist": "x" * 60, "_created_ts": old} for nid in "abcd"}
        nodes["a"]["locked"] = True  # the lowest-scored: no edges, no endorsements
        for nid in "bcd":
            nodes[nid]["_useful_ts"] = [time.time()]
        return nodes

    def test_a_locked_node_is_never_archived(self):
        nodes, est = self.graph(), CharEstimator()
        comp = Compactor(NodeScorer(0), est, est.estimate_graph(nodes, {}) - 10)
        archived = comp.compact_if_needed(nodes, {}, {})
        self.assertTrue(archived)
        self.assertNotIn("a", archived)
        self.assertFalse(nodes["a"].get("_archived"))

    def test_locked_alone_over_budget_stays_over_budget(self):
        nodes = {"a": {"id": "a", "gist": "x" * 200, "locked": True, "_created_ts": 0}}
        comp = Compactor(NodeScorer(0), CharEstimator(), 50)
        self.assertEqual(comp.compact_if_needed(nodes, {}, {}), [])
        self.assertFalse(nodes["a"].get("_archived"))

    def test_rebalance_never_swaps_a_locked_node_out(self):
        nodes, est = self.graph(), CharEstimator()
        nodes["b"]["_archived"] = True
        comp = Compactor(NodeScorer(0), est, est.estimate_graph(nodes, {}) + 5)
        swaps = comp.rebalance(nodes, {}, {})
        self.assertNotIn("a", [out for _, out in swaps])
        self.assertFalse(nodes["a"].get("_archived"))

    def test_restore_brings_back_archived_and_orphaned_locked_nodes(self):
        nodes = self.graph()
        nodes["a"]["_archived"] = True
        nodes["b"].update(locked=True, _archived=True, _orphaned_ts=1.0)
        restored = Compactor(NodeScorer(0), CharEstimator(), 10_000).restore_locked(nodes)
        self.assertEqual(sorted(restored), ["a", "b"])
        self.assertTrue(all(not n.get("_archived") and "_orphaned_ts" not in n
                            for n in (nodes["a"], nodes["b"])))

    def test_the_orphan_purge_skips_a_locked_node(self):
        store = MultiProjectGraphStore(GraphConfig(save_interval=9999), HTTPSessionManager())
        self.addCleanup(store.shutdown)
        store.put_node("user", "team-charter", "g", fields={"locked": True}, admin=True)
        store.graphs["user"]["nodes"]["team-charter"]["_orphaned_ts"] = 0.0
        store._prune_orphans("user")
        self.assertIn("team-charter", store.graphs["user"]["nodes"])


class SurfaceTests(Fixture):
    def test_the_render_marks_a_locked_node_and_charges_the_mark(self):
        node = {"id": "a", "gist": "g", "locked": True}
        line = render_active_line("a", "g", locked=True)
        self.assertEqual(line, "  a (locked): g")
        self.assertEqual(CharEstimator.estimate_node("a", node), len(line) + 1)

    def test_chores_skip_locked_nodes(self):
        nodes = [{"id": "long", "gist": "x" * 900, "locked": True}]
        pools = candidates_by_kind(nodes, [])
        self.assertNotIn("long", [i for ids in pools.values() for i in ids])

    def test_a_full_read_shows_lock_tags_and_fields(self):
        text = format_node_full("a", {"level": "user", "node": {
            "id": "a", "gist": "g", "locked": True, "tags": ["general"], "kind": "fact"}})
        self.assertIn("(user, active, locked)", text)
        self.assertIn("tags: general", text)
        self.assertIn('fields: kind="fact"', text)

    def test_search_indexes_tags_and_filters_by_them(self):
        write_schema(TEAM_SCHEMA)
        self.store.put_node("user", "billing-note", "invoices go out monthly",
                            fields={"tags": ["area:finance"]})
        self.store.put_node("user", "deploy-note", "invoices are not deploys",
                            fields={"tags": ["area:ops"]})
        hits = self.store.search("finance")
        self.assertEqual([r["id"] for r in hits["top"]], ["billing-note"])
        scoped = self.store.search("invoices", tags=["area:ops"])
        self.assertEqual([r["id"] for r in scoped["top"]], ["deploy-note"])

    def test_rest_is_the_admin_surface(self):
        write_schema(TEAM_SCHEMA)
        client = TestClient(create_rest_api(self.store, self.sm, ConnectionManager(), "test"))
        reply = client.post("/api/nodes", json={"level": "user", "id": "team-charter", "gist": "g",
                                                "fields": {"locked": True, "util": 0.5}})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertTrue(self.node("team-charter")["locked"])
        reply = client.post("/api/nodes", json={"level": "user", "id": "x", "gist": "g",
                                                "fields": {"colour": "red"}})
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(client.delete("/api/nodes/user/team-charter").status_code, 200)


class McpTests(Fixture):
    def mcp(self):
        srv.store, srv.session_manager = self.store, self.sm
        server = srv.create_mcp_server()
        ctx = SimpleNamespace(transport=SimpleNamespace(headers={}))
        listed = asyncio.run(server.get_request_handler("tools/list").handler(ctx, None)).tools
        call_handler = server.get_request_handler("tools/call").handler

        def call(name, **arguments):
            params = types.CallToolRequestParams(name=name, arguments=arguments)
            return asyncio.run(call_handler(ctx, params)).content[0].text
        return {t.name: t.input_schema["properties"] for t in listed}, call

    def test_a_root_without_fields_adds_no_parameters(self):
        props, _ = self.mcp()
        self.assertNotIn("tags", props["kg_put_node"])
        self.assertNotIn("fields", props["kg_put_node"])
        self.assertNotIn("tags", props["kg_search"])

    def test_declared_agent_fields_become_parameters(self):
        write_schema(TEAM_SCHEMA)
        props, call = self.mcp()
        self.assertIn("tags", props["kg_put_node"])
        self.assertEqual(sorted(props["kg_put_node"]["fields"]["properties"]),
                         ["kind", "pinned", "review_at", "source"])
        self.assertIn("tags", props["kg_search"])
        sid = self.sm.register()["session_id"]
        text = call("kg_put_node", session_id=sid, level="user", id="tagged-note", gist="g",
                    tags=["general"], fields={"kind": "fact"})
        self.assertIn("saved", text)
        self.assertEqual((self.node("tagged-note")["tags"], self.node("tagged-note")["kind"]),
                         (["general"], "fact"))

    def test_an_agent_write_to_a_locked_node_says_how_a_person_edits_it(self):
        self.rule()
        _, call = self.mcp()
        sid = self.sm.register()["session_id"]
        text = call("kg_put_node", session_id=sid, level="user", id="team-charter", gist="x")
        self.assertIn("locked", text)
        self.assertIn("visual editor", text)
        text = call("kg_delete_node", session_id=sid, id="team-charter")
        self.assertIn("locked", text)
        self.assertEqual(self.node("team-charter")["gist"], "Never deploy on Fridays")


class DoctorTests(unittest.TestCase):
    """kg doctor's storage line: locked nodes and what fields.json declares."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
        import steps
        self.extras = steps.storage_extras
        folder = tempfile.TemporaryDirectory(prefix="kg-doctor-root-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_nothing_to_say_says_nothing(self):
        self.assertEqual(self.extras(self.root), "")

    def test_counts_locked_nodes_and_names_fields(self):
        (self.root / "user.json").write_text(json.dumps({"nodes": {
            "a": {"id": "a", "gist": "g", "locked": True}, "b": {"id": "b", "gist": "g"}}}))
        (self.root / FIELDS_FILE).write_text(json.dumps(TEAM_SCHEMA))
        self.assertEqual(self.extras(self.root), ", 1 locked node(s), extra node fields: "
                         "kind, pinned, review_at, source, tags, util")

    def test_an_unreadable_file_is_flagged(self):
        (self.root / FIELDS_FILE).write_text("{not json")
        self.assertTrue(self.extras(self.root).startswith("!fields.json unreadable"))


if __name__ == "__main__":
    unittest.main()
