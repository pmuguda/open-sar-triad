"""
Tests for land-use classification.

Two things are worth guarding. The classifier must say true things about real
places, so the geometry tests use known coordinates rather than fixtures: a
footprint over Heathrow has to come out `airport`, and one in the open Pacific
must not. And the carry-forward path must work, because without it every weekly
run redoes nine minutes of geometry to re-derive an answer that cannot have
changed — the kind of silent waste that goes unnoticed for months.
"""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "python"))

spec = importlib.util.spec_from_file_location(
    "classify_scenes", ROOT / "scripts" / "classify_scenes.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

pytest.importorskip("shapely")
CACHE = ROOT / "data" / ".ne-cache"
have_layers = all((CACHE / f"{n}.geojson").exists() for n in cs.LAYERS.values())
needs_layers = pytest.mark.skipif(
    not have_layers, reason="Natural Earth layers not cached locally")


def box(lon, lat, half=0.05):
    """A footprint-sized square around a point."""
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [lon - half, lat - half], [lon + half, lat - half],
                [lon + half, lat + half], [lon - half, lat + half],
                [lon - half, lat - half]]]},
            "properties": {"id": f"{lon},{lat}"}}


@pytest.fixture(scope="module")
def layers():
    return cs.fetch_layers(CACHE)


def tags_at(layers, lon, lat, half=0.05):
    f = box(lon, lat, half)
    cs.classify_natural_earth([f], layers)
    return set(f["properties"]["landuse"])


# --------------------------------------------------------------------------- #
# Does it say true things about real places?
# --------------------------------------------------------------------------- #
@needs_layers
@pytest.mark.parametrize("place, lon, lat, expect", [
    ("Heathrow",            -0.454, 51.470, "airport"),
    ("Rotterdam",            4.10,  51.95,  "port"),
    ("central London",      -0.12,  51.51,  "urban"),
    ("Swiss Alps",           7.95,  46.55,  "mountain"),
    ("Sahara",              15.0,   25.0,   "desert"),
    ("Greenland ice sheet", -42.0,  72.0,   "ice"),
    ("mid-Pacific",       -150.0,   10.0,   "offshore"),
])
def test_known_places_get_the_right_tag(layers, place, lon, lat, expect):
    assert expect in tags_at(layers, lon, lat), place


@needs_layers
def test_the_open_ocean_is_not_an_airport(layers):
    t = tags_at(layers, -150.0, 10.0)
    assert t == {"offshore"}, t


@needs_layers
@pytest.mark.parametrize("city, lon, lat", [
    ("Tokyo", 139.77, 35.68),        # ~4% of the footprint is sea
    ("Barcelona", 2.17, 41.38),      # ~16%
    ("Tianjin", 117.736, 38.984),    # ~33%
])
def test_a_coastal_city_is_not_offshore(layers, city, lon, lat):
    """Touching the coastline is not being at sea.

    Testing whether the footprint *intersects* the ocean rather than how much
    of it is water labelled Tokyo, Barcelona, Lagos and Lima as offshore, which
    is how this started.
    """
    t = tags_at(layers, lon, lat)
    assert "offshore" not in t, f"{city}: {t}"
    assert "urban" in t, f"{city}: {t}"


@needs_layers
def test_a_mostly_sea_footprint_is_offshore(layers):
    """The other half of the threshold: it still has to fire when it should."""
    assert "offshore" in tags_at(layers, -150.0, 10.0)


@needs_layers
def test_a_footprint_can_carry_several_tags(layers):
    """A harbour is a port and a city and water at once; collapsing that to one
    label would throw away the most useful part."""
    assert len(tags_at(layers, 4.10, 51.95)) > 1


@needs_layers
def test_tags_come_back_in_the_declared_order(layers):
    f = box(4.10, 51.95)
    cs.classify_natural_earth([f], layers)
    got = f["properties"]["landuse"]
    assert got == [t for t in cs.TAGS if t in got]


@needs_layers
def test_a_broken_geometry_yields_no_tags_rather_than_raising(layers):
    f = {"type": "Feature", "geometry": None, "properties": {"id": "x"}}
    cs.classify_natural_earth([f], layers)
    assert f["properties"]["landuse"] == []


# --------------------------------------------------------------------------- #
# The taxonomy
# --------------------------------------------------------------------------- #
def test_geographic_descriptors_are_not_land_use():
    """Being inside the "Africa" polygon says nothing about the ground, and
    Island/Coast/Peninsula describe shape rather than use."""
    for junk in ("Continent", "Island", "Island group", "Coast",
                 "Pen/cape", "Geoarea", "Isthmus"):
        assert junk not in cs.TERRAIN, junk


def test_every_mapped_terrain_class_is_a_declared_tag():
    for tag in cs.TERRAIN.values():
        assert tag in cs.TAGS, tag


def test_osm_only_tags_are_declared_too():
    """They arrive from a different source but share one vocabulary."""
    for tag in ("agriculture", "forest", "industrial", "military"):
        assert tag in cs.TAGS


