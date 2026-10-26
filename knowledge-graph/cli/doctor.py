"""kg doctor, setup, update and uninstall: one set of steps, read, fixed or reversed."""

import os
import sys

try:
    from . import kg, steps
except ImportError:  # run as a script from a checkout
    import kg
    import steps

MARK = {steps.OK: "✓", steps.FIX: "✗", steps.MANUAL: "✗", steps.SUGGEST: "·", steps.OFF: "–"}


def survey(ctx) -> list[tuple]:
    rows = []
    for step in steps.STEPS:
        try:
            state, detail = step.check(ctx)
        except Exception as exc:  # a broken check must not hide the others
            state, detail = steps.MANUAL, f"check failed: {exc}"
        rows.append((step, state, detail))
    return rows


def show(rows) -> None:
    groups = {}
    for step, state, detail in rows:
        groups.setdefault(step.group, []).append((state, detail))
    for group, lines in groups.items():
        print(f"\n{group}")
        if all(state == steps.OFF for state, _ in lines) and len({d for _, d in lines}) == 1:
            lines = lines[:1]   # one "not installed", not one per step
        for state, detail in lines:
            print(f"  {MARK[state]} {detail}")


def release_line(want: str) -> str | None:
    latest = steps.latest_release()
    if latest and steps.version_key(latest) > steps.version_key(want):
        return f"kg-memory {latest} is available: `kg update --check`, then `kg update`"
    return None


def run() -> int:
    ctx = steps.Context()
    print(f"kg {ctx.want} — {kg.ROOT}")
    if line := release_line(ctx.want):
        print(f"  {line}")
    rows = survey(ctx)
    show(rows)
    problems = sum(state in (steps.FIX, steps.MANUAL) for _, state, _ in rows)
    offers = sum(state in (steps.FIX, steps.SUGGEST) for _, state, _ in rows)
    print(f"\n{problems} issue(s)." if problems else "\nAll good.", end="")
    print(f" `kg setup` can fix or improve {offers}." if offers else "")
    return 1 if problems else 0


def setup(assume_yes: bool, only: set[str] | None, plan: bool) -> int:
    ctx = steps.Context()
    print(f"kg {ctx.want} — {kg.ROOT}")
    rows = survey(ctx)
    show(rows)
    offered = [(s, st, d) for s, st, d in rows
               if st in (steps.FIX, steps.SUGGEST) and (not only or s.key in only)]
    manual = [(s, d) for s, st, d in rows if st == steps.MANUAL]
    if not offered:
        print("\nNothing to set up." + (" Manual steps are listed above." if manual else ""))
        return 1 if manual else 0
    print("\nSetup would:")
    for step, _, _ in offered:
        note = " (opt-in: spends quota)" if step.opt_in else ""
        print(f"  [{step.key}] {step.group}: {step.title}{note}")
    if plan:
        print("\nRun `kg setup` to choose, or `kg setup --yes [--only KEY,…]`.")
        return 0
    if not assume_yes and not sys.stdin.isatty():
        print("\nNo terminal to ask in: rerun with --yes (and --only KEY,… for a subset).")
        return 1
    failed = 0
    print()
    for step, _, detail in offered:
        if step.opt_in and assume_yes and not (only and step.key in only):
            print(f"  – {step.group}: {step.title} is opt-in: `kg setup --yes --only {step.key}`")
            continue
        if not confirm(f"{step.group}: {step.title}", assume_yes, default=not step.opt_in):
            continue
        try:
            print(f"  ✓ {step.apply(ctx)}")
        except Exception as exc:
            failed += 1
            print(f"  ✗ {step.key}: {exc}")
    if ctx.backups.exists():
        print(f"\nBackups of every changed file: {ctx.backups}")
    for step, detail in manual:
        print(f"Still for you: {step.group}: {detail}")
    return 1 if failed else 0


def confirm(question: str, assume_yes: bool, default: bool = True) -> bool:
    if assume_yes:
        return True
    answer = input(f"{question}? {'[Y/n]' if default else '[y/N]'} ").strip().lower()
    return answer in ("y", "yes") or (default and answer == "")


def update(after_upgrade: bool) -> int:
    """Upgrade kg the way it was installed, then bring the server and every
    installed plugin to its version."""
    kind = steps.install_kind()
    if not after_upgrade:
        before = kg.version()
        upgrade = {"uv": ["uv", "tool", "upgrade", "kg-memory"],
                   "pipx": ["pipx", "upgrade", "kg-memory"]}.get(kind)
        if upgrade:
            print(f"Upgrading kg ({kind})...", flush=True)
            try:
                steps.run(upgrade)
            except Exception as exc:
                print(f"✗ {exc}")
                return 1
            command = steps.kg_command()
            now = steps.run([command, "version"]).strip() if command else before
            if now != before:
                print(f"kg {before} → {now}", flush=True)   # execv drops unflushed output
                os.execv(command, [command, "update", "--after-upgrade"])
            print(f"kg {before} is the latest.")
        elif kind == "checkout":
            print("Development checkout: update it with git. Continuing with its current code.")
        else:
            print("Update kg the way you installed it. Continuing with this version.")
    ctx = steps.Context()
    for step in steps.STEPS:
        if step.key not in ("server", "claude-plugin", "codex-plugin", "agy-plugin"):
            continue
        state, detail = step.check(ctx)
        if state != steps.FIX:
            continue
        print(f"{step.group}: {detail}")
        try:
            print(f"  ✓ {step.apply(ctx)}")
        except Exception as exc:
            print(f"  ✗ {exc}")
    print()
    return run()


