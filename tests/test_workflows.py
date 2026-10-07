"""
The workflows must keep reporting their own health.

Every automated check in this repository has, at some point, written its finding
somewhere nobody looks. The reporters are the correction, so a reporter being
quietly deleted or rewired is itself a regression worth failing on — and the
test workflow going red for two days without anyone noticing is exactly what
these guard against.

This reads the YAML rather than running it. It cannot prove the reporters work,
only that they are still wired in; the running is proven elsewhere.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parent.parent
WF = ROOT / ".github" / "workflows"


def load(name):
    return yaml.safe_load((WF / name).read_text())


# PyYAML parses a bare `on:` key as the boolean True, so accept either.
def triggers(doc):
    return doc.get("on", doc.get(True, {}))


@pytest.mark.parametrize("workflow, label", [
    ("fetch-data.yml", "catalog-health"),
    ("deploy.yml", "site-health"),
    ("tests.yml", "tests-health"),
])
def test_each_workflow_reports_its_health(workflow, label):
    text = (WF / workflow).read_text()
    assert "report_health.py" in text, f"{workflow} no longer reports its health"
    if label != "catalog-health":          # the ingest uses the default label
        assert f"--label {label}" in text, f"{workflow} reports under the wrong label"


@pytest.mark.parametrize("workflow", ["fetch-data.yml", "deploy.yml", "tests.yml"])
def test_the_reporting_job_can_grant_itself_issue_write(workflow):
    doc = load(workflow)
    perms = [doc.get("permissions") or {}]
    perms += [(j.get("permissions") or {}) for j in doc["jobs"].values()]
    assert any(p.get("issues") == "write" for p in perms), \
        f"{workflow} cannot open an issue"


@pytest.mark.parametrize("workflow", ["fetch-data.yml", "deploy.yml", "tests.yml"])
def test_health_is_reported_even_when_the_run_failed(workflow):
    """A reporter that only runs on success cannot report a failure.

    Two spellings qualify. `always()` runs unconditionally; `!cancelled()` runs
    on success and failure but not when someone cancelled the run, which is the
    better guard where it is used — nobody wants an issue opened because they
    hit the stop button.
    """
    text = (WF / workflow).read_text()
    before = text[:text.index("report_health.py")]
    assert "always()" in before or "!cancelled()" in before, \
        f"{workflow} would skip reporting on failure"


# --------------------------------------------------------------------------- #
# The test workflow specifically
# --------------------------------------------------------------------------- #
def test_the_test_workflow_reports_once_not_once_per_matrix_leg():
    """Three matrix jobs each updating one issue would fight each other."""
    doc = load("tests.yml")
    job = doc["jobs"]["report"]
    assert "strategy" not in job, "the reporting job must not be a matrix"
    assert set(job["needs"]) >= {"test", "build-api"}


def test_the_test_workflow_does_not_report_on_pull_requests():
    """A contributor's failing PR is not a problem with main, and a fork's
    token has no issues:write anyway."""
    doc = load("tests.yml")
    cond = doc["jobs"]["report"]["if"]
    assert "pull_request" in cond and "!=" in cond, cond
    assert "pull_request" in triggers(doc), "PRs should still run the tests"


def test_the_test_workflow_still_runs_on_push_to_main():
    doc = load("tests.yml")
    push = triggers(doc)["push"]
    assert "main" in push["branches"]


def test_the_reporting_job_watches_every_other_job():
    """A job nobody waits on is a job whose failure is never reported."""
    doc = load("tests.yml")
    others = set(doc["jobs"]) - {"report"}
    assert set(doc["jobs"]["report"]["needs"]) == others, (
        f"report waits on {doc['jobs']['report']['needs']} but the workflow "
        f"also has {sorted(others)}"
    )
