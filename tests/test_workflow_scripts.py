"""
Runs the shell that the workflows run.

Reading a workflow file can show that a step exists and is wired to the right
ids. It cannot show that the step's script does the right thing, and the
scripts are where the decisions are: whether a failed deploy retries or gives
up, which script a dead ingest blames, whether a catalog that could not be
pushed is reported or forgotten. Before this file those few dozen lines of
bash and inline Python had only ever been executed by GitHub, on the day.

Each test lifts a step's `run` block out of the YAML, fills in the
`${{ ... }}` expressions with the values under test, and executes it with
bash in a scratch directory. `gh` is a stub that records how it was called,
and /tmp is redirected, so nothing here touches GitHub or the machine.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
WF = ROOT / ".github" / "workflows"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def find_step(workflow, *, step_id=None, name=None, contains=None):
    doc = yaml.safe_load((WF / workflow).read_text())
    for job in doc["jobs"].values():
        for s in job.get("steps") or []:
            if step_id and s.get("id") == step_id:
                return s
            if name and s.get("name") == name:
                return s
            if contains and contains in s.get("run", ""):
                return s
    raise LookupError(f"no such step in {workflow}")


class Sandbox:
    """A scratch directory with a stub `gh`, a redirected /tmp and a fake
    GITHUB_OUTPUT, in which a step's script can be run for real."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "work"
        self.dir.mkdir()
        self.tmp = tmp_path / "tmp"
        self.tmp.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.gh_log = tmp_path / "gh.log"
        self.output = tmp_path / "github_output"
        self.output.write_text("")
        self.gh_exit = 0
        self._write_gh()
        # `python` in the scripts must be this interpreter, whatever the PATH.
        py = self.bin / "python"
        py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        py.chmod(py.stat().st_mode | stat.S_IEXEC)

    def _write_gh(self):
        gh = self.bin / "gh"
        gh.write_text(f'#!/bin/sh\necho "$@" >> "{self.gh_log}"\nexit {self.gh_exit}\n')
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)

    def gh_fails(self):
        self.gh_exit = 1
        self._write_gh()

    def gh_calls(self):
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def outputs(self):
        return dict(l.split("=", 1) for l in self.output.read_text().splitlines() if "=" in l)

    def run(self, step, expressions=None, env=None, cwd=None):
        """Execute a step's script. Every ${{ }} expression in it must be
        given a value, so a new one cannot go untested by accident."""
        script = step["run"]
        expressions = expressions or {}

        def fill(m):
            key = m.group(1).strip()
            assert key in expressions, f"the test gives no value for ${{{{ {key} }}}}"
            return str(expressions[key])
        script = re.sub(r"\$\{\{(.*?)\}\}", fill, script)
        script = script.replace("/tmp/", f"{self.tmp}/")

        full_env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.dir),
            "GITHUB_OUTPUT": str(self.output),
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        }
        for k, v in (step.get("env") or {}).items():
            v = str(v)
            m = re.fullmatch(r"\$\{\{(.*?)\}\}", v.strip())
            if m:
                key = m.group(1).strip()
                if key.startswith("secrets."):
                    continue                  # never set: reporting then skips itself
                assert key in expressions, f"the test gives no value for env {k} = {v}"
                v = str(expressions[key])
            full_env[k] = v
        full_env.update(env or {})
        # GitHub's default shell for `run` is `bash -e`.
        return subprocess.run(["bash", "-e", "-c", script], cwd=cwd or self.dir,
                              env=full_env, capture_output=True, text=True)

    def read(self, name):
        return json.loads((self.tmp / name).read_text())


@pytest.fixture
def box(tmp_path):
    return Sandbox(tmp_path)


# --------------------------------------------------------------------------- #
# deploy.yml: retry a failed Pages deployment in a fresh run
# --------------------------------------------------------------------------- #
RETRY = find_step("deploy.yml", step_id="retry")


def retry(box, attempt, max_attempts="3"):
    return box.run(RETRY, {"inputs.attempt": attempt, "secrets.GITHUB_TOKEN": "x"},
                   env={"MAX_ATTEMPTS": max_attempts})