def check_update() -> int:
    """Whether now is a good moment for `kg update`: the facts and a verdict.
    Changes nothing."""
    want = steps.Context().want
    kind = steps.install_kind()
    health = kg.health() or {}
    latest = steps.latest_release()
    running = health.get("version")
    plugins = {"Claude Code": steps.find_binary("claude") and steps.claude_plugin_version(),
               "Codex": steps.codex_binary() and steps.codex_plugin_version(),
               "Antigravity": steps.find_binary("agy")
               and steps.load_json(steps.AGY_PLUGIN / ".claude-plugin/plugin.json").get("version")}
    plugins = {name: version for name, version in plugins.items() if version}
    newer = latest if latest and steps.version_key(latest) > steps.version_key(want) else None

    print("Versions")
    print(f"  {'this kg':<20} {want} ({kind} install)")
    print(f"  {'server':<20} {running or 'not running'}")
    for name, version in plugins.items():
        print(f"  {name + ' plugin':<20} {version}")
    print(f"  {'latest release':<20} {latest or 'not known yet (the server reads PyPI once a day)'}")
    behind = [n for n, v in plugins.items() if v != want] + (["server"] if running and running != want else [])
    if not newer and not behind:
        print("\nNothing to update.")
        return 0

    print("\nWhat it does")
    if newer and kind == "checkout":
        print(f"  this is a development checkout: `git pull` brings {newer}; `kg update` then aligns the rest")
    elif newer and kind not in ("uv", "pipx"):
        print(f"  kg was not installed with uv or pipx: upgrade it the way you installed it to reach {newer}")
    elif newer:
        print(f"  upgrades kg to {newer} ({kind}), restarts the server, updates each plugin")
    else:
        print(f"  brings {', '.join(behind)} to {want}")

    later, unknown = [], False
    print("\nA server restart interrupts")
    if not running:
        print("  nothing: the server is not running")
    else:
        recent = health.get("recent_sessions")
        if recent is None:
            print(f"  unknown: the running server ({running}) is too old to say")
            unknown = True
        else:
            print(f"  {recent} session(s) active in the last 10 minutes, the asking one included; "
                  "each reconnects on its next kg call, and a write sent during the restart fails")
            if recent > 1:
                later.append(f"{recent - 1} other session(s) are working")
        if health.get("chore_running"):
            print("  a maintenance run in progress, which loses its server")
            later.append("a maintenance run is in progress")
        if health.get("unsaved_graphs"):
            print(f"  nothing lost: {health['unsaved_graphs']} graph(s) with pending writes are saved on stop")

    root = kg.STORAGE_ROOT
    print("\nStorage")
    if (root / ".git").is_dir():
        print(f"  {root} is versioned with git (`kg stop` commits pending changes): a point to return to")
    else:
        print(f"  {root} has no version history: consider `git init` there first "
              "(README: Versioned history and external backups)")

    print("\nAfterwards")
    changing = set(plugins) if newer else set(behind)
    if "Claude Code" in changing:
        print("  restart Claude Code sessions to load the new plugin")
    if "Codex" in changing:
        print("  Codex asks to trust the changed hooks again: run /hooks in Codex")
    if "Antigravity" in changing:
        print("  start a new Antigravity conversation")
    if not changing & set(plugins):
        print("  nothing: the plugins are current")

    if later:
        print(f"\nVerdict: better later: {'; '.join(later)}.")
    elif unknown:
        print("\nVerdict: safe as far as kg can tell; ask whether other sessions are working.")
    else:
        print("\nVerdict: safe now.")
    return 0


def uninstall(assume_yes: bool, plan: bool) -> int:
    ctx = steps.Context()
    undo = [(s, p) for s in reversed(steps.STEPS) if (p := s.undo_plan(ctx))]
    kind = steps.install_kind()
    print(f"Your memory stays in {kg.STORAGE_ROOT}: uninstall never touches it.")
    if not undo:
        print("Nothing set up by kg to reverse.")
    else:
        print("\nUninstall would:")
        for step, text in undo:
            print(f"  [{step.key}] {text}")
        if plan:
            return 0
        if not assume_yes and not sys.stdin.isatty():
            print("\nNo terminal to ask in: rerun with --yes.")
            return 1
        print()
        for step, text in undo:
            if not confirm(text[0].upper() + text[1:], assume_yes):
                continue
            try:
                print(f"  ✓ {step.undo(ctx)}")
            except Exception as exc:
                print(f"  ✗ {step.key}: {exc}")
        if ctx.backups.exists():
            print(f"\nBackups of every changed file: {ctx.backups}")
    remove = {"uv": "uv tool uninstall kg-memory", "pipx": "pipx uninstall kg-memory"}.get(kind)
    if remove:
        print(f"Last step, the kg command itself: {remove}")
    return 0
