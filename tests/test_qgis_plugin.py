"""
Tests for the QGIS plugin.

Two layers. The first runs anywhere: the plugin's packaging, its metadata, the
vendored client, and the record builder that decides what the attribute table
shows. The second needs a QGIS build (``python3-qgis`` or the qgis/qgis
container) and exercises the real thing headlessly: the footprint layer, the
dock's filters, a search driven from a map canvas extent, and a download. On a
machine without QGIS those skip, and CI runs them in a QGIS container so the
skip is never the only coverage.
"""

import configparser
import importlib.util
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import pytest

from conftest import make_catalog

ROOT = Path(__file__).parent.parent
PLUGIN = ROOT / "qgis" / "open_sar_triad"
sys.path.insert(0, str(ROOT / "qgis"))

from open_sar_triad import records  # noqa: E402
from open_sar_triad.vendor import FAMILIES, Catalog  # noqa: E402

spec = importlib.util.spec_from_file_location("build_qgis_plugin", ROOT / "scripts" / "build_qgis_plugin.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.fixture
def api(tmp_path, catalog):
    """A built API, addressable as a file:// base URL (same as test_client)."""
    s = importlib.util.spec_from_file_location("build_api", ROOT / "scripts" / "build_api.py")
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
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
# Packaging
# --------------------------------------------------------------------------- #
def test_the_vendored_client_is_the_real_client():
    """Two copies of the client is one too many unless one is derived from the
    other. The build script syncs it; this fails if someone edited the copy or
    changed the original without rebuilding."""
    assert build.vendor_is_current(), \
        "qgis/open_sar_triad/vendor/client.py differs from python/opensartriad/client.py; " \
        "run scripts/build_qgis_plugin.py"


def test_metadata_has_what_the_plugin_repository_requires():
    cfg = configparser.ConfigParser()
    cfg.read(PLUGIN / "metadata.txt", encoding="utf-8")
    g = cfg["general"]
    for key in ("name", "qgisMinimumVersion", "description", "about", "version",
                "author", "email", "repository", "tracker", "homepage", "icon"):
        assert g.get(key), f"metadata.txt is missing {key}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", g["version"])
    assert (PLUGIN / g["icon"]).exists()
    assert g.get("experimental", "False") == "False"


def test_the_zip_is_laid_out_for_install_from_zip(tmp_path):
    out = build.build_zip(tmp_path)
    assert out.name == f"open_sar_triad-{build.version()}.zip"
    names = zipfile.ZipFile(out).namelist()
    assert "open_sar_triad/metadata.txt" in names
    assert "open_sar_triad/__init__.py" in names
    assert "open_sar_triad/vendor/client.py" in names
    assert "open_sar_triad/icon.png" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert all(n.startswith("open_sar_triad/") for n in names), \
        "QGIS expects one top-level folder named after the package"


def test_the_record_builder_does_not_need_qgis():
    """records.py is what the tests on plain Python cover; if it grows a qgis
    import that coverage silently becomes a skip."""
    text = (PLUGIN / "records.py").read_text()
    assert "from qgis" not in text and "import qgis" not in text


# --------------------------------------------------------------------------- #
# Records: what the attribute table shows
# --------------------------------------------------------------------------- #
def test_a_record_carries_every_declared_field(cat):
    rec = records.scene_record(cat.all()[0])
    for name, _ in records.FIELDS:
        assert name in rec
    assert rec["geometry"]["type"] == "Polygon"


def test_family_urls_resolve_per_provider(cat):
    umbra = next(s for s in cat.all() if s.provider == "umbra")
    rec = records.scene_record(umbra)
    assert rec["url_detected"].endswith("_GEC.tif")
    assert rec["url_complex"].endswith("_SICD.nitf")
    assert rec["url_phase"] is None, "the fixture publishes no CPHD"


def test_lists_are_flattened_for_the_table(cat):
    rec = records.scene_record(cat.all()[0])
    assert rec["polarization"] == "VV"
    assert isinstance(rec["formats"], str) and "," in rec["formats"]


def test_the_web_link_frames_the_scene(cat):
    s = cat.all()[0]
    url = records.web_url(s)
    assert url.startswith(records.WEB_APP + "#")
    assert f"from={s.date[:7]}" in url and f"to={s.date[:7]}" in url
    assert "bbox=" in url
    others = [p for p in records.PROVIDER_COLOURS if p != s.provider]
    assert all(p in url.split("hide=")[1].split("&")[0] for p in others)


def test_a_scene_without_a_bbox_links_to_the_app_root():
    from open_sar_triad.vendor import Scene
    assert records.web_url(Scene("x", "umbra", "2025-01-01", None, None, None)) == records.WEB_APP


