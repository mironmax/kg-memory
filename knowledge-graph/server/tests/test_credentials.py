#!/usr/bin/env python3
"""Credentials never reach storage: memory writes holding one are refused.

    cd knowledge-graph/server && ./venv/bin/python tests/test_credentials.py

The fake keys are assembled at run time, so this file never holds a string
that a secret scanner would take for a real key.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["KG_STORAGE_ROOT"] = tempfile.mkdtemp(prefix="kg-test-storage-")
os.environ.pop("KG_CHORES", None)

from core.credentials import credential_kind, term_is_secret_shaped  # noqa: E402
from core.exceptions import KGError  # noqa: E402
from mcp_http.session_manager import HTTPSessionManager  # noqa: E402
from mcp_http.store import GraphConfig, MultiProjectGraphStore  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


MIX = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY"  # 32 mixed characters

FAKE = {
    "Anthropic or OpenAI API key": "sk-" + "ant-" + "api03-" + MIX + MIX,
    "GitHub token": "gh" + "p_" + MIX + "Zz9q",
    "AWS access key": "AK" + "IA" + "Q3EXAMPLE7KEY2ZX",
    "Google API key": "AI" + "za" + MIX + "Qq9",
    "Slack token": "xo" + "xb-" + "1234567890-abcdefABCDEF",
    "private key": "-----BEGIN " + "RSA PRIVATE KEY-----",
}

# Text that real nodes hold and that must stay writable.
BENIGN = [
    "main 158d17b, tag v0.15.1, full sha 0123456789abcdef0123456789abcdef01234567",
    "transcript ~/.claude/projects/x/11be8ade-0c03-40f6-a3d8-cd8b960da570.jsonl",
    "the token lives in ~/.config/anthropic/claude-oauth-token (0600)",
    "Turnstile site key 0x4AAAAAAAB1c2D3e4F5g6H7 is public",
    "YouTube channel UCINV7xyz12AbC34dEf56GhI",
    "token=valid redirects to /result?token=abc",
    "sk-learn is not a key; neither is task-sk-1",
    "node task-sk-compaction-pass-for-the-long-kebab-identifiers-here",
]


def test_shapes():
    print("1. credential shapes")
    for kind, fake in FAKE.items():
        check(f"finds a {kind}", credential_kind(f"note: {fake} here") == kind,
              credential_kind(fake))
    for text in BENIGN:
        check(f"passes {text[:40]!r}", credential_kind(text) is None, credential_kind(text))


def test_recall_terms():
    print("2. recall terms")
    check("drops a lowercased key", term_is_secret_shaped(FAKE["Google API key"].lower()))
    check("drops a full sha", term_is_secret_shaped("0123456789abcdef0123456789abcdef01234567"))
    for term in ("devdocs/ops/2026-10-15-watcher-build-sdk-letter.md", "test_v0924.py", "158d17b",
                 "knowledge-graph/server/mcp_http/ambient.py"):
        check(f"keeps {term}", not term_is_secret_shaped(term))


def test_store_refuses():
    print("3. the store refuses and names the field, never the value")
    store = MultiProjectGraphStore(GraphConfig(save_interval=9999), HTTPSessionManager())
    fake = FAKE["GitHub token"]
    for field, kwargs in (("gist", {"gist": f"push with {fake}"}),
                          ("notes", {"gist": "a gist", "notes": ["fine", f"used {fake}"]}),
                          ("touches", {"gist": "a gist", "touches": [f"https://x.test/?t={fake}"]})):
        try:
            store.put_node("user", f"probe-{field}", kwargs.pop("gist"), **kwargs)
            err = None
        except KGError as e:
            err = str(e)
        check(f"refuses a key in the {field}", err is not None and f"in the {field}" in err, err)
        check(f"the {field} message holds no key", err is not None and fake not in err)
        check(f"nothing stored for the {field}",
              f"probe-{field}" not in store.graphs["user"]["nodes"])

    store.put_node("user", "token-location", "the token lives in a 0600 file",
                   notes=["path ~/.config/anthropic/claude-oauth-token"])
    check("a pointer to the secret is written",
          "token-location" in store.graphs["user"]["nodes"])

    store.put_node("user", "edge-end", "another node")
    try:
        store.put_edge("user", "token-location", "edge-end", "relates-to",
                       notes=[f"key {FAKE['AWS access key']}"])
        err = None
    except KGError as e:
        err = str(e)
    check("refuses a key in edge notes", err is not None and "AWS access key" in err, err)
    check("no edge stored",
          not any(k[:2] == ("token-location", "edge-end") for k in store.graphs["user"]["edges"]))


def test_web_targets():
    print("4. fetched URLs are counted without their secret-bearing parts")
    from mcp_http.ambient import _extract_target
    signed = "https://user:pw@bucket.example.test/a/b.png?X-Amz-Signature=abc123def456&t=1#frag"
    check("drops user info, query and fragment",
          _extract_target("WebFetch", {"url": signed}) == ("web", "https://bucket.example.test/a/b.png"),
          _extract_target("WebFetch", {"url": signed}))
    check("keeps a plain URL", _extract_target("WebFetch", {"url": "https://example.test/docs"})
          == ("web", "https://example.test/docs"))
    check("ignores what is not a URL", _extract_target("WebFetch", {"url": "not a url"}) is None)


def main():
    test_shapes()
    test_recall_terms()
    test_store_refuses()
    test_web_targets()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
