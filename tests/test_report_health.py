"""
Tests for the ingest health reporter.

The thing being pinned here is a state machine, not a formatter: an unhealthy
run must open an issue, repeated identical unhealthy runs must not spam the
thread, a change in the problems must be announced, and a healthy run must
close the issue. Getting the close wrong is the worst outcome, because an issue
that never closes is the green tick all over again.
"""

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "report_health.py"

spec = importlib.util.spec_from_file_location("report_health", SCRIPT)
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)


class FakeGitHub:
    """Records calls instead of making them, and keeps one issue of state."""

    def __init__(self, existing=None):
        self.issue = existing
        self.created = []
        self.updates = []
        self.comments = []
        self.labels_ensured = 0
        self._n = 41

    def ensure_label(self):
        self.labels_ensured += 1

    def open_issue(self):
        return self.issue

    def create(self, title, body):
        self._n += 1
        self.issue = {"number": self._n, "title": title, "body": body}
        self.created.append(self.issue)
        return self.issue

    def update(self, number, **fields):
        self.updates.append((number, fields))
        if self.issue and self.issue["number"] == number:
            self.issue = {**self.issue, **fields}
        return self.issue

    def comment(self, number, body):
        self.comments.append((number, body))
        return {"id": 1}


def report(errors=(), warnings=(), **info):
    return {"ok": not errors, "errors": list(errors),
            "warnings": list(warnings), **info}


HEALTHY = report(total=14889, by_provider={"umbra": 11904})
STALE = report(warnings=["provider 'capella' has ingested nothing for 23 days"],
               total=14889, by_provider={"capella": 2464},
               provider_freshness={"capella": {"newest_first_seen": "2026-09-07",
                                               "age_days": 23}})
BROKEN = report(errors=["scripts/fetch_catalog.py failed"], total=0,
                subject="Catalog ingest",
                on_error_note="The catalog **was not committed**. The site is "
                              "still serving the last good data.")


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #
def test_unhealthy_run_opens_an_issue():
    gh = FakeGitHub()
    rh.run(STALE, gh)
    assert len(gh.created) == 1
    assert "capella" in gh.created[0]["title"]


def test_healthy_run_with_no_issue_does_nothing():
    """Silence has to mean working, so a good run must be completely quiet."""
    gh = FakeGitHub()
    rh.run(HEALTHY, gh)
    assert not gh.created and not gh.comments and not gh.updates


def test_errors_outrank_warnings_in_the_title():
    gh = FakeGitHub()
    rh.run(report(errors=["boom"], warnings=["meh"]), gh)
    assert "error" in gh.created[0]["title"]


def test_body_carries_the_reports_own_error_note():
    """validate_catalog.py supplies this; the deploy supplies a different one."""
    gh = FakeGitHub()
    rh.run(BROKEN, gh)
    assert "was not committed" in gh.created[0]["body"]


def test_body_carries_the_fetchers_own_count():
    """new_this_run read 0 for nine weeks and nothing surfaced it."""
    gh = FakeGitHub()
    rh.run(report(warnings=["frozen"], new_this_run=0), gh)
    assert "new_this_run: 0" in gh.created[0]["body"]


# --------------------------------------------------------------------------- #
# Not spamming
# --------------------------------------------------------------------------- #
def test_same_problems_next_run_refreshes_without_commenting():
    """A provider quiet for a year must not make a year of comments."""
    gh = FakeGitHub()
    rh.run(STALE, gh)
    first = gh.issue
    gh2 = FakeGitHub(existing=first)
    rh.run(STALE, gh2)
    assert not gh2.created
    assert gh2.updates, "body should still be refreshed so counts stay current"
    assert not gh2.comments


def test_changed_problems_add_a_comment():
    gh = FakeGitHub(existing={"number": 7, "title": "old", "body": "stale body"})
    rh.run(STALE, gh)
    assert gh.comments and gh.comments[0][0] == 7


def test_fingerprint_tracks_the_problem_set_not_the_counts():
    a = rh.fingerprint(["x", "y"])
    assert a == rh.fingerprint(["y", "x"]), "order must not matter"
    assert a != rh.fingerprint(["x", "z"])


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #
def test_recovery_closes_the_issue():
    gh = FakeGitHub(existing={"number": 9, "title": "bad", "body": "b"})
    rh.run(HEALTHY, gh)
    assert (9, {"state": "closed", "state_reason": "completed"}) in gh.updates


def test_recovery_comments_before_closing():
    gh = FakeGitHub(existing={"number": 9, "title": "bad", "body": "b"})
    rh.run(HEALTHY, gh)
    assert gh.comments and "healthy again" in gh.comments[0][1]


def test_a_run_with_only_warnings_still_counts_as_unhealthy():
    """Warnings are the whole point: the freeze never produced an error."""
    assert rh.problems(STALE)
    gh = FakeGitHub()
    rh.run(STALE, gh)
    assert gh.created


# --------------------------------------------------------------------------- #
# The script as a process
# --------------------------------------------------------------------------- #
def test_dry_run_touches_nothing_and_prints(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(STALE))
    sys.argv = ["report_health.py", "--report", str(p), "--repo", "a/b", "--dry-run"]
    assert rh.main() == 0
    assert "would post" in capsys.readouterr().out


def test_missing_report_is_an_error(tmp_path):
    sys.argv = ["report_health.py", "--report", str(tmp_path / "nope.json")]
    assert rh.main() == 1


