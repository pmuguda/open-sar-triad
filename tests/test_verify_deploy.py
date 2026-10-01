"""
Tests for the deploy verifier.

The risk here is not missing a stale site, it is reporting one that was merely
slow. An alert that cries wolf gets muted, and then the real one is missed too,
which would put this back where it started. So the retry behaviour is pinned
harder than the happy path.
"""

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "verify_deploy.py"

spec = importlib.util.spec_from_file_location("verify_deploy", SCRIPT)
vd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vd)


@pytest.fixture
def no_sleep():
    """Collects the waits instead of serving them."""
    waits = []
    yield waits, waits.append


def responder(*totals):
    """Serves one total per call; an Exception instance is raised instead."""
    seq = list(totals)

    def fetch(base, cache_bust, timeout=30):
        v = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(v, Exception):
            raise v
        return {"total": v}

    return fetch


def test_matching_site_passes_on_the_first_try(monkeypatch, no_sleep):
    waits, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats", responder(14889))
    rep = vd.check("https://x", 14889, attempts=6, wait=30, cache_bust="1", sleep=sleep)
    assert rep["ok"] and not rep["errors"]
    assert rep["attempts_used"] == 1
    assert waits == [], "a site that is already correct must not be polled again"


def test_a_slow_deploy_is_not_reported(monkeypatch, no_sleep):
    """The whole point: stale on attempt one, correct on attempt three."""
    waits, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats", responder(14859, 14859, 14889))
    rep = vd.check("https://x", 14889, attempts=6, wait=30, cache_bust="1", sleep=sleep)
    assert rep["ok"], rep["errors"]
    assert rep["attempts_used"] == 3
    assert waits == [30, 30]


def test_a_genuinely_stale_site_is_reported(monkeypatch, no_sleep):
    waits, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats", responder(14859))
    rep = vd.check("https://x", 14889, attempts=3, wait=30, cache_bust="1", sleep=sleep)
    assert not rep["ok"]
    assert "14,859" in rep["errors"][0] and "14,889" in rep["errors"][0]
    assert rep["attempts_used"] == 3


def test_the_error_says_it_is_not_propagation_delay(monkeypatch, no_sleep):
    """So the issue does not read as something that might fix itself."""
    _, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats", responder(1))
    rep = vd.check("https://x", 2, attempts=2, wait=10, cache_bust="1", sleep=sleep)
    assert "not propagation delay" in rep["errors"][0]


def test_it_waits_between_attempts_but_not_after_the_last(monkeypatch, no_sleep):
    waits, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats", responder(1))
    vd.check("https://x", 2, attempts=4, wait=15, cache_bust="1", sleep=sleep)
    assert waits == [15, 15, 15], "should not sleep after giving up"


def test_an_unreachable_site_is_reported(monkeypatch, no_sleep):
    _, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats",
                        responder(urllib.error.URLError("refused")))
    rep = vd.check("https://x", 10, attempts=2, wait=1, cache_bust="1", sleep=sleep)
    assert not rep["ok"]
    assert "could not read" in rep["errors"][0]


def test_a_transient_network_error_recovers(monkeypatch, no_sleep):
    _, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats",
                        responder(urllib.error.URLError("flap"), 42))
    rep = vd.check("https://x", 42, attempts=3, wait=1, cache_bust="1", sleep=sleep)
    assert rep["ok"], rep["errors"]


def test_a_missing_total_is_reported_not_crashed(monkeypatch, no_sleep):
    _, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats", responder(None))
    rep = vd.check("https://x", 10, attempts=1, wait=0, cache_bust="1", sleep=sleep)
    assert not rep["ok"]
    assert "no usable total" in rep["errors"][0]