# --------------------------------------------------------------------------- #
# Inside QGIS
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qgs():
    """A running QgsApplication, or a skip where QGIS is not installed.

    A skip, not a module-level one: the packaging and record tests above must
    still run on plain Python. In the QGIS container CI sets OST_REQUIRE_QGIS
    so a broken import fails loudly instead of skipping the whole point."""
    if os.environ.get("OST_REQUIRE_QGIS"):
        import qgis.core  # noqa: F401  (raises if the bindings are broken)
    else:
        pytest.importorskip("qgis.core")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import QgsApplication
    app = QgsApplication([], True)
    app.initQgis()
    yield app
    app.exitQgis()


def pump(app, until, timeout=30.0):
    """Run the event loop until a condition holds. QgsTask results come back
    through the loop, so a test that just waits on the thread never sees them."""
    deadline = time.time() + timeout
    while not until():
        app.processEvents()
        if time.time() > deadline:
            raise AssertionError("timed out waiting for the task")
        time.sleep(0.02)


class FakeIface:
    """The three things the dock asks QGIS for."""

    def __init__(self):
        from qgis.gui import QgsMapCanvas
        self.canvas = QgsMapCanvas()

    def mapCanvas(self):
        return self.canvas

    def messageBar(self):
        return None

    def mainWindow(self):
        return None


def test_the_layer_has_one_feature_per_scene_in_provider_colours(qgs, cat):
    from open_sar_triad.layer import build_layer
    recs = [records.scene_record(s) for s in cat.all()]
    layer = build_layer(recs)
    assert layer.isValid()
    assert layer.featureCount() == 12
    assert layer.crs().authid() == "EPSG:4326"
    assert [f.name() for f in layer.fields()] == [n for n, _ in records.FIELDS]
    cats = {c.value(): c.label() for c in layer.renderer().categories()}
    assert cats == {"iceye": "ICEYE", "umbra": "Umbra", "capella": "Capella"}
    assert {a.name() for a in layer.actions().actions()} == {"Open in the web app", "Open thumbnail"}
    f = next(layer.getFeatures())
    assert f.geometry().isGeosValid()


def test_a_multipolygon_footprint_is_accepted(qgs):
    from open_sar_triad.layer import _geojson_to_wkt
    from qgis.core import QgsGeometry
    wkt = _geojson_to_wkt({"type": "MultiPolygon", "coordinates": [
        [[[0, 0], [1, 0], [1, 1], [0, 0]]], [[[2, 2], [3, 2], [3, 3], [2, 2]]]]})
    assert QgsGeometry.fromWkt(wkt).isGeosValid()


@pytest.fixture
def dock(qgs, api):
    from open_sar_triad.dock import OpenSarTriadDock
    from qgis.core import QgsProject
    QgsProject.instance().clear()        # the project is global; drop earlier tests' layers
    iface = FakeIface()
    d = OpenSarTriadDock(iface)
    d.base_url.setText(api)
    d._connect_catalog()
    pump(qgs, lambda: d.search_btn.isEnabled())
    return d


def test_the_dock_reads_the_catalog_stats(dock):
    assert "12 scenes" in dock.status.text()
    assert "spotlight" in [dock.mode.itemText(i) for i in range(dock.mode.count())]
    assert "VV" in [dock.polarization.itemText(i) for i in range(dock.polarization.count())]


def test_an_unfiltered_search_adds_every_footprint(qgs, dock):
    from qgis.core import QgsProject
    dock.use_extent.setChecked(False)
    dock.search()
    pump(qgs, lambda: dock.layer is not None and dock.search_btn.isEnabled())
    assert dock.layer.featureCount() == 12
    assert QgsProject.instance().mapLayer(dock.layer.id()) is dock.layer
    assert "12 scene(s)" in dock.result.text()
    assert dock.dl_btn.isEnabled()


def test_the_canvas_extent_narrows_the_search(qgs, dock):
    """The fixture puts one scene per provider at longitude 10, 11, 12, 13."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
    dock.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    dock.iface.canvas.setExtent(QgsRectangle(9.5, 49.5, 10.5, 50.5))
    dock.use_extent.setChecked(True)
    assert dock.filters()["bbox"][0] == pytest.approx(9.5, abs=0.5)
    dock.search()
    pump(qgs, lambda: dock.layer is not None and dock.search_btn.isEnabled())
    assert dock.layer.featureCount() == 3


def test_a_projected_canvas_is_transformed_before_filtering(qgs, dock):
    """Most projects are in Web Mercator. The bbox must still be degrees."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
    dock.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    dock.iface.canvas.setExtent(QgsRectangle(1_050_000, 6_400_000, 1_180_000, 6_500_000))
    w, s, e, n = dock.canvas_bbox()
    assert 9 < w < 10 and 10 < e < 11 and 49 < s < 51 and 49 < n < 51


