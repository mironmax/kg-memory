"""Run the reproductions and check each against its expected outcome (repros.txt).

The reproductions drive the real server code, so this is the drift guard
between the formal findings and the code: a change that brings back a fixed
finding, or that closes an open one, changes an outcome and fails the run.

A run fails when it exits non-zero, times out, misses an expected line, or
prints a FAIL or BUG verdict line that no expectation names.

usage: python3 check_repros.py [--python PY] [--timeout S] [FILTER...]
  --python   interpreter with the server's requirements (default: this one)
  FILTER     run only the commands containing one of these substrings
Writes a Markdown table to $GITHUB_STEP_SUMMARY when it is set.
"""
import argparse, os, re, shlex, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERDICT = re.compile(r"^\s*(FAIL|BUG)\b|^\s*RESULT\b.*\bFAIL\b")


def load(path):
    runs = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cmd, *expects = [part.strip() for part in line.split("=>")]
        if not expects:
            sys.exit(f"{path.name}:{n}: no expected outcome after '=>'")
        runs.append((cmd, [re.compile(e) for e in expects]))
    return runs


def check(cmd, expects, python, timeout):
    """(ok, seconds, problems, output) for one run."""
    start = time.monotonic()
    try:
        p = subprocess.run([python, *shlex.split(cmd)], cwd=HERE, capture_output=True,
                           text=True, timeout=timeout, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        out, rc = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return False, time.monotonic() - start, [f"timed out after {timeout} s"], out
    secs = time.monotonic() - start
    lines = out.splitlines()
    problems = [] if rc == 0 else [f"exit code {rc}"]
    problems += [f"missing: {e.pattern}" for e in expects if not any(e.search(l) for l in lines)]
    problems += [f"unexpected: {l.strip()}" for l in lines
                 if VERDICT.match(l) and not any(e.search(l) for e in expects)]
    return not problems, secs, problems, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("filters", nargs="*")
    a = ap.parse_args()
    runs = [r for r in load(HERE / "repros.txt") if not a.filters or any(f in r[0] for f in a.filters)]
    rows, failed = [], 0
    for cmd, expects in runs:
        ok, secs, problems, out = check(cmd, expects, a.python, a.timeout)
        print(f"{'ok  ' if ok else 'FAIL'} {secs:6.1f}s  {cmd}", flush=True)
        if not ok:
            failed += 1
            for pr in problems:
                print(f"        {pr}")
            print("        last lines of output:")
            for l in out.splitlines()[-20:]:
                print(f"          {l}")
        rows.append((ok, secs, cmd, "; ".join(problems)))
    print(f"\n{len(runs) - failed} of {len(runs)} runs as expected")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Reproductions: {len(runs) - failed} of {len(runs)} as expected\n\n"
                    "| | s | run | problem |\n|---|---|---|---|\n")
            for ok, secs, cmd, pr in rows:
                f.write(f"| {'✓' if ok else '✗'} | {secs:.1f} | `{cmd}` | {pr} |\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
