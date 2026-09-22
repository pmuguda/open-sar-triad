"""
Tests for the plotting helpers.

Two things matter here beyond "does it draw": the TopoJSON/GeoJSON decoding has
to be arithmetically right (tested against synthetic input with known answers,
no network), and matplotlib has to stay genuinely optional.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "python"))

from opensartriad import Catalog  # noqa: E402
from opensartriad import plotting as P  # noqa: E402

mpl = pytest.importorskip("matplotlib")
mpl.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


@pytest.fixture
def api(tmp_path, catalog):
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
def scenes(api):
    return Catalog(api).all()


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A tiny synthetic basemap, so plot tests never touch the network."""
    rings = [[(0, 40), (10, 40), (10, 50), (0, 50)]]
    monkeypatch.setattr(P, "_world_cache", rings)
    return rings


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


# --------------------------------------------------------------------------- #
# TopoJSON decoding
# --------------------------------------------------------------------------- #
def test_decode_arc_accumulates_deltas_and_dequantizes():
    """TopoJSON stores quantized deltas; decoding must accumulate then apply
    scale and translate."""
    pts = P._decode_arc([[0, 0], [5, 0], [0, 10]], scale=[2.0, 1.0],
                        translate=[-10.0, -5.0])
    assert pts == [(-10.0, -5.0), (0.0, -5.0), (0.0, 5.0)]


def test_ring_stitches_arcs_and_drops_shared_join_point():
    arcs = [[(0, 0), (1, 0)], [(1, 0), (1, 1)]]
    assert P._ring([0, 1], arcs) == [(0, 0), (1, 0), (1, 1)]


def test_ring_reverses_negative_arc_indices():
    """A negative index is the ones' complement of the arc, traversed backwards."""
    assert P._ring([~0], [[(0, 0), (1, 1), (2, 2)]]) == [(2, 2), (1, 1), (0, 0)]


def test_parse_topojson_end_to_end():
    topo = {
        "type": "Topology",
        "transform": {"scale": [2.0, 1.0], "translate": [-10.0, -5.0]},
        "arcs": [[[0, 0], [5, 0], [0, 10], [-5, 0], [0, -10]]],
        "objects": {"countries": {"type": "GeometryCollection", "geometries": [
            {"type": "Polygon", "id": "999", "arcs": [[0]]}]}},
    }
    rings = P.parse_world(topo)
    assert len(rings) == 1
    assert rings[0][0] == (-10.0, -5.0)


def test_parse_geojson_end_to_end():
    gj = {"type": "FeatureCollection", "features": [
        {"geometry": {"type": "Polygon", "coordinates": [[[0, 40], [10, 40], [10, 50], [0, 40]]]}},
        {"geometry": {"type": "MultiPolygon", "coordinates": [
            [[[20, 0], [30, 0], [30, 10], [20, 0]]]]}},
    ]}
    assert len(P.parse_world(gj)) == 2


def test_parse_skips_non_polygon_geometries():
    gj = {"type": "FeatureCollection", "features": [
        {"geometry": {"type": "Point", "coordinates": [0, 0]}},
        {"geometry": None},
    ]}
    assert P.parse_world(gj) == []


# --------------------------------------------------------------------------- #
# Antimeridian handling
# --------------------------------------------------------------------------- #
def test_ring_crossing_antimeridian_is_split_not_dropped():
    """A polygon that jumps +/-180 renders as a streak across the map unless it
    is split. Both sides must survive: a ring is cyclic, so the final fragment
    continues into the first one and they have to be rejoined."""
    gj = {"type": "FeatureCollection", "features": [{"geometry": {
        "type": "Polygon", "coordinates": [[
            [170, -70], [179, -70], [-179, -70], [-170, -70],
            [-170, -75], [170, -75], [170, -70]]]}}]}
    parts = P.parse_world(gj)
    assert len(parts) == 2, "both sides of the antimeridian must be kept"
    for part in parts:
        span = max(p[0] for p in part) - min(p[0] for p in part)
        assert span < 180, "a fragment still streaks across the map"