def test_a_second_search_replaces_the_layer_rather_than_stacking(qgs, dock):
    from qgis.core import QgsProject
    dock.use_extent.setChecked(False)
    dock.search()
    pump(qgs, lambda: dock.layer is not None and dock.search_btn.isEnabled())
    first = dock.layer.id()
    dock.search()
    pump(qgs, lambda: dock.layer is not None and dock.layer.id() != first and dock.search_btn.isEnabled())
    names = [l.name() for l in QgsProject.instance().mapLayers().values()]
    assert names.count(dock.layer.name()) == 1


def test_filters_follow_the_panel(dock):
    dock.providers["iceye"].setChecked(False)
    dock.mode.setCurrentText("spotlight")
    dock.use_dates.setChecked(True)
    dock.use_extent.setChecked(False)
    f = dock.filters()
    assert f["providers"] == ["umbra", "capella"]
    assert f["mode"] == "spotlight"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", f["start"])
    assert "bbox" not in f and "polarization" not in f


def test_download_task_fetches_skips_and_reports(qgs, tmp_path):
    """Driven directly (run() is a plain method), with file:// URLs standing in
    for the providers' storage."""
    from open_sar_triad.tasks import DownloadTask
    src = tmp_path / "src"
    src.mkdir()
    (src / "a_GEC.tif").write_bytes(b"x" * 100)
    (src / "a.json").write_bytes(b"{}")
    dest = tmp_path / "dest"
    (dest / "umbra").mkdir(parents=True)
    (dest / "umbra" / "a.json").write_bytes(b"already here")
    jobs = [
        {"scene": "a", "provider": "umbra", "format": "GEC", "kind": "data", "url": f"file://{src}/a_GEC.tif"},
        {"scene": "a", "provider": "umbra", "format": "GEC", "kind": "metadata", "url": f"file://{src}/a.json"},
        {"scene": "b", "provider": "umbra", "format": "GEC", "kind": "data", "url": f"file://{src}/missing.tif"},
    ]
    done = {}
    task = DownloadTask(jobs, dest, lambda ok, w, f, d: done.update(ok=ok, written=w, failed=f))
    assert task.run() is True
    task.finished(True)
    assert (dest / "umbra" / "a_GEC.tif").read_bytes() == b"x" * 100
    assert (dest / "umbra" / "a.json").read_bytes() == b"already here", "existing files are kept"
    assert len(done["written"]) == 2
    assert len(done["failed"]) == 1 and "missing.tif" in done["failed"][0]
    assert not list(dest.rglob("*.part")), "no partial files left behind"


def test_the_download_plan_is_one_product_per_scene(qgs, cat):
    from open_sar_triad.tasks import DownloadTask
    jobs = DownloadTask.plan(list(cat.all()), "detected")
    assert sum(j["kind"] == "data" for j in jobs) == 12


class FakeQgisInterface(FakeIface):
    """Enough of QgisInterface for the plugin's lifecycle: toolbar, menu, dock."""

    def __init__(self):
        super().__init__()
        self.toolbar, self.menu, self.docks = [], [], []

    def addToolBarIcon(self, a):
        self.toolbar.append(a)

    def removeToolBarIcon(self, a):
        self.toolbar.remove(a)

    def addPluginToWebMenu(self, name, a):
        self.menu.append((name, a))

    def removePluginWebMenu(self, name, a):
        self.menu.remove((name, a))

    def addDockWidget(self, area, dock):
        self.docks.append(dock)

    def removeDockWidget(self, dock):
        self.docks.remove(dock)


def test_the_plugin_loads_toggles_and_unloads_cleanly(qgs):
    """What QGIS does on enable, click and disable. plugin.py is otherwise the
    one file nothing else exercises, and a typo there breaks the install."""
    import open_sar_triad
    iface = FakeQgisInterface()
    plugin = open_sar_triad.classFactory(iface)
    plugin.initGui()
    assert len(iface.toolbar) == 1 and len(iface.menu) == 1
    assert not iface.toolbar[0].icon().isNull(), "icon.png did not load"

    plugin.toggle(True)
    assert len(iface.docks) == 1 and plugin.dock.isVisible() is not None
    plugin.toggle(False)
    assert not plugin.dock.isVisible()

    plugin.unload()
    assert not iface.toolbar and not iface.menu and not iface.docks
    assert plugin.dock is None and plugin.action is None
