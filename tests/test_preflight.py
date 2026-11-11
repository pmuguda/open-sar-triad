"""
scripts/preflight.sh must do what CI does, or it is a comfort rather than a check.

Five of this repository's ten red runs were the test workflow failing straight
after a push that had passed locally, in an environment with more installed
than CI's. The preflight script exists to make "passed locally" mean "will
pass in CI", which only holds while the two install and run the same things.
These compare the script against the workflow, so changing one without the
other fails here.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
PREFLIGHT = (ROOT / "scripts" / "preflight.sh").read_text()
HOOK = ROOT / ".githooks" / "pre-push"
TESTS_YML = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())


def ci_step(job, name):
    return next(s for s in TESTS_YML["jobs"][job]["steps"] if s.get("name") == name)


def test_it_installs_exactly_what_the_test_job_installs():
    install = ci_step("test", "Install dependencies")["run"]
    for line in install.splitlines():
        line = line.strip()
        if line.startswith("pip install") and "--upgrade pip" not in line:
            wanted = line.replace("pip install ", "")
            assert wanted in PREFLIGHT, f"CI runs `{line}`; preflight does not install {wanted}"
    assert "requirements.txt" not in PREFLIGHT.replace("requirements-dev.txt", ""), \
        "the pipeline's requirements are not installed in CI's test job; installing " \
        "them here is exactly the difference this script exists to remove"


def test_it_runs_the_tests_with_the_flags_ci_uses():
    step = ci_step("test", "Run tests")
    for flag in step["run"].replace("pytest", "").split():
        assert flag in PREFLIGHT, f"CI passes {flag} to pytest; preflight does not"
    for name, value in (step.get("env") or {}).items():
        assert f"{name}={value}" in PREFLIGHT, f"CI sets {name}={value}; preflight does not"


def test_it_covers_the_build_api_job():
    for step in TESTS_YML["jobs"]["build-api"]["steps"]:
        for script in ("scripts/validate_catalog.py", "scripts/build_api.py"):
            if script in step.get("run", ""):
                assert script in PREFLIGHT, f"CI runs {script}; preflight does not"


def test_it_stops_at_the_first_failure():
    assert "set -euo pipefail" in PREFLIGHT


def test_the_hook_and_the_script_are_executable():
    assert os.access(ROOT / "scripts" / "preflight.sh", os.X_OK)
    assert os.access(HOOK, os.X_OK)
    assert os.access(ROOT / "scripts" / "git_push_retry.sh", os.X_OK)


@pytest.fixture
def hook_repo(tmp_path):
    """A repository whose preflight script is a stub that records being run
    and exits with whatever the test asks for."""
    if shutil.which("git") is None or shutil.which("bash") is None:
        pytest.skip("needs git and bash")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    stub = repo / "scripts" / "preflight.sh"
    stub.write_text('#!/bin/sh\necho ran >> "$(dirname "$0")/../ran.log"\nexit "${STUB_EXIT:-0}"\n')
    stub.chmod(0o755)

    def push(remote_ref, **extra):
        return subprocess.run(
            ["bash", str(HOOK), "origin", "https://example.invalid/x.git"],
            input=f"refs/heads/work abc123 {remote_ref} def456\n",
            cwd=repo, env={**env, **extra}, capture_output=True, text=True)
    push.ran = lambda: (repo / "ran.log").exists()
    return push


def test_a_push_to_main_runs_the_preflight(hook_repo):
    assert hook_repo("refs/heads/main").returncode == 0
    assert hook_repo.ran()


def test_a_failing_preflight_stops_the_push(hook_repo):
    r = hook_repo("refs/heads/main", STUB_EXIT="1")
    assert r.returncode == 1
    assert "CI would have failed too" in r.stderr


def test_a_push_to_another_branch_is_not_held_up(hook_repo):
    assert hook_repo("refs/heads/experiment", STUB_EXIT="1").returncode == 0
    assert not hook_repo.ran()


def test_the_hook_can_be_skipped_deliberately(hook_repo):
    r = hook_repo("refs/heads/main", STUB_EXIT="1", OST_SKIP_PREFLIGHT="1")
    assert r.returncode == 0 and not hook_repo.ran()
    assert "skipped" in r.stderr