def test_a_first_failure_starts_attempt_two(box):
    """A push-triggered run has no `attempt` input at all, so it arrives empty."""
    r = retry(box, "")
    assert r.returncode == 0, r.stderr
    assert box.gh_calls() == ["workflow run deploy.yml --ref main -f attempt=2"]
    assert box.outputs()["retrying"] == "true"


def test_the_attempt_number_counts_up(box):
    retry(box, "2")
    assert box.gh_calls() == ["workflow run deploy.yml --ref main -f attempt=3"]


def test_the_last_attempt_gives_up_and_lets_the_report_through(box):
    r = retry(box, "3")
    assert r.returncode == 0, r.stderr
    assert box.gh_calls() == [], "it must not start a fourth run"
    assert box.outputs()["retrying"] == "false"
    assert "::error::" in r.stdout


def test_the_retry_cannot_run_away(box):
    """Whatever the input, the chain ends: no value may dispatch beyond the cap."""
    for attempt in ("3", "4", "99", "100000"):
        retry(box, attempt)
    assert box.gh_calls() == []


@pytest.mark.parametrize("junk", ["abc", "2; echo pwned", "-1", "1.5", " ", "$(id)"])
def test_a_malformed_attempt_is_treated_as_the_first(box, junk):
    """The input is a free-text field on a manual dispatch. It goes into shell
    arithmetic, so anything that is not a plain number is discarded rather than
    evaluated."""
    r = retry(box, junk)
    assert r.returncode == 0, r.stderr
    assert box.gh_calls() == ["workflow run deploy.yml --ref main -f attempt=2"]
    assert "pwned" not in r.stdout and "uid=" not in r.stdout


def test_if_the_retry_cannot_be_started_the_failure_is_reported_instead(box):
    """`retrying` must not read true when no retry exists, or the report step
    would stay silent about a deploy that failed and was never retried."""
    box.gh_fails()
    r = retry(box, "1")
    assert r.returncode != 0
    assert box.outputs().get("retrying") != "true"


# --------------------------------------------------------------------------- #
# deploy.yml: the report written when the deployment produced none
# --------------------------------------------------------------------------- #
DEPLOY_REPORT = find_step("deploy.yml", name="Report deploy health")