# --------------------------------------------------------------------------- #
# Carry-forward
# --------------------------------------------------------------------------- #
def test_carry_forward_reuses_committed_tags(tmp_path, monkeypatch):
    previous = {"features": [
        {"properties": {"id": "a", "landuse": ["port", "urban"]}},
        {"properties": {"id": "b", "landuse": []}},
    ]}

    class R:
        stdout = json.dumps(previous).encode()

    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R())
    cat = ROOT / "data" / "scenes.geojson"
    feats = [{"properties": {"id": "a"}}, {"properties": {"id": "b"}},
             {"properties": {"id": "c"}}]
    n = cs.carry_forward(feats, cat, log=lambda *a: None)
    assert n == 2
    assert feats[0]["properties"]["landuse"] == ["port", "urban"]
    assert feats[1]["properties"]["landuse"] == []      # a real empty, reused
    assert "landuse" not in feats[2]["properties"]      # new scene, needs work


def test_carry_forward_survives_a_missing_previous_catalog(monkeypatch):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(cs.subprocess, "run", boom)
    feats = [{"properties": {"id": "a"}}]
    assert cs.carry_forward(feats, ROOT / "data" / "scenes.geojson",
                            log=lambda *a: None) == 0
    assert "landuse" not in feats[0]["properties"]


def test_classification_skips_scenes_that_already_have_tags(layers_unused=None):
    """The whole point of carry-forward: no geometry work for known scenes."""
    f = box(-0.454, 51.470)
    f["properties"]["landuse"] = ["kept"]
    cs.classify_natural_earth([f], {k: [] for k in cs.LAYERS})
    assert f["properties"]["landuse"] == ["kept"]


# --------------------------------------------------------------------------- #
# OSM enrichment: it must never be what breaks an ingest
# --------------------------------------------------------------------------- #
def test_osm_gives_up_when_overpass_is_unreachable(monkeypatch):
    import urllib.error

    def refuse(*a, **k):
        raise urllib.error.URLError("blocked")
    monkeypatch.setattr(cs.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)

    feats = [box(i * 0.5, 50.0) for i in range(40)]
    for f in feats:
        f["properties"]["landuse"] = ["urban"]
    stats = cs.osm_enrich(feats, log=lambda *a: None)
    assert stats["queried"] == 0 and stats["failed"] >= 10
    assert stats["failed"] < 40, "should stop rather than try every location"
    assert all(f["properties"]["landuse"] == ["urban"] for f in feats), \
        "an unreachable Overpass must leave Natural Earth tags alone"


def test_osm_merges_rather_than_replaces(monkeypatch):
    import io

    payload = json.dumps({"elements": [{"tags": {"landuse": "farmland"}}]}).encode()

    class Resp:
        def read(self): return payload
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda *a, **k: Resp())
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    f = box(10.0, 50.0)
    f["properties"]["landuse"] = ["plain"]
    cs.osm_enrich([f], log=lambda *a: None)
    assert set(f["properties"]["landuse"]) == {"plain", "agriculture"}


def test_osm_groups_scenes_by_location(monkeypatch):
    """3,300 places, not 14,920 scenes: four times less traffic, same answer."""
    calls = []

    class Resp:
        def read(self): return b'{"elements": []}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(cs.urllib.request, "urlopen",
                        lambda *a, **k: calls.append(1) or Resp())
    monkeypatch.setattr(cs.time, "sleep", lambda s: None)
    feats = [box(10.0, 50.0) for _ in range(25)] + [box(30.0, 40.0)]
    cs.osm_enrich(feats, log=lambda *a: None)
    assert len(calls) == 2, f"26 scenes on 2 places should be 2 queries, was {len(calls)}"


# --------------------------------------------------------------------------- #
# The published catalog and API
# --------------------------------------------------------------------------- #
def test_the_committed_catalog_is_classified():
    cat = ROOT / "data" / "scenes.geojson"
    if not cat.exists():
        pytest.skip("catalog not present")
    doc = json.loads(cat.read_text())
    assert doc.get("landuse_tags") == cs.TAGS, "the catalog should declare its vocabulary"
    feats = doc["features"]
    tagged = sum(1 for f in feats if f["properties"].get("landuse"))
    assert tagged > len(feats) * 0.5, f"only {tagged}/{len(feats)} scenes tagged"
    vocab = set(cs.TAGS)
    for f in feats:
        assert set(f["properties"].get("landuse") or []) <= vocab, f["properties"]["id"]


# --------------------------------------------------------------------------- #
# The web app wiring
# --------------------------------------------------------------------------- #
APP = (ROOT / "js" / "app.js").read_text()
INDEX = (ROOT / "index.html").read_text()


def test_the_app_has_a_land_use_control():
    assert 'id="luChips"' in INDEX and 'id="luVal"' in INDEX


def test_the_app_filters_on_land_use():
    assert "landuseFilter" in APP and "sceneLanduse" in APP
    assert re.search(r"f\.landuse\s*&&\s*f\.landuse\.size", APP)


def test_selecting_several_buckets_is_an_or():
    """They are not exclusive; requiring all of them would match almost nothing."""
    clause = re.search(r"if \(f\.landuse[^;]+;", APP, re.S)
    assert clause, "no land-use clause in the filter"
    body = clause.group(0)
    assert "sceneLanduse(p).some(" in body, f"expected an OR, got: {body}"
    assert ".every(" not in body


def test_land_use_is_in_the_shareable_url():
    assert "p.set('lu'" in APP and "p.get('lu')" in APP


def test_reset_clears_the_land_use_filter():
    reset = APP[APP.index("document.getElementById('resetBtn')"):]
    reset = reset[:reset.index("showToast('Filters reset')")]
    assert "landuseFilter.clear()" in reset


def test_the_ui_says_these_describe_the_place_not_the_imagery():
    """The distinction matters: nothing here looks at a pixel."""
    assert "not what the radar shows" in INDEX