def test_ordinary_ring_is_untouched():
    gj = {"type": "FeatureCollection", "features": [{"geometry": {
        "type": "Polygon", "coordinates": [[[0, 40], [10, 40], [10, 50], [0, 40]]]}}]}
    assert len(P.parse_world(gj)) == 1


def test_degenerate_rings_are_dropped():
    gj = {"type": "FeatureCollection", "features": [{"geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}}]}
    assert P.parse_world(gj) == []


# --------------------------------------------------------------------------- #
# Basemap resilience
# --------------------------------------------------------------------------- #
def test_basemap_failure_warns_but_still_plots(monkeypatch, scenes):
    """A blocked CDN is common on corporate networks and must not take the whole
    plot down with it."""
    monkeypatch.setattr(P, "_world_cache", None)
    monkeypatch.setattr(P, "load_world",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
    with pytest.warns(UserWarning, match="Basemap unavailable"):
        ax = scenes.plot_coverage()
    assert ax is not None


def test_basemap_failure_can_be_made_fatal(monkeypatch):
    monkeypatch.setattr(P, "load_world",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
    _, ax = plt.subplots()
    with pytest.raises(RuntimeError):
        P.draw_basemap(ax, required=True)


def test_load_world_tries_every_source(monkeypatch, tmp_path):
    """The first sources may be blocked; the loader must fall through."""
    monkeypatch.setattr(P, "_world_cache", None)
    monkeypatch.setattr(P, "_cache_path", lambda: tmp_path / "nope.json")
    tried = []

    def fake_fetch(url, *a, **k):
        tried.append(url)
        if len(tried) < 3:
            raise RuntimeError("blocked")
        return {"type": "FeatureCollection", "features": [{"geometry": {
            "type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}]}

    import opensartriad.client as C
    monkeypatch.setattr(C, "_fetch_json", fake_fetch)
    rings = P.load_world(use_cache=False)
    assert len(tried) == 3 and len(rings) == 1


# --------------------------------------------------------------------------- #
# The plots themselves
# --------------------------------------------------------------------------- #
def test_plot_coverage(world, scenes):
    ax = scenes.plot_coverage()
    assert ax.get_xlim() == (-180, 180)
    assert ax.get_ylim() == (-90, 90)
    assert len(ax.collections) >= 2          # basemap + at least one provider
    assert ax.get_legend() is not None


def test_plot_coverage_accepts_an_axes(world, scenes):
    """Returning/accepting Axes is what lets these compose into your own figure."""
    fig, axes = plt.subplots(1, 2)
    out = scenes.plot_coverage(ax=axes[0])
    assert out is axes[0]


def test_plot_coverage_with_true_footprints(world, scenes):
    ax = scenes.plot_coverage(footprints=True)
    assert len(ax.collections) >= 2


def test_plot_coverage_honours_bbox(world, scenes):
    ax = scenes.plot_coverage(bbox=(0, 40, 20, 60))
    assert ax.get_xlim() == (0, 20)


def test_plot_footprint(world, scenes):
    ax = scenes[0].plot_footprint(pad=5)
    w, s, e, n = scenes[0].bbox
    assert ax.get_xlim()[0] == pytest.approx(w - 5)
    assert ax.get_xlim()[1] == pytest.approx(e + 5)


def test_plot_timeline(world, scenes):
    ax = scenes.plot_timeline()
    assert ax.patches, "no bars drawn"
    assert ax.get_legend() is not None


def test_plot_timeline_by_year(world, scenes):
    ax = scenes.plot_timeline(freq="year")
    assert [t.get_text() for t in ax.get_xticklabels()][0].isdigit()


def test_plot_providers(world, scenes):
    ax = scenes.plot_providers()
    assert len(ax.patches) == 3          # one bar per provider


def test_plots_handle_an_empty_collection(world, api):
    empty = Catalog(api).search(providers="umbra", orbit="descending")
    assert len(empty) == 0
    empty.plot_coverage()
    empty.plot_timeline()
    empty.plot_providers()


def test_provider_colours_match_the_web_app():
    assert P.PROVIDER_COLORS == {
        "iceye": "#00FF87", "umbra": "#00C9FF", "capella": "#FF6B35"}
