"""Reset the demo fixture so the loop is re-runnable.

After an autonomous auto-fix *merges*, master's pool config is fixed
(POOL_SIZE raised) and the integration test passes — there's genuinely
nothing left to fix, so a new "Run Pipeline" would be green (correct, but
not demo-able). This script restores the failing fixture (POOL_SIZE=5,
MAX_OVERFLOW=0) on master so the demo can run again.

It's a fixture reset, clearly labeled — not part of the AI flow. Pushes to
the gitlab remote only (no pipeline is triggered; pushes are excluded by
the workflow rule).

Usage:
    python scripts/reset_fixture.py
"""
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
POOL = os.path.join(REPO, "app", "db", "pool.py")
FAILING = {"POOL_SIZE": "5", "MAX_OVERFLOW": "0", "EXPECTED_WORKERS": "6"}


def _git(args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def main():
    if not os.path.isdir(os.path.join(REPO, ".git")):
        print("no git repo at", REPO)
        return 1
    # Sync with the gitlab remote first (an auto-fix merge may have advanced it).
    remotes = _git(["remote", "-v"]).stdout
    gitlab_remote = "origin"
    for line in remotes.splitlines():
        name = line.split("\t")[0]
        url = line.split("\t")[1] if "\t" in line else ""
        if "18929" in url or name == "gitlab":
            gitlab_remote = name
            break
    _git(["fetch", gitlab_remote, "master"])
    r = _git(["rev-list", "--count", "HEAD..gitlab/master"])
    ahead = r.stdout.strip()
    # Stash any uncommitted work (including untracked) so rebase can proceed,
    # then restore it afterwards.
    dirty = _git(["status", "--porcelain"]).stdout.strip() != ""
    if dirty:
        s = _git(["stash", "push", "-u", "--include-untracked", "-m", "reset_fixture auto-stash"])
        if s.returncode != 0:
            print("could not stash uncommitted changes:", s.stderr)
            return 1
    if ahead.isdigit() and int(ahead) > 0:
        b = _git(["rebase", f"{gitlab_remote}/master"])
        if b.returncode != 0:
            print("rebase onto gitlab/master failed — resolve conflicts, then re-run:", b.stderr)
            _git(["rebase", "--abort"])
            if dirty:
                _git(["stash", "pop"])
            return 1
        print(f"rebased local master onto {gitlab_remote}/master ({ahead} new commit(s))")
    if dirty:
        _git(["stash", "pop"])
    with open(POOL) as f:
        content = f.read()
    changed = []
    for key, val in FAILING.items():
        pat = rf"^{key}\s*=\s*\d+"
        repl = f"{key} = {val}"
        if not re.search(rf"^{key}\s*=\s*{val}\b", content, re.M):
            content = re.sub(pat, repl, content, flags=re.M)
            changed.append(key)
    if not changed:
        print("fixture already at failing values; nothing to do")
        return 0
    with open(POOL, "w") as f:
        f.write(content)
    _git(["add", "app/db/pool.py"])
    _git(["commit", "-m", f"chore: reset demo fixture to failing pool config ({', '.join(changed)})"])
    p = _git(["push", gitlab_remote, "HEAD:refs/heads/master"])
    if p.returncode != 0:
        print("push failed:", p.stderr)
        return 1
    print(f"fixture reset (POOL_SIZE=5, MAX_OVERFLOW=0); demo is re-runnable. pushed to {gitlab_remote}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
