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


# --------------------------------------------------------------------------- #
# Visibility of small footprints
#
# A SAR footprint is a few kilometres across. On a world or continental view
# that is thinner than a pixel, so drawing it at true scale produced a map that
# looked empty while the title said "92 scenes". These tests pin the fix: small
# footprints become markers with a size floor, and markers that land on the same
# target merge and grow.
# --------------------------------------------------------------------------- #
from opensartriad import Scene  # noqa: E402


class _StubCatalog:
    """Serves each scene a rectangular geometry from its own bbox.

    plot_footprint asks for true geometry, which normally means a network fetch
    of the provider record. These tests are about marker sizing, not fetching.
    """

    def _record_for(self, provider, scene_id):
        w, s, e, n = self._bboxes[scene_id]
        return {"geometry": {"type": "Polygon",
                             "coordinates": [[(w, s), (e, s), (e, n), (w, n), (w, s)]]}}


def _make(provider, scene_id, bbox, **kw):
    cat = _StubCatalog()
    cat._bboxes = {scene_id: bbox}
    return Scene(scene_id, provider, "2025-01-01", kw.pop("mode", "spotlight"),
                 None, None, formats=["GEC"], bbox=list(bbox), _catalog=cat)


def _tiny(provider="umbra", lon=10.0, lat=50.0, size=0.02, n=1, jitter=0.04):
    """Scenes with a realistically small footprint over one target.

    Repeat tasking never lands on exactly the same coordinates, so these are
    spread by a few hundredths of a degree. Without that, the merge test would
    pass on identical bin keys alone and say nothing about the cell size.
    """
    out = []
    for i in range(n):
        x, y = lon + (i % 7) * jitter, lat + (i % 5) * jitter
        out.append(_make(provider, f"{provider}-{lon}-{lat}-{i}",
                         (x, y, x + size, y + size)))
    return out


def _scatters(ax):
    from matplotlib.collections import PathCollection
    return [c for c in ax.collections if isinstance(c, PathCollection)]


def test_merge_markers_groups_by_distance_and_averages_position():
    xs, ys, counts = P._merge_markers(
        [(0.0, 0.0), (0.2, 0.0), (10.0, 10.0)], cell=1.0)
    order = sorted(zip(counts, xs, ys))
    assert [c for c, _, _ in order] == [1, 2]
    assert order[1][1] == pytest.approx(0.1)   # mean of the two, not a grid node


@pytest.mark.parametrize("shift", [0.0, 0.25, 0.5, 0.75, 0.9])
def test_merge_markers_does_not_split_a_cluster_on_a_grid_boundary(shift):
    """A fixed grid cut clusters that straddled a cell edge.

    The same five scenes over one target have to merge into one marker wherever
    that target happens to sit, otherwise marker size reports position rather
    than how busy the target is.
    """
    pts = [(shift + i * 0.02, shift + i * 0.02) for i in range(5)]
    _, _, counts = P._merge_markers(pts, cell=1.0)
    assert counts == [5], f"cluster split at offset {shift}"


def test_merge_markers_still_separates_targets_further_than_a_cell():
    _, _, counts = P._merge_markers([(0.0, 0.0), (0.0, 3.0)], cell=1.0)
    assert sorted(counts) == [1, 1]


def test_merge_markers_keeps_distinct_targets_apart():
    xs, _, counts = P._merge_markers([(0.0, 0.0), (5.0, 0.0)], cell=1.0)
    assert sorted(counts) == [1, 1]
    assert len(xs) == 2


def test_marker_area_grows_with_count_but_sublinearly():
    one, ten, hundred = (P._marker_area(n, 26) for n in (1, 10, 100))
    assert one == 26
    assert ten > one and hundred > ten
    assert hundred < 100 * one, "a busy target must not swamp the map"


def test_tiny_footprints_are_drawn_as_markers(world):
    """The regression: at world scale these polygons are sub-pixel."""
    ax = P.plot_coverage(_tiny(n=3))
    offsets = [o for c in _scatters(ax) for o in c.get_offsets()]
    assert offsets, "sub-pixel footprints drew nothing visible"


def test_markers_meet_a_minimum_on_screen_size(world):
    ax = P.plot_coverage(_tiny(n=1), marker_size=26)
    sizes = [s for c in _scatters(ax) for s in c.get_sizes()]
    assert sizes and min(sizes) >= 26


def test_repeat_acquisitions_merge_into_one_larger_marker(world):
    """Ninety scenes over one target is one bright dot, not ninety invisible ones."""
    busy = _tiny(lon=10.0, lat=50.0, n=90) + _tiny(lon=-60.0, lat=-20.0, n=1)
    ax = P.plot_coverage(busy)
    sizes = sorted(s for c in _scatters(ax) for s in c.get_sizes())
    assert len(sizes) == 2, "repeat visits should collapse to one marker per target"
    assert sizes[1] > sizes[0]


def test_large_footprints_stay_polygons(world):
    from matplotlib.collections import PolyCollection
    big = [_make("umbra", "big", (0, 0, 40, 30), mode="stripmap")]
    ax = P.plot_coverage(big)
    assert not _scatters(ax), "a footprint this size does not need a marker"
    assert any(isinstance(c, PolyCollection) for c in ax.collections)


def test_zooming_in_turns_markers_back_into_footprints(world):
    """The same scene is a marker at world scale and a polygon up close."""
    one = _tiny(size=0.5)
    assert _scatters(P.plot_coverage(one))
    plt.close("all")
    assert not _scatters(P.plot_coverage(one, bbox=(9, 49, 11, 51)))


def test_markers_can_be_switched_off(world):
    ax = P.plot_coverage(_tiny(n=3), markers=False)
    assert not _scatters(ax)


def test_legend_counts_scenes_not_polygons(world):
    """It used to label from the polygon list, which markers would have emptied."""
    ax = P.plot_coverage(_tiny(provider="umbra", n=5))
    labels = [t.get_text() for t in ax.get_legend().get_texts()]
    assert "umbra (5)" in labels


def test_plot_footprint_rings_a_scene_too_small_to_see(world):
    scene = _tiny(size=0.02)[0]
    assert _scatters(P.plot_footprint(scene, pad=8))
    plt.close("all")
    assert not _scatters(P.plot_footprint(scene, pad=8, locator=False))


def test_plot_footprint_leaves_a_large_scene_alone(world):
    big = _make("umbra", "big", (0, 0, 20, 15), mode="stripmap")
    assert not _scatters(P.plot_footprint(big, pad=2))
