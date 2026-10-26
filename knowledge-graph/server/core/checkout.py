"""Linked git worktrees count as their main checkout.

A session in `~/work/app-fix` (a `git worktree add` of `~/work/app`) belongs to
the `app` project: same graph, same project-relative file keys. Without this the
project would be named after the worktree folder and start empty.

Read from the files git writes, never by running git: a linked worktree has a
`.git` FILE naming its private git dir, and that dir holds a `commondir` file
pointing at the shared one. A submodule also has a `.git` file, but its git dir
has no `commondir`, so it stays its own project. A worktree of a bare
repository has no main checkout and stays as it is.

Every file read stays inside the home folder, where project roots must be
(constants.safe_project_path): the walk up stops there, and a `.git` file or
`commondir` pointing outside it is not followed.
"""

import os


def _inside(path: str, root: str) -> bool:
    # constants.path_within, which imports this module.
    try:
        return os.path.commonpath([path, root]) == os.path.normpath(root)
    except ValueError:
        return False


def _linked_worktree(real: str, home: str) -> tuple[str, str] | None:
    """(worktree top, main checkout top) when `real` lies in a linked worktree."""
    top = real
    while True:
        if not _inside(top, home):
            return None
        dot_git = os.path.join(top, ".git")
        if os.path.isdir(dot_git):
            return None                # a main checkout, or a plain repository
        if os.path.isfile(dot_git):
            break
        parent = os.path.dirname(top)
        if parent == top:
            return None                # not inside any checkout
        top = parent
    try:
        with open(dot_git, encoding="utf-8") as f:
            line = f.readline().strip()
        if not line.startswith("gitdir:"):
            return None
        git_dir = os.path.realpath(os.path.join(top, line[len("gitdir:"):].strip()))
        if not _inside(git_dir, home):
            return None
        with open(os.path.join(git_dir, "commondir"), encoding="utf-8") as f:
            common = os.path.realpath(os.path.join(git_dir, f.readline().strip()))
    except OSError:
        return None                    # no commondir: a submodule, or a broken link
    if not _inside(common, home):
        return None
    if os.path.basename(common) != ".git":
        return None                    # a bare repository has no main checkout
    main = os.path.dirname(common)
    if not os.path.isdir(main) or main == top:
        return None
    return top, main


def main_checkout_path(path: str) -> str:
    """The same place in the main checkout when `path` lies in a linked
    worktree, else `path` itself. Both are real paths."""
    real = os.path.realpath(path)
    linked = _linked_worktree(real, os.path.realpath(os.path.expanduser("~")))
    if linked is None:
        return real
    top, main = linked
    rel = os.path.relpath(real, top)
    return main if rel == "." else os.path.join(main, rel)
