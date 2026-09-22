"""
Tests for scripts/build_api.py.

The API is a published contract, so these assert the things a consumer depends
on: the files exist at stable paths, STAC items are well formed, assets carry
their metadata sidecars, and the CC-BY notice travels with every file.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from conftest import make_catalog, make_scene

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "build_api.py"


@pytest.fixture
def built(tmp_path, catalog):
    """Run the builder against a synthetic catalog in a temp dir."""
    spec = importlib.util.spec_from_file_location("build_api", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    src = tmp_path / "scenes.geojson"
    src.write_text(json.dumps(catalog))
    mod.SRC = src
    mod.OUT = tmp_path / "api" / "v1"
    assert mod.main() == 0
    return mod.OUT


def load(p):
    return json.loads(Path(p).read_text())


# --------------------------------------------------------------------------- #
# Files and paths: the contract consumers code against
# --------------------------------------------------------------------------- #
def test_produces_all_expected_endpoints(built):
    expected = {
        "catalog.json", "index.json", "stats.json",
        "collections/iceye.json", "collections/umbra.json", "collections/capella.json",
        "items/iceye.json", "items/umbra.json", "items/capella.json",
        "scenes/iceye.geojson", "scenes/umbra.geojson", "scenes/capella.geojson",
    }
    actual = {str(p.relative_to(built)) for p in built.rglob("*") if p.is_file()}
    assert expected == actual


def test_root_catalog_is_valid_stac(built):
    cat = load(built / "catalog.json")
    assert cat["type"] == "Catalog"
    assert cat["stac_version"] == "1.0.0"
    assert cat["id"] == "open-sar-triad"
    children = [l for l in cat["links"] if l["rel"] == "child"]
    assert len(children) == 3
    assert any(l["rel"] == "root" for l in cat["links"])


def test_collections_are_valid_stac(built):
    for prov in ("iceye", "umbra", "capella"):
        col = load(built / "collections" / f"{prov}.json")
        assert col["type"] == "Collection"
        assert col["stac_version"] == "1.0.0"
        assert col["id"] == prov
        assert col["license"] == "CC-BY-4.0"
        bbox = col["extent"]["spatial"]["bbox"][0]
        assert len(bbox) == 4 and bbox[0] <= bbox[2] and bbox[1] <= bbox[3]
        assert col["extent"]["temporal"]["interval"][0][0] is not None


# --------------------------------------------------------------------------- #
# STAC items
# --------------------------------------------------------------------------- #
def test_items_are_valid_stac_features(built):
    ic = load(built / "items" / "umbra.json")
    assert ic["type"] == "FeatureCollection"
    for item in ic["features"]:
        assert item["type"] == "Feature"
        assert item["stac_version"] == "1.0.0"
        assert item["collection"] == "umbra"
        assert item["id"]
        assert len(item["bbox"]) == 4
        assert item["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert item["properties"]["datetime"].endswith("Z")
        assert any(l["rel"] == "collection" for l in item["links"])


def test_item_assets_include_metadata_sidecars(built):
    """Every data asset that has a published sidecar must carry it, so a client
    never has to know each provider's file-naming rules."""
    ic = load(built / "items" / "umbra.json")
    item = ic["features"][0]
    assert "GEC" in item["assets"]
    assert item["assets"]["GEC"]["roles"] == ["data"]
    assert "GEC_metadata" in item["assets"]
    assert item["assets"]["GEC_metadata"]["roles"] == ["metadata"]
    assert item["assets"]["GEC_metadata"]["href"].endswith(".stac.v2.json")


def test_iceye_sidecar_derivation(tmp_path):
    """ICEYE ships .xml beside NITF products and .json beside the rest."""
    spec = importlib.util.spec_from_file_location("build_api", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    host = "https://iceye-open-data-catalog.s3.amazonaws.com"
    assert mod.metadata_url("iceye", f"{host}/a/X_GRD.tif") == f"{host}/a/X_GRD.json"
    assert mod.metadata_url("iceye", f"{host}/a/X_SICD.nitf") == f"{host}/a/X_SICD.xml"
    assert mod.metadata_url("iceye", f"{host}/a/X.cphd") is None
    cap = "https://capella-open-data.s3.amazonaws.com"
    assert mod.metadata_url("capella", f"{cap}/a/C.tif") == f"{cap}/a/C_extended.json"
    assert mod.metadata_url("capella", f"{cap}/a/C.ntf") is None


def test_every_scene_becomes_exactly_one_item(built, catalog):
    total = sum(len(load(built / "items" / f"{p}.json")["features"])
                for p in ("iceye", "umbra", "capella"))
    assert total == len(catalog["features"])


# --------------------------------------------------------------------------- #
# Search index
# --------------------------------------------------------------------------- #
def test_index_rows_align_with_declared_fields(built, catalog):
    """The index ships rows as arrays to halve its size; every row must match
    the declared field order or the client decodes garbage."""
    idx = load(built / "index.json")
    assert idx["count"] == len(catalog["features"]) == len(idx["scenes"])
    assert idx["fields"] == ["id", "provider", "date", "mode", "orbit", "look",
                             "formats", "bbox"]
    for row in idx["scenes"]:
        assert len(row) == len(idx["fields"])
        rec = dict(zip(idx["fields"], row))
        assert isinstance(rec["id"], str) and rec["id"]
        assert rec["provider"] in ("iceye", "umbra", "capella")
        assert len(rec["bbox"]) == 4
        assert isinstance(rec["formats"], list)


def test_stats_match_the_catalog(built, catalog):
    st = load(built / "stats.json")
    assert st["total"] == len(catalog["features"])
    assert st["by_provider"] == {"iceye": 4, "umbra": 4, "capella": 4}
    assert set(st["families"]) == {"detected", "complex", "phase", "visual"}
    assert st["temporal_extent"][0] <= st["temporal_extent"][1]


# --------------------------------------------------------------------------- #
# Licence travels with the data
# --------------------------------------------------------------------------- #
def test_every_file_carries_the_licence_notice(built):
    """CC-BY requires the notice to travel with the material, and any of these
    files can be fetched standalone."""
    files = [p for p in built.rglob("*") if p.is_file()]
    assert len(files) == 12
    for p in files:
        doc = load(p)
        assert doc["license"] == "CC-BY-4.0", p
        assert doc["license_url"].startswith("https://creativecommons.org/"), p
        for key in ("attribution", "modifications", "disclaimer"):
            assert doc.get(key), f"{p} missing {key}"
        assert "ICEYE" in doc["attribution"]
        assert "not endorsed" in doc["disclaimer"].lower()


def test_builder_fails_on_empty_catalog(tmp_path):
    spec = importlib.util.spec_from_file_location("build_api", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / "empty.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    mod.SRC = src
    mod.OUT = tmp_path / "api"
    assert mod.main() == 1


def test_builder_fails_when_source_missing(tmp_path):
    spec = importlib.util.spec_from_file_location("build_api", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SRC = tmp_path / "nope.geojson"
    mod.OUT = tmp_path / "api"
    assert mod.main() == 1
