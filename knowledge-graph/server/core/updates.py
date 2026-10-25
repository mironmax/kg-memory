"""What is new: a release on PyPI, a plugin ahead of its server, and the
upgrade notes a release leaves for the agent.

The server reads PyPI's public JSON for kg-memory at most once a day, in a
background thread, and keeps the answer in updates.json in the storage root,
beside chore_state.json. The request
carries nothing about the user; KG_UPDATE_CHECK=0 turns it off. Besides the
user's own fetches, it is the server's only outbound request.

The session-start preload then says it through the agent:
  - the update line, when PyPI or the plugin is ahead of this server, at
    most once a day per project;
  - the upgrade notes (upgrade-notes/<version>.md beside the server) of every
    version this machine has not been shown yet, once, in version order.
Both are recorded when the preload is rendered, not when it is delivered: a
hook reply lost to its timeout loses that day's line, or the notes.

`kg doctor` and `kg update --check` read the same state file (cli/doctor.py)
and never write it.
"""

import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

from .constants import get_storage_root

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/kg-memory/json"
CHECK_INTERVAL = 86400          # one PyPI read a day
WAKE_INTERVAL = 3600            # a long-running server re-checks the age hourly
FETCH_TIMEOUT = 10
STATE_NAME = "updates.json"
NOTES_DIR = Path(__file__).resolve().parents[2] / "upgrade-notes"
# The preload holds ~10K inline: the notes may take a share, never the room.
NOTES_CHAR_CAP = 3000

_lock = threading.Lock()


def check_enabled() -> bool:
    return os.getenv("KG_UPDATE_CHECK", "1") != "0"


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(x) if x.isdigit() else 0 for x in str(text).split("."))


def newer(candidate: str | None, than: str) -> bool:
    return bool(candidate) and version_key(candidate) > version_key(than)


def _path() -> Path:
    return get_storage_root() / STATE_NAME


def read_state() -> dict:
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data: dict) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning(f"Could not write {path}: {exc}")


def fetch_latest(timeout: float = FETCH_TIMEOUT) -> str | None:
    """The latest release on PyPI, or None when it cannot be read."""
    request = urllib.request.Request(PYPI_URL, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read())["info"]["version"]
    except Exception as exc:
        logger.info(f"Release check: PyPI not read ({exc.__class__.__name__})")
        return None


def refresh(now: float | None = None, fetch=fetch_latest) -> bool:
    """Read PyPI if the last read is a day old. True when it was read."""
    if not check_enabled():
        return False
    now = time.time() if now is None else now
    if now - (read_state().get("checked_at") or 0) < CHECK_INTERVAL:
        return False
    latest = fetch()
    with _lock:
        data = read_state()
        # A failed read is stamped too: one try a day, not one a wake.
        data["checked_at"] = now
        if latest:
            data["latest"] = latest
        _write_state(data)
    return bool(latest)


class ReleaseChecker:
    """The background thread that keeps the daily PyPI read."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="release-check", daemon=True)

    def start(self) -> None:
        if check_enabled():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                refresh()
            except Exception:
                logger.exception("Release check failed")
            self._stop.wait(WAKE_INTERVAL)


def _notes_between(after: str | None, upto: str) -> list[tuple[str, str]]:
    """(version, text) of each note newer than `after` and not newer than
    `upto`, oldest first. No record yet (a fresh install, or an upgrade from
    before the channel): only `upto`'s own note."""
    try:
        files = list(NOTES_DIR.glob("*.md"))
    except OSError:
        return []
    found = []
    for path in files:
        version = path.stem
        if newer(version, upto):
            continue
        if after is None and version != upto:
            continue
        if after is not None and not newer(version, after):
            continue
        try:
            found.append((version, path.read_text().strip()))
        except OSError:
            continue
    return sorted(found, key=lambda item: version_key(item[0]))


def _update_line(server: str, latest: str | None, plugin: str | None) -> str:
    if newer(plugin, server):
        return (f"KG UPDATE: the knowledge-graph plugin here is {plugin} but the memory server "
                f"runs {server}, so they can disagree. ")
    if newer(latest, server):
        return f"KG UPDATE: kg-memory {latest} is available (this machine runs {server}). "
    return ""


def session_notice(project_key: str, server: str, plugin: str | None = None,
                   today: str | None = None) -> str:
    """The update line and any unseen upgrade notes for a fresh preload, as
    one block for its top, or "". Records what it returns."""
    today = today or time.strftime("%Y-%m-%d")
    with _lock:
        data = read_state()
        line = _update_line(server, data.get("latest") if check_enabled() else None, plugin)
        announced = data.get("announced") or {}
        if line and announced.get(project_key) == today:
            line = ""
        notes = _notes_between(data.get("notes_seen"), server)
        changed = False
        if line:
            # Only today's entries decide anything; older ones go.
            data["announced"] = {k: v for k, v in announced.items() if v == today}
            data["announced"][project_key] = today
            changed = True
        if data.get("notes_seen") != server and not newer(data.get("notes_seen"), server):
            data["notes_seen"] = server
            changed = True
        if changed:
            _write_state(data)
    parts = []
    if line:
        parts.append(line + "Tell the user in one line. If they want it, follow the kg-ops "
                     "skill's Update section: `kg update --check` first, then `kg update` on their yes.")
    if notes:
        body = "\n\n".join(f"[{version}]\n{text}" for version, text in notes)
        if len(body) > NOTES_CHAR_CAP:
            body = (body[:NOTES_CHAR_CAP].rsplit("\n", 1)[0]
                    + f"\n[…cut: the full notes are in {NOTES_DIR}]")
        parts.append("KG UPGRADE NOTES — kg-memory changed since this machine last used it. "
                     "Written for you, the agent: tell the user what matters to them in a few "
                     "lines, and offer any steps they name; the user decides.\n" + body)
    return "\n\n".join(parts) + "\n\n" if parts else ""
