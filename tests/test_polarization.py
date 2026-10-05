"""
Tests for polarization: parsing it, indexing it, filtering on it.

The parsing half exists because of a specific bug. Upstream hands polarization
over as a *Python* repr, "['VV']", which is not JSON. The old code tried
json.loads on it, the single quotes made that raise, a bare `except: pass`
swallowed the error, and the raw six characters shipped as the value. Every
scene in the catalog carried the literal string "['VV']" for display, for STAC
and for anything trying to filter on it, and nothing failed.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "python"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fc = _load("fetch_catalog", ROOT / "scripts" / "fetch_catalog.py")
ba = _load("build_api", ROOT / "scripts" / "build_api.py")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw, want", [
    # The shape that actually bit us, and the reason this function exists.
    ("['VV']", ["VV"]),
    ("['HH', 'HV']", ["HH", "HV"]),
    ('["HH"]', ["HH"]),                 # real JSON still works
    ('["VV", "VH"]', ["VV", "VH"]),
    (["VV"], ["VV"]),                   # already a list
    (("HH", "HV"), ["HH", "HV"]),       # tuple, as parquet sometimes yields
    ("VV", ["VV"]),                     # bare token
    ("HH, HV", ["HH", "HV"]),           # delimited
    ("hh+hv", ["HH", "HV"]),            # lower case, odd separator
    ("VV/VH", ["VV", "VH"]),
    ("['VV', 'VV']", ["VV"]),           # deduped
    ("['VV+VH']", ["VV", "VH"]),        # a separator inside the quoted token
    (None, []),
    ("", []),
    ("nan", []),
    ("None", []),
    ("[]", []),
])
def test_parse_polarizations(raw, want):
    assert fc.parse_polarizations(raw) == want


def test_the_python_repr_is_not_json():
    """Why the original json.loads could never have worked."""
    with pytest.raises(ValueError):
        json.loads("['VV']")
    assert fc.parse_polarizations("['VV']") == ["VV"]


def test_separators_win_over_quoting():
    """The parser reads channels, not serialisation. A literal_eval-based
    version returned the single token "VVVH" here."""
    assert fc.parse_polarizations("['VV+VH']") == ["VV", "VH"]


def test_parsing_never_leaks_the_brackets():
    """The symptom: punctuation surviving into the value."""
    for raw in ("['VV']", '["VV"]', "('VV',)", "[ 'VV' ]"):
        assert fc.parse_polarizations(raw) == ["VV"], raw


def test_garbage_yields_empty_rather_than_raising():
    for raw in (object(), 12345, "???"):
        assert isinstance(fc.parse_polarizations(raw), list)


# --------------------------------------------------------------------------- #
# The committed catalog
# --------------------------------------------------------------------------- #
def test_the_committed_catalog_is_clean():
    """No scene should still be carrying a repr."""
    cat = ROOT / "data" / "scenes.geojson"
    if not cat.exists():
        pytest.skip("catalog not present")
    bad = []
    for f in json.loads(cat.read_text())["features"]:
        v = f["properties"].get("polarization")
        if v is not None and not re.fullmatch(r"[A-Z]{2}(, [A-Z]{2})*", str(v)):
            bad.append(v)
        if len(bad) > 3:
            break
    assert not bad, f"unparsed polarization values in the catalog: {bad}"


# --------------------------------------------------------------------------- #
# The built API
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import make_catalog, make_scene
    doc = make_catalog()
    # Give the fixture a dual-pol scene and one with none, since the single-pol
    # case cannot distinguish a working split from a string compare.
    for f, pol in zip(doc["features"], ["VV", "HH, HV", None, "HH"]):
        f["properties"]["polarization"] = pol
    tmp = tmp_path_factory.mktemp("api")
    src = tmp / "scenes.geojson"
    src.write_text(json.dumps(doc))
    ba.SRC, ba.OUT = src, tmp / "api" / "v1"
    assert ba.main() == 0
    return ba.OUT


def test_split_pol():
    assert ba.split_pol("HH, HV") == ["HH", "HV"]
    assert ba.split_pol("VV") == ["VV"]
    assert ba.split_pol(None) == []
    assert ba.split_pol("") == []


def test_the_index_carries_polarization(built):
    doc = json.loads((built / "index.json").read_text())
    assert "pol" in doc["fields"]
    i = doc["fields"].index("pol")
    assert all(isinstance(r[i], list) for r in doc["scenes"]), "pol must be a list"
    assert any(len(r[i]) == 2 for r in doc["scenes"]), "no dual-pol row in the fixture"


def test_stac_polarizations_is_an_array_not_a_string(built):
    """The STAC spec types sar:polarizations as an array. We were emitting the
    display string, which validates as neither."""
    seen = 0
    for f in (built / "items").glob("*.json"):
        for item in json.loads(f.read_text())["features"]:
            v = item["properties"].get("sar:polarizations")
            if v is None:
                continue
            seen += 1
            assert isinstance(v, list), f"{item['id']}: {v!r}"
            assert all(isinstance(x, str) for x in v)
    assert seen, "no item carried a polarization"


def test_a_scene_without_polarization_omits_the_field(built):
    """Rather than emitting an empty array, which would claim 'no channels'."""
    items = [i for f in (built / "items").glob("*.json")
             for i in json.loads(f.read_text())["features"]]
    assert any("sar:polarizations" not in i["properties"] for i in items)


def test_stats_counts_polarizations(built):
    stats = json.loads((built / "stats.json").read_text())
    assert "by_polarization" in stats
    assert stats["by_polarization"], "no polarizations counted"
    # A dual-pol scene counts once per channel, not once per scene.
    assert sum(stats["by_polarization"].values()) >= stats["total"]


# --------------------------------------------------------------------------- #
# The client filter
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def cat(built):
    from opensartriad import Catalog
    return Catalog(f"file://{built}")


def test_scene_exposes_polarization(cat):
    s = cat.all()[0]
    assert isinstance(s.pol, list)
    assert s.polarizations == s.pol, "the readable alias should agree"


def test_search_by_polarization(cat):
    everything = len(cat.all())
    vv = cat.search(polarization="VV")
    assert 0 < len(vv) < everything
    assert all("VV" in s.pol for s in vv)


def test_search_by_polarization_is_case_insensitive(cat):
    assert len(cat.search(polarization="vv")) == len(cat.search(polarization="VV"))


def test_a_dual_pol_scene_matches_either_channel(cat):
    """The point of storing a list: "HH, HV" must be found by HH and by HV."""
    dual = [s for s in cat.all() if len(s.pol) > 1]
    assert dual, "fixture has no dual-pol scene"
    for channel in dual[0].pol:
        assert dual[0].id in {s.id for s in cat.search(polarization=channel)}


def test_search_accepts_several_polarizations(cat):
    both = cat.search(polarization=["VV", "HH"])
    assert len(both) >= len(cat.search(polarization="VV"))


def test_polarization_combines_with_other_filters(cat):
    narrow = cat.search(polarization="VV", providers="umbra")
    assert all(s.provider == "umbra" and "VV" in s.pol for s in narrow)


def test_exports_carry_polarization(cat):
    sel = cat.all()
    gj = sel.to_geojson()
    assert "polarization" in gj["features"][0]["properties"]
    pd = pytest.importorskip("pandas")
    assert "polarization" in sel.to_dataframe().columns


# --------------------------------------------------------------------------- #
# The web app wiring
#
# Behaviour was checked by booting the real app in a headless browser against
# the real catalog: 14,920 scenes, 9,425 VV, 5,495 HH, matching the client
# exactly. CI has no browser, so these keep the wiring from being deleted.
# --------------------------------------------------------------------------- #
APP = (ROOT / "js" / "app.js").read_text()
INDEX = (ROOT / "index.html").read_text()


def test_the_app_has_a_polarization_control():
    assert 'id="polSel"' in INDEX
    assert 'id="polVal"' in INDEX


def test_the_app_filters_on_polarization():
    assert re.search(r"if \(f\.pol\s+&&", APP), "no polarization clause in the filter"
    assert "function scenePols" in APP
    assert "populatePolarizations" in APP


def test_the_app_splits_polarization_rather_than_comparing_the_string():
    """A dual-pol scene must match on a channel, not on "HH, HV" exactly."""
    fn = re.search(r"function scenePols\(p\) \{(.+?)\n\}", APP, re.S).group(1)
    assert ".split(','" in fn, "scenePols should split on the separator"


def test_polarization_is_in_the_shareable_url():
    assert "p.set('pol'" in APP
    assert "p.get('pol')" in APP


def test_reset_clears_the_polarization_filter():
    reset = APP[APP.index("document.getElementById('resetBtn')"):]
    reset = reset[:reset.index("showToast('Filters reset')")]
    assert "polSel" in reset, "reset leaves the polarization filter applied"
