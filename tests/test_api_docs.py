"""
Tests that api.html describes the API that actually exists.

Documentation drifts silently. Nothing fails when a page promises an endpoint
that was renamed or a field that was dropped, and the reader finds out instead
of the author — which is the same shape as every other problem in this
repository's history. So the page is checked against a real build.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PAGE = ROOT / "api.html"
APIJS = ROOT / "js" / "api.js"
BASE_URL = "https://www.pmuguda.com/open-sar-triad/api/v1"


@pytest.fixture(scope="module")
def built(tmp_path_factory, catalog_module_scope):
    """A real api/v1 tree, built from the test catalog."""
    spec = importlib.util.spec_from_file_location(
        "build_api", ROOT / "scripts" / "build_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tmp = tmp_path_factory.mktemp("api")
    src = tmp / "scenes.geojson"
    src.write_text(json.dumps(catalog_module_scope))
    mod.SRC = src
    mod.OUT = tmp / "api" / "v1"
    assert mod.main() == 0
    return mod.OUT


@pytest.fixture(scope="module")
def catalog_module_scope():
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import make_catalog
    return make_catalog()


@pytest.fixture(scope="module")
def page():
    return PAGE.read_text()


def documented_endpoints(page):
    """Every `GET /path` the page lists, as a relative path."""
    return [m.group(1).lstrip("/")
            for m in re.finditer(r"<code>GET (/[^<]+)</code>", page)]


def test_the_page_documents_some_endpoints(page):
    assert len(documented_endpoints(page)) >= 6


def test_every_documented_endpoint_exists(page, built):
    """The core check: a path on the page must be a file in a real build."""
    missing = []
    for ep in documented_endpoints(page):
        # {provider} is a placeholder, so try it against a real provider.
        for candidate in ([ep] if "{provider}" not in ep
                          else [ep.replace("{provider}", p)
                                for p in ("iceye", "umbra", "capella")]):
            if not (built / candidate).is_file():
                missing.append(candidate)
    assert not missing, f"documented but not built: {missing}"


def test_every_built_endpoint_is_documented(page, built):
    """And the reverse, so a new endpoint cannot ship undocumented."""
    documented = set(documented_endpoints(page))
    undocumented = []
    for f in sorted(built.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(built).as_posix()
        generic = re.sub(r"(collections|items|scenes)/[^/]+(\.\w+)$",
                         r"\1/{provider}\2", rel)
        if rel not in documented and generic not in documented:
            undocumented.append(rel)
    assert not undocumented, f"built but not documented: {undocumented}"


def test_the_documented_index_fields_match_the_build(page, built):
    """The page shows the `fields` array; readers zip it against each row."""
    doc = json.loads((built / "index.json").read_text())
    for field in doc["fields"]:
        assert f'"{field}"' in page, f"index field {field!r} is not on the page"


def test_the_documented_families_match_the_client(page):
    from opensartriad import FAMILIES
    for fam in FAMILIES:
        assert f'resolve("{fam}")' in page, f"family {fam!r} is not documented"


def test_the_documented_stac_properties_exist(page, built):
    item = json.loads((built / "items" / "umbra.json").read_text())["features"][0]
    for prop in ("sar:instrument_mode", "sat:orbit_state", "ost:formats",
                 "ost:first_seen"):
        assert prop in item["properties"], f"{prop} is not in a built Item"
        assert prop in page, f"{prop} is documented nowhere"


def test_the_base_url_matches_the_builder(page):
    spec = importlib.util.spec_from_file_location(
        "build_api", ROOT / "scripts" / "build_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.BASE_URL == BASE_URL
    assert BASE_URL in page, "the page shows a different base URL from the builder"


def test_metadata_sidecars_are_real(built):
    """The page promises every asset has a `_metadata` sibling."""
    item = json.loads((built / "items" / "umbra.json").read_text())["features"][0]
    data_keys = [k for k in item["assets"] if not k.endswith("_metadata")]
    assert data_keys
    for k in data_keys:
        assert f"{k}_metadata" in item["assets"], f"{k} has no sidecar"


def test_every_endpoint_carries_the_licence_the_page_claims(built):
    for f in built.rglob("*"):
        if f.is_file():
            assert json.loads(f.read_text())["license"] == "CC-BY-4.0", f


# --------------------------------------------------------------------------- #
# The page itself
# --------------------------------------------------------------------------- #
def test_the_console_links_to_the_page():
    index = (ROOT / "index.html").read_text()
    assert 'href="api.html"' in index, "no way to reach the docs from the map"


def test_the_page_links_back_to_the_console(page):
    assert 'href="./"' in page


def test_the_page_is_in_the_service_worker_shell():
    """Otherwise the installed PWA serves the console offline but not the docs."""
    sw = (ROOT / "sw.js").read_text()
    for asset in ("./api.html", "./css/api.css", "./js/api.js"):
        assert asset in sw, f"{asset} is not precached"


def test_asset_cache_busts_match_between_page_and_worker(page):
    """A stale cached stylesheet against a new page is how themes break."""
    sw = (ROOT / "sw.js").read_text()
    for m in re.finditer(r'(css/api\.css|js/api\.js)\?v=([\w.-]+)', page):
        assert f"{m.group(1)}?v={m.group(2)}" in sw, \
            f"{m.group(1)} is {m.group(2)} on the page but differs in sw.js"


def test_the_page_has_no_inline_script(page):
    """Its CSP is script-src 'self', so an inline script would silently die."""
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page)


def test_the_csp_allows_the_api_call(page):
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert "connect-src 'self'" in csp, "the live figures fetch would be blocked"
    assert "script-src 'self'" in csp


def test_the_live_fetch_is_relative():
    """So the page works on a fork, a local server and the real domain alike."""
    js = APIJS.read_text()
    assert "fetch('api/v1/stats.json'" in js
    assert "https://www.pmuguda.com" not in js


# --------------------------------------------------------------------------- #
# The button cluster
#
# The three map-overlay buttons are positioned from the right edge. Before the
# API button was added, the narrow-screen block restated `right` on .helpbtn,
# which at equal specificity and later source order dropped the visitors button
# underneath the help button on every screen below 860px. No test could see it.
# --------------------------------------------------------------------------- #
STYLE = (ROOT / "css" / "style.css").read_text()


def test_each_overlay_button_has_its_own_offset():
    for sel in (".usagebtn {", ".apibtn {"):
        assert sel in STYLE, f"{sel} has no rule of its own"
    assert "--hb-step" in STYLE, "offsets should derive from one step value"


def test_the_narrow_screen_block_moves_the_cluster_without_flattening_it():
    """Setting `right` directly there is what stacked the buttons before."""
    narrow = STYLE[STYLE.index("@media (max-width: 860px)"):]
    narrow = narrow[:narrow.index("@media", 10)] if "@media" in narrow[10:] else narrow
    rule = re.search(r"\.helpbtn\s*\{([^}]*)\}", narrow)
    assert rule, ".helpbtn is no longer adjusted for narrow screens"
    body = rule.group(1)
    assert not re.search(r"(^|;)\s*right\s*:", body), (
        "setting `right` on .helpbtn here overrides .usagebtn and .apibtn, "
        "which both have equal specificity and come earlier in the file"
    )
    assert "--hb-edge" in body, "the cluster should move via the edge variable"


def test_the_api_button_is_a_link_not_a_button():
    """It navigates to a page, so middle-click and open-in-new-tab must work."""
    index = (ROOT / "index.html").read_text()
    assert re.search(r'<a[^>]*id="apiBtn"[^>]*href="api\.html"', index)
