"""
Tests for the Python client.

Each test builds a small API on disk and points the client at it over file://,
so the suite exercises the real fetch path with no network and no server.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from conftest import make_catalog, make_scene

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "python"))

from opensartriad import Catalog, FAMILIES, OpenSarTriadError, Scene  # noqa: E402


@pytest.fixture
def api(tmp_path, catalog):
    """A built API, addressable as a file:// base URL."""
    spec = importlib.util.spec_from_file_location(
        "build_api", ROOT / "scripts" / "build_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / "scenes.geojson"
    src.write_text(json.dumps(catalog))
    mod.SRC = src
    mod.OUT = tmp_path / "api" / "v1"
    assert mod.main() == 0
    return f"file://{mod.OUT}"


@pytest.fixture
def cat(api):
    return Catalog(api)


# --------------------------------------------------------------------------- #
# Basics
# --------------------------------------------------------------------------- #
def test_stats(cat):
    st = cat.stats()
    assert st["total"] == 12
    assert st["by_provider"]["umbra"] == 4


def test_all_returns_every_scene(cat):
    assert len(cat.all()) == 12


def test_index_decoding_gives_named_attributes(cat):
    """The wire format is positional arrays; the client must turn them into
    objects with the right fields in the right places."""
    s = cat.all()[0]
    assert isinstance(s, Scene)
    assert isinstance(s.id, str) and s.id
    assert s.provider in ("iceye", "umbra", "capella")
    assert s.date.count("-") == 2
    assert s.year == int(s.date[:4])
    assert isinstance(s.formats, list) and s.formats
    assert len(s.bbox) == 4


def test_get_by_id(cat):
    first = cat.all()[0]
    assert cat.get(first.id).id == first.id
    assert cat.get("does-not-exist") is None


# --------------------------------------------------------------------------- #
# Search filters
# --------------------------------------------------------------------------- #
def test_search_by_provider(cat):
    assert len(cat.search(providers="umbra")) == 4
    assert len(cat.search(providers=["umbra", "iceye"])) == 8
    assert {s.provider for s in cat.search(providers="capella")} == {"capella"}


def test_search_by_date_range(cat):
    assert len(cat.search(start="2025-01-01")) == 12
    assert len(cat.search(start="2030-01-01")) == 0
    assert len(cat.search(end="2020-01-01")) == 0


def test_search_dates_are_inclusive(cat):
    d = cat.all()[0].date
    assert len(cat.search(start=d, end=d)) > 0


def test_search_accepts_date_objects(cat):
    from datetime import date
    assert len(cat.search(start=date(2025, 1, 1))) == 12


def test_search_by_mode_is_case_insensitive(cat):
    assert len(cat.search(mode="spotlight")) == 12
    assert len(cat.search(mode="SPOTLIGHT")) == 12
    assert len(cat.search(mode="stripmap")) == 0


def test_search_by_orbit_and_look(cat):
    assert len(cat.search(orbit="ascending")) == 12
    assert len(cat.search(orbit="descending")) == 0
    assert len(cat.search(look="right")) == 12
    assert len(cat.search(look="left")) == 0


def test_search_by_bbox(cat):
    """Scenes sit at lon 10..13; a box over them matches, one far away does not."""
    assert len(cat.search(bbox=(9.0, 49.0, 14.0, 51.0))) == 12
    assert len(cat.search(bbox=(-170.0, -80.0, -160.0, -70.0))) == 0


def test_search_by_exact_format(cat):
    assert len(cat.search(formats="GEC")) == 12
    assert len(cat.search(formats="GRD")) == 0


def test_search_by_family(cat):
    assert len(cat.search(family="complex")) == 12   # every fixture scene has SICD
    assert len(cat.search(family="phase")) == 0      # none has CPHD


def test_search_rejects_unknown_family(cat):
    with pytest.raises(ValueError, match="Unknown family"):
        cat.search(family="nonsense")


def test_search_limit(cat):
    assert len(cat.search(limit=3)) == 3


def test_filters_combine_with_and(cat):
    assert len(cat.search(providers="umbra", mode="spotlight", orbit="ascending")) == 4
    assert len(cat.search(providers="umbra", orbit="descending")) == 0


# --------------------------------------------------------------------------- #
# SceneCollection behaves like a sequence
# --------------------------------------------------------------------------- #
def test_collection_is_a_sequence(cat):
    r = cat.all()
    assert len(r) == 12
    assert isinstance(r[0], Scene)
    assert len(r[:5]) == 5
    assert len(list(iter(r))) == 12
    assert "12 scenes" in repr(r)


def test_empty_collection_repr(cat):
    assert "empty" in repr(cat.search(providers="umbra", orbit="descending"))


# --------------------------------------------------------------------------- #
# Family resolution: the reason the client exists
# --------------------------------------------------------------------------- #
def test_resolve_prefers_first_available_format():
    s = Scene("x", "umbra", "2025-01-01", "spotlight", None, None,
              formats=["GEC", "SICD", "CPHD"], bbox=[0, 0, 1, 1])
    assert s.resolve("complex") == "SICD"     # no SLC published, falls back
    assert s.resolve("detected") == "GEC"     # no GEO or GRD, falls back
    assert s.resolve("phase") == "CPHD"
    assert s.resolve("visual") is None


def test_resolve_picks_preferred_when_present():
    s = Scene("x", "capella", "2025-01-01", "spotlight", None, None,
              formats=["GEC", "GEO", "SLC", "SICD"], bbox=[0, 0, 1, 1])
    assert s.resolve("complex") == "SLC"      # SLC beats SICD
    assert s.resolve("detected") == "GEO"     # GEO beats GEC


def test_resolve_rejects_unknown_family():
    s = Scene("x", "umbra", "2025-01-01", None, None, None, formats=[], bbox=[])
    with pytest.raises(ValueError):
        s.resolve("nope")


def test_families_constant_matches_the_app():
    assert FAMILIES["complex"] == ["SLC", "SICD"]
    assert FAMILIES["detected"] == ["GEO", "GRD", "GEC", "SIDD"]


# --------------------------------------------------------------------------- #
# Lazy asset resolution
# --------------------------------------------------------------------------- #
def test_urls_resolve_lazily(cat):
    s = cat.search(providers="umbra", limit=1)[0]
    assert s.url("GEC").startswith("https://umbra-open-data-catalog")
    assert s.metadata_url("GEC").endswith(".stac.v2.json")
    assert s.url("NOPE") is None
    assert s.properties["sar:polarizations"] == "VV"
    assert s.geometry["type"] == "Polygon"


def test_detached_scene_raises(cat):
    s = Scene("x", "umbra", "2025-01-01", None, None, None, formats=[], bbox=[])
    with pytest.raises(OpenSarTriadError, match="detached"):
        _ = s.assets


# --------------------------------------------------------------------------- #
# Download planning
# --------------------------------------------------------------------------- #
def test_download_urls_one_data_file_per_scene_per_family(cat):
    r = cat.search(providers="umbra")
    jobs = r.download_urls(family="complex")
    data = [j for j in jobs if j["kind"] == "data"]
    assert len(data) == len(r)
    assert all(j["format"] == "SICD" for j in data)


def test_download_urls_include_sidecars(cat):
    jobs = cat.search(providers="umbra", limit=2).download_urls(family="detected")
    assert any(j["kind"] == "metadata" for j in jobs)


def test_download_urls_can_skip_metadata(cat):
    jobs = cat.search(providers="umbra", limit=2).download_urls(
        family="detected", metadata=False)
    assert all(j["kind"] == "data" for j in jobs)


def test_download_urls_deduplicate_shared_sidecars(cat):
    """Umbra publishes one sidecar per acquisition shared by every format, so
    requesting several formats must not fetch it repeatedly."""
    r = cat.search(providers="umbra", limit=1)
    jobs = r.download_urls(formats=["GEC", "SICD"])
    metas = [j["url"] for j in jobs if j["kind"] == "metadata"]
    assert len(metas) == len(set(metas)) == 1


def test_download_urls_rejects_both_family_and_formats(cat):
    with pytest.raises(ValueError, match="not both"):
        cat.all().download_urls(family="complex", formats=["SLC"])


def test_dry_run_downloads_nothing(cat, tmp_path):
    out = tmp_path / "dl"
    written = cat.search(limit=2).download(out, family="complex",
                                           dry_run=True, quiet=True)
    assert written == []
    assert not out.exists()


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def test_to_geojson(cat):
    gj = cat.search(limit=3).to_geojson()
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 3
    assert gj["features"][0]["geometry"]["type"] == "Polygon"


def test_to_stac(cat):
    ic = cat.search(limit=3).to_stac()
    assert ic["stac_version"] == "1.0.0"
    assert all(f["type"] == "Feature" for f in ic["features"])
    assert all("assets" in f for f in ic["features"])


def test_save_geojson(cat, tmp_path):
    p = cat.search(limit=2).save_geojson(tmp_path / "sel.geojson")
    assert json.loads(p.read_text())["type"] == "FeatureCollection"


def test_to_dataframe(cat):
    pd = pytest.importorskip("pandas")
    df = cat.search(limit=5).to_dataframe()
    assert len(df) == 5
    assert {"id", "provider", "date", "west", "north"} <= set(df.columns)


# --------------------------------------------------------------------------- #
# STAC passthrough and licence
# --------------------------------------------------------------------------- #
def test_stac_passthrough(cat):
    assert cat.stac_catalog()["type"] == "Catalog"
    assert cat.stac_collection("umbra")["id"] == "umbra"
    assert len(cat.stac_items("umbra")["features"]) == 4


def test_license_is_surfaced(cat):
    lic = cat.license()
    assert lic["license"] == "CC-BY-4.0"
    assert "ICEYE" in lic["attribution"]
    assert "modifications" in lic


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #
def test_unreachable_base_url_raises():
    c = Catalog("file:///nonexistent/api/v1")
    with pytest.raises(OpenSarTriadError):
        c.stats()


def test_index_is_cached(cat):
    """Reusing a Catalog must not refetch; a cold instance is the slow path."""
    cat.all()
    assert cat._index is not None
    n = len(cat._index)
    cat.search(providers="umbra")
    assert len(cat._index) == n
