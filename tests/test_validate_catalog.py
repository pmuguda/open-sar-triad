"""
Mutation tests for scripts/validate_catalog.py.

A validator that never fails is worthless, so each case deliberately corrupts a
healthy catalog in one specific way and asserts the validator rejects it. Every
case here corresponds to a real way the pipeline could break.
"""

import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from conftest import make_catalog, make_scene

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "validate_catalog.py"

spec = importlib.util.spec_from_file_location("validate_catalog", SCRIPT)
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)


def run(doc, baseline=None):
    """Validate a catalog dict in-process; returns the Report."""
    rep = vc.Report()
    feats = vc.check_structure(doc, rep)
    if feats:
        vc.check_features(feats, rep)
        vc.check_ingestion_freshness(feats, rep)
        vc.check_regression(feats, baseline, rep)
    return rep


# --------------------------------------------------------------------------- #
# Baseline: a healthy catalog must pass
# --------------------------------------------------------------------------- #
def test_healthy_catalog_passes(catalog):
    rep = run(catalog, baseline=catalog)
    assert rep.ok, f"healthy catalog rejected: {rep.errors}"


def test_healthy_catalog_reports_counts(catalog):
    rep = run(catalog)
    assert rep.info["total"] == 12
    assert rep.info["by_provider"] == {"iceye": 4, "umbra": 4, "capella": 4}


# --------------------------------------------------------------------------- #
# Structural corruption
# --------------------------------------------------------------------------- #
def test_rejects_wrong_top_level_type(catalog, mutate):
    bad = mutate(catalog, lambda c: c.update(type="Feature"))
    assert not run(bad).ok


def test_rejects_empty_features(catalog, mutate):
    bad = mutate(catalog, lambda c: c.update(features=[]))
    assert not run(bad).ok


# --------------------------------------------------------------------------- #
# Per-feature corruption
# --------------------------------------------------------------------------- #
def test_rejects_duplicate_ids(catalog, mutate):
    bad = mutate(catalog, lambda c: c["features"][1]["properties"].update(
        id=c["features"][0]["properties"]["id"]))
    rep = run(bad)
    assert not rep.ok and any("duplicate" in e for e in rep.errors)


def test_rejects_missing_id(catalog, mutate):
    bad = mutate(catalog, lambda c: c["features"][0]["properties"].update(id=""))
    assert not run(bad).ok


def test_rejects_unknown_provider(catalog, mutate):
    bad = mutate(catalog, lambda c: c["features"][0]["properties"].update(provider="sentinel"))
    rep = run(bad)
    assert not rep.ok and any("provider" in e for e in rep.errors)


def test_rejects_future_date(catalog, mutate):
    future = (date.today() + timedelta(days=30)).isoformat()
    bad = mutate(catalog, lambda c: c["features"][0]["properties"].update(
        date=future, year=int(future[:4])))
    rep = run(bad)
    assert not rep.ok and any("future" in e for e in rep.errors)


def test_rejects_implausibly_old_date(catalog, mutate):
    bad = mutate(catalog, lambda c: c["features"][0]["properties"].update(
        date="1999-01-01", year=1999))
    assert not run(bad).ok


def test_rejects_malformed_date(catalog, mutate):
    bad = mutate(catalog, lambda c: c["features"][0]["properties"].update(date="June 2025"))
    rep = run(bad)
    assert not rep.ok and any("date" in e for e in rep.errors)


def test_rejects_year_disagreeing_with_date(catalog, mutate):
    bad = mutate(catalog, lambda c: c["features"][0]["properties"].update(year=1234))
    rep = run(bad)
    assert not rep.ok and any("disagrees" in e for e in rep.errors)


def test_rejects_coordinates_off_the_earth(catalog, mutate):
    def corrupt(c):
        c["features"][0]["geometry"]["coordinates"][0][0] = [999.0, 12.0]
    rep = run(mutate(catalog, corrupt))
    assert not rep.ok and any("geometry" in e for e in rep.errors)


def test_rejects_degenerate_ring(catalog, mutate):
    def corrupt(c):
        c["features"][0]["geometry"]["coordinates"][0] = [[10.0, 50.0], [10.1, 50.0]]
    rep = run(mutate(catalog, corrupt))
    assert not rep.ok and any("geometry" in e for e in rep.errors)


def test_rejects_wrong_geometry_type(catalog, mutate):
    def corrupt(c):
        c["features"][0]["geometry"] = {"type": "Point", "coordinates": [10.0, 50.0]}
    assert not run(mutate(catalog, corrupt)).ok


def test_rejects_non_http_asset_url(catalog, mutate):
    def corrupt(c):
        c["features"][0]["properties"]["products"] = {"GEC": "javascript:alert(1)"}
        c["features"][0]["properties"]["download"] = "javascript:alert(1)"
    rep = run(mutate(catalog, corrupt))
    assert not rep.ok and any("http" in e for e in rep.errors)


def test_rejects_asset_on_unexpected_host(catalog, mutate):
    """An asset URL drifting to some other host means the pipeline picked up
    something it should not have."""
    def corrupt(c):
        c["features"][0]["properties"]["products"] = {"GEC": "https://evil.example.com/x.tif"}
        c["features"][0]["properties"]["download"] = "https://evil.example.com/x.tif"
    rep = run(mutate(catalog, corrupt))
    assert not rep.ok and any("unexpected host" in e for e in rep.errors)


