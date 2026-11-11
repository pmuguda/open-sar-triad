"""
Tests for scripts/git_push_retry.sh, against real git repositories.

The data workflows used a bare `git push`. Two of them commit to main on their
own schedules and people push there too, so sooner or later a push lands in
the gap between a run's checkout and its push, the push is rejected, and the
run's work is lost. Nothing about that can be checked by reading YAML, so
these build a remote and two clones in a temp directory and race them.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "git_push_retry.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")

ENV = {**os.environ,
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
       "PUSH_BACKOFF": "0"}


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=check,
                          capture_output=True, text=True)


def commit(repo, name, text, message):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


def push_retry(repo, **env):
    return subprocess.run(["bash", str(SCRIPT), "origin", "main"], cwd=repo,
                          env={**ENV, **env}, capture_output=True, text=True)


@pytest.fixture
def world(tmp_path):
    """A bare remote with one commit, and two clones of it.

    `runner` is cloned shallow over file://, the way actions/checkout leaves a
    workflow's checkout, because rebasing in a depth-1 clone is the case that
    has to work."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(remote), str(seed))
    git(seed, "checkout", "-q", "-b", "main")
    commit(seed, "scenes.geojson", "v1\n", "catalog v1")
    commit(seed, "usage.json", "u1\n", "usage v1")
    git(seed, "push", "-q", "origin", "main")

    runner = tmp_path / "runner"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{remote}", str(runner))
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    return remote, runner, other


def remote_log(remote):
    return git(remote, "log", "--format=%s", "main").stdout.split("\n")


def test_a_plain_push_just_works(world):
    remote, runner, _ = world
    commit(runner, "scenes.geojson", "v2\n", "catalog v2")
    r = push_retry(runner)
    assert r.returncode == 0, r.stderr
    assert remote_log(remote)[0] == "catalog v2"


def test_a_push_that_lost_the_race_is_rebased_and_lands(world):
    """The usage workflow pushes while the catalog run is still working."""
    remote, runner, other = world
    commit(other, "usage.json", "u2\n", "usage v2")
    git(other, "push", "-q", "origin", "main")

    commit(runner, "scenes.geojson", "v2\n", "catalog v2")
    assert git(runner, "push", "origin", "HEAD:main", check=False).returncode != 0, \
        "the fixture is not racing: a bare push should have been rejected"

    r = push_retry(runner)
    assert r.returncode == 0, r.stderr
    log = remote_log(remote)
    assert log[0] == "catalog v2" and "usage v2" in log, log
    assert "rebasing" in r.stderr


def test_neither_sides_work_is_lost(world):
    remote, runner, other = world
    commit(other, "usage.json", "u2\n", "usage v2")
    git(other, "push", "-q", "origin", "main")
    commit(runner, "scenes.geojson", "v2\n", "catalog v2")
    assert push_retry(runner).returncode == 0
    check = world[0].parent / "check"
    git(remote.parent, "clone", "-q", str(remote), str(check))
    assert (check / "scenes.geojson").read_text() == "v2\n"
    assert (check / "usage.json").read_text() == "u2\n"


def test_a_real_conflict_is_reported_not_retried(world):
    """Both sides changed the same file. Retrying cannot help, and forcing
    would throw away someone's work."""
    remote, runner, other = world
    commit(other, "scenes.geojson", "theirs\n", "someone else's catalog")
    git(other, "push", "-q", "origin", "main")
    commit(runner, "scenes.geojson", "ours\n", "catalog v2")

    r = push_retry(runner)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "conflicts" in r.stderr
    assert remote_log(remote)[0] == "someone else's catalog", "the remote must be untouched"


def test_a_conflict_leaves_the_checkout_clean(world):
    """A half-finished rebase would break every later step of the run."""
    _, runner, other = world
    commit(other, "scenes.geojson", "theirs\n", "someone else's catalog")
    git(other, "push", "-q", "origin", "main")
    commit(runner, "scenes.geojson", "ours\n", "catalog v2")
    push_retry(runner)
    assert not (runner / ".git" / "rebase-merge").exists()
    assert not (runner / ".git" / "rebase-apply").exists()
    assert git(runner, "status", "--porcelain").stdout == ""
    assert (runner / "scenes.geojson").read_text() == "ours\n"


def test_an_unreachable_remote_gives_up_with_its_own_exit_code(world):
    _, runner, _ = world
    commit(runner, "scenes.geojson", "v2\n", "catalog v2")
    git(runner, "remote", "set-url", "origin", str(runner / "nowhere.git"))
    r = push_retry(runner, PUSH_ATTEMPTS="2")
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "after 2 attempt" in r.stderr


def test_it_never_force_pushes():
    text = SCRIPT.read_text()
    code = "\n".join(l.split("#", 1)[0] for l in text.splitlines())
    assert "--force" not in code and " -f" not in code and "+HEAD" not in code