def test_no_token_skips_quietly(tmp_path, monkeypatch, capsys):
    """A fork or a local run must not fail the pipeline over reporting."""
    p = tmp_path / "r.json"
    p.write_text(json.dumps(STALE))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    sys.argv = ["report_health.py", "--report", str(p), "--repo", "a/b"]
    assert rh.main() == 0
    assert "skipping" in capsys.readouterr().out


def test_github_failure_does_not_fail_the_pipeline(tmp_path, monkeypatch, capsys):
    """Reporting health must never be the thing that breaks the ingest."""
    p = tmp_path / "r.json"
    p.write_text(json.dumps(STALE))
    monkeypatch.setenv("GITHUB_TOKEN", "x")

    def boom(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(rh.GitHub, "open_issue", boom)
    sys.argv = ["report_health.py", "--report", str(p), "--repo", "a/b"]
    assert rh.main() == 0
    assert "could not report health" in capsys.readouterr().err


def test_label_conflict_is_not_an_error(monkeypatch):
    """422 from the labels endpoint means it already exists, which is the goal."""
    gh = rh.GitHub("a/b", "t")

    def conflict(method, path, payload=None):
        raise urllib.error.HTTPError(path, 422, "exists", {}, None)

    monkeypatch.setattr(gh, "_call", conflict)
    gh.ensure_label()          # must not raise


def test_other_label_errors_still_raise(monkeypatch):
    gh = rh.GitHub("a/b", "t")

    def denied(method, path, payload=None):
        raise urllib.error.HTTPError(path, 403, "forbidden", {}, None)

    monkeypatch.setattr(gh, "_call", denied)
    with pytest.raises(urllib.error.HTTPError):
        gh.ensure_label()


def test_a_crashed_run_omits_the_empty_catalog_section():
    """An empty heading reads as missing information rather than none."""
    gh = FakeGitHub()
    rh.run(report(errors=["fetch died"]), gh)
    assert "### Catalog" not in gh.created[0]["body"]


def test_a_normal_run_keeps_the_catalog_section():
    gh = FakeGitHub()
    rh.run(STALE, gh)
    assert "### Catalog" in gh.created[0]["body"]


# --------------------------------------------------------------------------- #
# Two concerns, two issues
#
# Sharing a label would let a healthy deploy close an open ingest issue, and a
# broken ingest would be overwritten by a deploy failure. They are independent.
# --------------------------------------------------------------------------- #
def test_each_topic_has_its_own_label():
    assert "catalog-health" in rh.TOPICS and "site-health" in rh.TOPICS
    assert rh.TOPICS["catalog-health"] != rh.TOPICS["site-health"]


def test_the_label_scopes_the_issue_lookup(monkeypatch):
    seen = []
    gh = rh.GitHub("a/b", "t", label="site-health")
    monkeypatch.setattr(gh, "_call", lambda m, p, payload=None: seen.append(p) or [])
    gh.open_issue()
    assert "labels=site-health" in seen[0]


def test_a_created_issue_carries_its_own_label(monkeypatch):
    sent = {}
    gh = rh.GitHub("a/b", "t", label="site-health")
    monkeypatch.setattr(gh, "_call",
                        lambda m, p, payload=None: sent.update(payload or {}) or {"number": 1})
    gh.create("t", "b")
    assert sent["labels"] == ["site-health"]


def test_the_subject_comes_from_the_report():
    gh = FakeGitHub()
    rh.run(report(errors=["boom"], subject="Site deploy"), gh)
    assert gh.created[0]["title"].startswith("Site deploy")


def test_the_subject_defaults_to_the_ingest():
    gh = FakeGitHub()
    rh.run(report(errors=["boom"]), gh)
    assert gh.created[0]["title"].startswith("Catalog ingest")


def test_the_error_note_comes_from_the_report():
    """So the deploy does not tell you the catalog was not committed."""
    gh = FakeGitHub()
    rh.run(report(errors=["boom"], subject="Site deploy",
                  on_error_note="The site may still be serving the previous build."), gh)
    body = gh.created[0]["body"]
    assert "previous build" in body
    assert "was not committed" not in body


# --------------------------------------------------------------------------- #
# Three concerns now: the ingest, the deploy and the test suite
# --------------------------------------------------------------------------- #
def test_the_test_suite_has_its_own_topic():
    assert "tests-health" in rh.TOPICS
    assert len({v for v in rh.TOPICS.values()}) == len(rh.TOPICS), \
        "each topic needs its own colour and description"


def test_the_footer_does_not_promise_an_ingest():
    """One reporter serves three concerns. Telling someone watching a red test
    run to wait for a healthy ingest would be nonsense."""
    gh = FakeGitHub()
    rh.run(report(errors=["boom"], subject="Test suite"), gh)
    body = gh.created[0]["body"]
    assert "healthy run" in body
    assert "healthy ingest" not in body


def test_the_fingerprint_marker_is_not_tied_to_one_topic():
    gh = FakeGitHub()
    rh.run(report(errors=["boom"], subject="Test suite"), gh)
    assert "catalog-health-fingerprint" not in gh.created[0]["body"]
    assert "ost-health-fingerprint" in gh.created[0]["body"]


def test_a_red_test_run_opens_an_issue_naming_the_suite():
    gh = FakeGitHub(existing=None)
    rh.run(report(errors=["the test matrix (3.9, 3.11, 3.12) failure"],
                  subject="Test suite",
                  on_error_note="`main` does not pass its own tests."), gh)
    assert gh.created[0]["title"].startswith("Test suite failing")
    assert "does not pass its own tests" in gh.created[0]["body"]