# --------------------------------------------------------------------------- #
# Counting and plumbing
# --------------------------------------------------------------------------- #
def test_local_total_counts_scenes_without_parsing(tmp_path):
    doc = {"type": "FeatureCollection", "features": [
        {"properties": {"provider": "umbra"}}, {"properties": {"provider": "iceye"}}]}
    p = tmp_path / "c.geojson"
    p.write_text(json.dumps(doc, separators=(",", ":")))
    assert vd.local_total(p) == 2


def test_local_total_matches_the_real_catalog():
    """The shortcut has to agree with a real parse, or it is not a shortcut."""
    real = ROOT / "data" / "scenes.geojson"
    if not real.exists():
        pytest.skip("catalog not present")
    parsed = len(json.loads(real.read_text())["features"])
    assert vd.local_total(real) == parsed


def test_the_url_is_cache_busted(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=30):
        seen["url"] = req.full_url
        seen["headers"] = req.headers
        raise urllib.error.URLError("stop here")

    monkeypatch.setattr(vd.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.URLError):
        vd.fetch_stats("https://x/site", "abc123")
    assert seen["url"] == "https://x/site/api/v1/stats.json?v=abc123"
    assert seen["headers"].get("Cache-control") == "no-cache"


def test_report_is_shaped_for_the_health_reporter(monkeypatch, no_sleep):
    """It is consumed by report_health.py, which expects these keys."""
    _, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats", responder(1))
    rep = vd.check("https://x", 2, attempts=1, wait=0, cache_bust="1", sleep=sleep)
    assert set(rep) >= {"ok", "errors", "warnings", "subject", "on_error_note"}
    assert rep["subject"] == "Site deploy"


# --------------------------------------------------------------------------- #
# Commit matching
#
# The first live run passed in under a second against a site that had not been
# redeployed: the scene count only moves on ingest weeks, so on a code-only
# deploy it is identical before and after and proves nothing. The build commit
# changes every time.
# --------------------------------------------------------------------------- #
def test_matching_commit_passes():
    ok, err = vd.compare({"commit": "abc123", "total": 1}, expected=99, commit="abc123")
    assert ok and err is None, "count must not override a matching commit"


def test_a_stale_build_is_caught_even_when_the_count_is_unchanged():
    """The hole the first live run fell through."""
    ok, err = vd.compare({"commit": "old1111", "total": 14889},
                         expected=14889, commit="new2222")
    assert not ok
    assert "old1111"[:7] in err and "new2222"[:7] in err


def test_falls_back_to_the_count_when_the_site_predates_commit_stamping():
    """A build published before this existed has no commit field."""
    ok, _ = vd.compare({"total": 14889}, expected=14889, commit="new2222")
    assert ok
    ok, err = vd.compare({"total": 14859}, expected=14889, commit="new2222")
    assert not ok and "14,859" in err


def test_falls_back_to_the_count_outside_actions():
    """A local run has no GITHUB_SHA to compare against."""
    ok, _ = vd.compare({"commit": "abc123", "total": 5}, expected=5, commit="")
    assert ok


def test_a_slow_deploy_is_still_not_reported_with_commits(monkeypatch, no_sleep):
    _, sleep = no_sleep
    seq = [{"commit": "old", "total": 1}, {"commit": "old", "total": 1},
           {"commit": "new", "total": 1}]
    monkeypatch.setattr(vd, "fetch_stats",
                        lambda b, c, timeout=30: seq.pop(0) if len(seq) > 1 else seq[0])
    rep = vd.check("https://x", 1, attempts=5, wait=5, cache_bust="1",
                   commit="new", sleep=sleep)
    assert rep["ok"] and rep["attempts_used"] == 3


def test_the_report_records_both_commits(monkeypatch, no_sleep):
    _, sleep = no_sleep
    monkeypatch.setattr(vd, "fetch_stats",
                        lambda b, c, timeout=30: {"commit": "old", "total": 1})
    rep = vd.check("https://x", 1, attempts=1, wait=0, cache_bust="1",
                   commit="new", sleep=sleep)
    assert rep["published_commit"] == "old" and rep["expected_commit"] == "new"