def test_rejects_uncollapsed_capella_id(catalog, mutate):
    """The Capella collapse turns 6-7 per-format records into one acquisition.
    A surviving _GEC_ token means that step silently did not run."""
    def corrupt(c):
        for f in c["features"]:
            if f["properties"]["provider"] == "capella":
                f["properties"]["id"] = "CAPELLA_C15_SS_GEC_HH_20260629145819"
                break
    rep = run(mutate(catalog, corrupt))
    assert not rep.ok and any("collapse" in e for e in rep.errors)


def test_rejects_bad_first_seen_format(catalog, mutate):
    bad = mutate(catalog, lambda c: c["features"][0]["properties"].update(first_seen="last week"))
    assert not run(bad).ok


def test_rejects_provider_with_zero_scenes(catalog, mutate):
    def corrupt(c):
        c["features"] = [f for f in c["features"] if f["properties"]["provider"] != "iceye"]
    rep = run(mutate(catalog, corrupt))
    assert not rep.ok and any("zero scenes" in e for e in rep.errors)


# --------------------------------------------------------------------------- #
# Regression against the previous catalog
# --------------------------------------------------------------------------- #
def test_rejects_large_total_drop():
    """The catalog losing most of its scenes is the loudest possible signal."""
    baseline = make_catalog(n_per_provider=40)
    shrunk = make_catalog(n_per_provider=4)
    rep = run(shrunk, baseline=baseline)
    assert not rep.ok and any("fell" in e for e in rep.errors)


def test_rejects_provider_dropping_to_zero():
    baseline = make_catalog(n_per_provider=10)
    gone = {"type": "FeatureCollection", "generated_at": "x", "features": [
        f for f in make_catalog(10)["features"] if f["properties"]["provider"] != "umbra"
    ]}
    rep = run(gone, baseline=baseline)
    assert not rep.ok
    assert any("zero" in e for e in rep.errors)


def test_accepts_healthy_growth():
    """Growth must never be mistaken for a fault."""
    baseline = make_catalog(n_per_provider=4)
    grown = make_catalog(n_per_provider=6)
    rep = run(grown, baseline=baseline)
    assert rep.ok, rep.errors
    assert rep.info["delta_total"] == 6
    assert rep.info["added"] == 6


def test_accepts_small_churn():
    """A few scenes disappearing upstream is normal and must not fail."""
    baseline = make_catalog(n_per_provider=40)
    trimmed = {"type": "FeatureCollection", "generated_at": "x",
               "features": make_catalog(40)["features"][:-2]}
    rep = run(trimmed, baseline=baseline)
    assert rep.ok, rep.errors


def test_warns_when_identical_to_baseline(catalog):
    """This is the silent-fallback signature that froze the catalog for weeks:
    a run that 'succeeds' while the scene set never moves."""
    rep = run(catalog, baseline=catalog)
    assert rep.ok
    assert any("identical" in w for w in rep.warnings)
    assert rep.info.get("identical_to_baseline") is True


def test_warns_on_stale_ingestion():
    old = "2020-01-01"
    doc = {"type": "FeatureCollection", "generated_at": "x", "features": [
        make_scene(f"{p}-1", provider=p, first_seen=old) for p in ("iceye", "umbra", "capella")
    ]}
    rep = run(doc)
    assert any("no new scenes ingested" in w for w in rep.warnings)


# --------------------------------------------------------------------------- #
# The script as a process: exit codes are what CI actually gates on
# --------------------------------------------------------------------------- #
def test_cli_exits_zero_on_healthy_catalog(catalog, write_catalog, tmp_path):
    p = write_catalog(catalog)
    base = write_catalog(catalog, "baseline.geojson")
    r = subprocess.run([sys.executable, str(SCRIPT), "--catalog", str(p),
                        "--baseline", str(base)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "PASSED" in r.stdout


def test_cli_exits_nonzero_on_corrupt_catalog(catalog, mutate, write_catalog):
    bad = mutate(catalog, lambda c: c["features"][1]["properties"].update(
        id=c["features"][0]["properties"]["id"]))
    p = write_catalog(bad)
    r = subprocess.run([sys.executable, str(SCRIPT), "--catalog", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "FAILED" in r.stderr


def test_cli_strict_mode_fails_on_warnings(catalog, write_catalog):
    p = write_catalog(catalog)
    base = write_catalog(catalog, "baseline.geojson")
    r = subprocess.run([sys.executable, str(SCRIPT), "--catalog", str(p),
                        "--baseline", str(base), "--strict"],
                       capture_output=True, text=True)
    assert r.returncode == 1  # identical-to-baseline warning becomes fatal


def test_cli_writes_json_report(catalog, write_catalog, tmp_path):
    p = write_catalog(catalog)
    out = tmp_path / "report.json"
    subprocess.run([sys.executable, str(SCRIPT), "--catalog", str(p),
                    "--json", str(out)], capture_output=True, text=True)
    rep = json.loads(out.read_text())
    assert rep["ok"] is True and rep["total"] == 12
    assert "checked_at" in rep


def test_cli_fails_on_missing_catalog(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--catalog",
                        str(tmp_path / "nope.geojson")], capture_output=True, text=True)
    assert r.returncode == 1
