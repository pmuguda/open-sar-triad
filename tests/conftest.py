"""Shared fixtures. Tests build tiny synthetic catalogs rather than loading the
real 28 MB file, so the suite runs in under a second."""

import copy
import datetime
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


# --------------------------------------------------------------------------- #
# Skips that are allowed to be skips
# --------------------------------------------------------------------------- #
#: The only reasons a test may skip when OST_STRICT_SKIPS is set, which the test
#: workflow does. Everything else that skips there is treated as a failure.
#:
#: This exists because skipping is how coverage quietly disappears. The whole
#: workflow test file skipped itself on every CI run for weeks because PyYAML
#: was not installed, and nine terrain tests skipped because nothing fetched
#: their elevation tiles; the run was green both times. A skip is only honest
#: when the thing it waits for is genuinely optional in that environment:
#:   * QGIS is not installed in the plain test job; the plugin has its own
#:     job, inside a QGIS container, where a missing QGIS fails instead.
#:   * Natural Earth layers and elevation tiles are downloaded best-effort, so
#:     an outage at their host must not turn the build red.
ALLOWED_SKIPS = ("qgis", "natural earth layers not cached",
                 "elevation tiles not cached", "elevation tile unavailable")


def skip_is_allowed(reason: str) -> bool:
    return any(a in reason.lower() for a in ALLOWED_SKIPS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not report.skipped or not os.environ.get("OST_STRICT_SKIPS"):
        return
    if hasattr(report, "wasxfail"):
        return
    reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
    if skip_is_allowed(reason):
        return
    report.outcome = "failed"
    report.longrepr = _strict_message(item.nodeid, reason)


def _strict_message(nodeid: str, reason: str) -> str:
    return (f"{nodeid} skipped under OST_STRICT_SKIPS: {reason}\n"
            "A skip here usually means a dependency is missing from "
            "requirements-dev.txt. If the skip is legitimate, add its "
            "reason to ALLOWED_SKIPS in tests/conftest.py.")


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    """A module-level importorskip skips the whole file at collection, before
    any test exists for the hook above to see. That is the worst kind: one
    missing package and a file of tests is gone."""
    outcome = yield
    report = outcome.get_result()
    if not report.skipped or not os.environ.get("OST_STRICT_SKIPS"):
        return
    reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
    if skip_is_allowed(reason):
        return
    report.outcome = "failed"
    report.longrepr = _strict_message(report.nodeid, reason)


#: Default ingestion stamp, relative to today. A fixed date would drift past the
#: staleness thresholds as the calendar moved, so the "healthy catalog" fixture
#: would start emitting stale-ingestion warnings for no reason connected to the
#: test. Tests that care about staleness pass first_seen explicitly.
RECENTLY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

#: Default acquisition date, also relative. A fixed one drifts past the
#: acquisition-recency threshold as the calendar moves, so the "healthy catalog"
#: fixture would start reporting a provider drought for no reason connected to
#: the test — the same way a fixed first_seen did. Tests that care about
#: recency pass a date explicitly.
ACQUIRED = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()


def make_scene(sid, provider="umbra", date=ACQUIRED, mode="spotlight",
               lon=10.0, lat=50.0, products=None, first_seen=RECENTLY):
    """One well-formed feature, shaped exactly like fetch_catalog.py emits."""
    host = {
        "umbra": "https://umbra-open-data-catalog.s3.us-west-2.amazonaws.com",
        "iceye": "https://iceye-open-data-catalog.s3.amazonaws.com",
        "capella": "https://capella-open-data.s3.amazonaws.com",
    }[provider]
    if products is None:
        products = {"GEC": f"{host}/d/{sid}_GEC.tif", "SICD": f"{host}/d/{sid}_SICD.nitf"}
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[
            [lon, lat], [lon + 0.1, lat], [lon + 0.1, lat + 0.1],
            [lon, lat + 0.1], [lon, lat],
        ]]},
        "properties": {
            "id": sid, "provider": provider, "provider_label": provider.title(),
            "color": "#00C9FF", "date": date, "year": int(date[:4]),
            "sensor_mode": mode, "polarization": "VV", "orbit_state": "ascending",
            "look_dir": "right", "first_seen": first_seen,
            "products": products, "download": next(iter(products.values())),
        },
    }


def make_catalog(n_per_provider=4):
    feats = []
    for provider in ("iceye", "umbra", "capella"):
        for i in range(n_per_provider):
            feats.append(make_scene(f"{provider}-scene-{i:03d}", provider=provider,
                                    lon=10.0 + i, lat=50.0))
    return {
        "type": "FeatureCollection",
        "generated_at": "2026-09-21T08:00:00+00:00",
        "features": feats,
    }


@pytest.fixture
def catalog():
    return make_catalog()


@pytest.fixture
def write_catalog(tmp_path):
    """Write a catalog dict to disk and return its path."""
    def _write(doc, name="scenes.geojson"):
        p = tmp_path / name
        p.write_text(json.dumps(doc))
        return p
    return _write


@pytest.fixture
def mutate():
    """Deep-copy a catalog and apply a mutation, so cases never share state."""
    def _mutate(doc, fn):
        c = copy.deepcopy(doc)
        fn(c)
        return c
    return _mutate