def test_a_failed_deployment_is_reported_as_one(box):
    r = box.run(DEPLOY_REPORT, {"steps.deployment.outcome": "failure"},
                env={"MAX_ATTEMPTS": "3"}, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    rep = box.read("deploy-health.json")
    assert rep["ok"] is False and rep["subject"] == "Site deploy"
    assert "Pages deployment itself failed 3 times" in rep["errors"][0]


def test_a_verifier_that_left_no_report_is_blamed_not_the_deployment(box):
    box.run(DEPLOY_REPORT, {"steps.deployment.outcome": "success"},
            env={"MAX_ATTEMPTS": "3"}, cwd=ROOT)
    assert "verify_deploy.py did not produce a report" in box.read("deploy-health.json")["errors"][0]


def test_an_existing_report_is_passed_through_untouched(box):
    good = {"ok": True, "errors": [], "warnings": [], "subject": "Site deploy"}
    (box.tmp / "deploy-health.json").write_text(json.dumps(good))
    r = box.run(DEPLOY_REPORT, {"steps.deployment.outcome": "success"},
                env={"MAX_ATTEMPTS": "3"}, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert box.read("deploy-health.json") == good


# --------------------------------------------------------------------------- #
# fetch-data.yml: the report for an ingest that died, or could not publish
# --------------------------------------------------------------------------- #
INGEST_REPORT = find_step("fetch-data.yml", name="Report ingest health")
OUTCOMES = {"steps.fetch.outcome": "success", "steps.classify.outcome": "success",
            "steps.validate.outcome": "success", "steps.commit.outcome": "success",
            "steps.deploy.outcome": "success"}
HEALTHY = {"ok": True, "errors": [], "warnings": [], "total": 15040}


def ingest_report(box, existing=None, **outcomes):
    if existing is not None:
        (box.tmp / "validation.json").write_text(json.dumps(existing))
    values = {**OUTCOMES, **{f"steps.{k}.outcome": v for k, v in outcomes.items()}}
    r = box.run(INGEST_REPORT, values, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    return box.read("validation.json")


@pytest.mark.parametrize("dead, blamed", [
    ({"fetch": "failure", "classify": "skipped", "validate": "skipped"}, "fetch_catalog.py failed"),
    ({"classify": "failure", "validate": "skipped"}, "classify_scenes.py failed"),
    ({"validate": "failure"}, "validate_catalog.py did not produce a report"),
])
def test_a_dead_ingest_blames_the_step_that_died(box, dead, blamed):
    rep = ingest_report(box, **dead, commit="skipped", deploy="skipped")
    assert rep["ok"] is False
    assert blamed in rep["errors"][0], rep["errors"]


def test_a_healthy_ingest_is_reported_as_it_was(box):
    assert ingest_report(box, existing=HEALTHY) == HEALTHY


def test_a_catalog_that_could_not_be_pushed_is_not_reported_healthy(box):
    """Validation passed, so the report on disk says ok. Without this the run
    would be red and the issue would close itself as "healthy again"."""
    rep = ingest_report(box, existing=HEALTHY, commit="failure", deploy="skipped")
    assert rep["ok"] is False
    assert "could not be committed and pushed" in rep["errors"][0]
    assert "was not committed" in rep["on_error_note"]
    assert rep["total"] == 15040, "the rest of the report must survive"


def test_a_catalog_that_was_pushed_but_not_deployed_is_an_error(box):
    rep = ingest_report(box, existing=HEALTHY, deploy="failure")
    assert rep["ok"] is False
    assert "dispatching deploy.yml failed" in rep["errors"][0]
    assert "was committed" in rep["on_error_note"]


def test_a_week_with_nothing_to_commit_is_not_an_error(box):
    """No change means the deploy step is skipped, which is not a failure."""
    assert ingest_report(box, existing=HEALTHY, deploy="skipped")["ok"] is True


# --------------------------------------------------------------------------- #
# fetch-usage.yml: the report
# --------------------------------------------------------------------------- #
USAGE_REPORT = find_step("fetch-usage.yml", name="Report usage health")


def usage_report(box, fetch="success", commit="success", deploy="success"):
    r = box.run(USAGE_REPORT, {"steps.fetch.outcome": fetch, "steps.commit.outcome": commit,
                               "steps.deploy.outcome": deploy}, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    return box.read("usage-health.json")


def test_a_good_usage_run_is_healthy(box):
    rep = usage_report(box)
    assert rep["ok"] is True and rep["errors"] == [] and rep["subject"] == "Usage statistics"


def test_a_day_with_no_new_statistics_is_healthy(box):
    """Nothing changed, so nothing was pushed and the deploy step was skipped."""
    assert usage_report(box, deploy="skipped")["ok"] is True


def test_a_failed_statistics_fetch_is_reported(box):
    rep = usage_report(box, fetch="failure", commit="skipped", deploy="skipped")
    assert rep["ok"] is False and len(rep["errors"]) == 1
    assert "fetch_gsc.py" in rep["errors"][0]


def test_a_failed_usage_push_is_reported(box):
    rep = usage_report(box, commit="failure", deploy="skipped")
    assert "committing and pushing" in rep["errors"][0]


# --------------------------------------------------------------------------- #
# The commit steps, against a real repository
# --------------------------------------------------------------------------- #
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "PUSH_BACKOFF": "0"}


def git(cwd, *args):
    env = {**os.environ, **GIT_ENV, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def checkout(tmp_path):
    """A remote and a shallow checkout of it laid out like the repository:
    data/ files to commit and scripts/git_push_retry.sh to push with."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(remote), str(seed))
    git(seed, "checkout", "-q", "-b", "main")
    (seed / "data").mkdir()
    (seed / "scripts").mkdir()
    (seed / "data" / "scenes.geojson").write_text("v1\n")
    (seed / "data" / "usage.json").write_text("u1\n")
    shutil.copy(ROOT / "scripts" / "git_push_retry.sh", seed / "scripts" / "git_push_retry.sh")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "main")
    work = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{remote}", str(work))
    return remote, work, seed


@pytest.mark.parametrize("workflow, path, message", [
    ("fetch-data.yml", "data/scenes.geojson", "chore: update SAR scene data"),
    ("fetch-usage.yml", "data/usage.json", "chore: update usage stats"),
])
def test_a_changed_file_is_committed_and_pushed(box, checkout, workflow, path, message):
    remote, work, _ = checkout
    (work / path).write_text("new\n")
    r = box.run(find_step(workflow, step_id="commit"), env=GIT_ENV, cwd=work)
    assert r.returncode == 0, r.stderr
    assert box.outputs()["pushed"] == "true"
    assert message in git(remote, "log", "-1", "--format=%s", "main")
    assert git(remote, "show", f"main:{path}") == "new\n"


@pytest.mark.parametrize("workflow", ["fetch-data.yml", "fetch-usage.yml"])
def test_an_unchanged_file_pushes_nothing_and_says_so(box, checkout, workflow):
    """`pushed=false` is what keeps the deploy from being dispatched for nothing."""
    remote, work, _ = checkout
    r = box.run(find_step(workflow, step_id="commit"), env=GIT_ENV, cwd=work)
    assert r.returncode == 0, r.stderr
    assert box.outputs()["pushed"] == "false"
    assert git(remote, "log", "--format=%s", "main").strip() == "seed"


def test_the_ingest_commit_survives_main_moving_underneath_it(box, checkout):
    """The whole point of the retrying push, through the real commit step."""
    remote, work, seed = checkout
    (seed / "data" / "usage.json").write_text("u2\n")
    git(seed, "commit", "-qam", "usage moved main")
    git(seed, "push", "-q", "origin", "main")

    (work / "data" / "scenes.geojson").write_text("v2\n")
    r = box.run(find_step("fetch-data.yml", step_id="commit"), env=GIT_ENV, cwd=work)
    assert r.returncode == 0, r.stderr
    assert box.outputs()["pushed"] == "true"
    assert git(remote, "show", "main:data/scenes.geojson") == "v2\n"
    assert git(remote, "show", "main:data/usage.json") == "u2\n"


def test_a_push_that_fails_does_not_claim_to_have_pushed(box, checkout):
    """Otherwise the deploy would be dispatched for a commit that is not there."""
    _, work, _ = checkout
    (work / "data" / "scenes.geojson").write_text("v2\n")
    git(work, "remote", "set-url", "origin", str(work / "nowhere.git"))
    r = box.run(find_step("fetch-data.yml", step_id="commit"),
                env={**GIT_ENV, "PUSH_ATTEMPTS": "1"}, cwd=work)
    assert r.returncode != 0
    assert box.outputs().get("pushed") != "true"


def test_the_usage_commit_does_nothing_when_no_statistics_were_produced(box, tmp_path):
    """The Search Console key is optional; without it there is no file at all."""
    r = box.run(find_step("fetch-usage.yml", step_id="commit"), env=GIT_ENV)
    assert r.returncode == 0, r.stderr
    assert box.outputs()["pushed"] == "false"


# --------------------------------------------------------------------------- #
# site-check.yml
# --------------------------------------------------------------------------- #
def test_the_site_check_asks_the_remote_for_the_tip_of_main(box, checkout):
    remote, work, _ = checkout
    r = box.run(find_step("site-check.yml", step_id="tip"), cwd=work)
    assert r.returncode == 0, r.stderr
    assert box.outputs()["sha"] == git(remote, "rev-parse", "main").strip()


def test_the_site_check_stops_if_it_cannot_find_the_tip(box, checkout):
    """An empty sha would be compared with the site and "mismatch" for ever."""
    _, work, _ = checkout
    git(work, "remote", "set-url", "origin", str(work / "nowhere.git"))
    r = box.run(find_step("site-check.yml", step_id="tip"), cwd=work)
    assert r.returncode != 0
    assert "sha" not in box.outputs()


def test_the_repair_starts_a_deploy(box):
    r = box.run(find_step("site-check.yml", step_id="heal"), {"secrets.GITHUB_TOKEN": "x"})
    assert r.returncode == 0, r.stderr
    assert box.gh_calls() == ["workflow run deploy.yml --ref main"]
