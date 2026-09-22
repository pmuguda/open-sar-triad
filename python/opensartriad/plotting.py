"""
Plotting helpers.

matplotlib is an optional dependency: importing this module does not require it,
and every entry point raises a clear install hint if it is missing. That keeps
``pip install open-sar-triad`` free of dependencies.

The basemap is the same country geometry the web app draws, world-atlas
``countries-110m``, decoded from TopoJSON here rather than pulling in cartopy or
contextily. Decoding is roughly forty lines of arithmetic and costs nothing at
install time; cartopy would need PROJ and GEOS compiled, contextily needs
rasterio. The file is fetched once and cached on disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

#: Tried in order. The first is the same file the web app draws, so the Python
#: and browser basemaps match. The rest are fallbacks: corporate proxies and
#: air-gapped networks often block public CDNs, and a blocked basemap must not
#: take the whole plot down with it. TopoJSON and GeoJSON are both accepted.
WORLD_SOURCES = (
    "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json",
    "https://unpkg.com/world-atlas@2/countries-110m.json",
    "https://raw.githubusercontent.com/martynafford/natural-earth-geojson"
    "/master/110m/cultural/ne_110m_admin_0_countries.json",
)
WORLD_URL = WORLD_SOURCES[0]

#: A polygon crossing +/-180 (Antarctica, Russia, Fiji) draws as a streak across
#: a plate-carree plot unless it is split there first. The web map dropped
#: Antarctica outright; splitting is better, because SAR over the ice sheet is a
#: real use case and deleting the continent leaves those scenes with no basemap.
_ANTIMERIDIAN_JUMP = 180.0

PROVIDER_COLORS = {
    "iceye": "#00FF87",
    "umbra": "#00C9FF",
    "capella": "#FF6B35",
}

_world_cache: list | None = None


def _require_matplotlib():
    try:
        import matplotlib.pyplot as plt  # noqa: F401
        return plt
    except ImportError as e:  # pragma: no cover - exercised via the no-deps CI job
        raise ImportError(
            "Plotting needs matplotlib, which is an optional dependency.\n"
            "    pip install 'open-sar-triad[plot]'"
        ) from e


# --------------------------------------------------------------------------- #
# Basemap
# --------------------------------------------------------------------------- #
def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    d = Path(base) / "opensartriad"
    d.mkdir(parents=True, exist_ok=True)
    return d / "countries-110m.json"


def _decode_arc(arc, scale, translate):
    """TopoJSON arcs are delta-encoded and quantized; walk the deltas and
    project back to degrees."""
    sx, sy = scale
    tx, ty = translate
    x = y = 0
    out = []
    for dx, dy in arc:
        x += dx
        y += dy
        out.append((x * sx + tx, y * sy + ty))
    return out


def _ring(arc_indices, arcs):
    """Stitch a ring from arc indices. A negative index means that arc traversed
    backwards, encoded as the ones' complement."""
    pts: list = []
    for i in arc_indices:
        a = arcs[~i][::-1] if i < 0 else arcs[i]
        # consecutive arcs share their join point, so drop the duplicate
        pts.extend(a[1:] if pts else a)
    return pts


def _emit(ring, out: list) -> None:
    """Split a ring wherever it jumps the antimeridian, then keep the pieces
    that are still real polygons."""
    if len(ring) < 3:
        return
    closed = ring[0] == ring[-1]
    pts = ring[:-1] if closed else ring
    if len(pts) < 3:
        return

    parts, cur = [], [pts[0]]
    for prev, pt in zip(pts, pts[1:]):
        if abs(pt[0] - prev[0]) > _ANTIMERIDIAN_JUMP:
            parts.append(cur)
            cur = [pt]
        else:
            cur.append(pt)
    parts.append(cur)

    # A ring is cyclic, so when it is split the final fragment continues into
    # the first one. Joining them keeps that side whole; without this both
    # halves are usually short enough to be discarded and the shape loses an
    # edge (Antarctica lost its entire eastern side this way).
    if closed and len(parts) > 1 and \
            abs(parts[0][0][0] - parts[-1][-1][0]) <= _ANTIMERIDIAN_JUMP:
        parts[0] = parts[-1] + parts[0]
        parts.pop()

    out.extend(p for p in parts if len(p) >= 3)


def parse_world(raw: dict) -> list:
    """Country outlines from either a TopoJSON Topology or a GeoJSON
    FeatureCollection, as plain lists of (lon, lat) tuples."""
    rings: list = []

    if raw.get("type") == "Topology":
        tr = raw.get("transform") or {}
        scale = tr.get("scale", [1, 1])
        translate = tr.get("translate", [0, 0])
        arcs = [_decode_arc(a, scale, translate) for a in raw["arcs"]]
        objects = raw.get("objects") or {}
        layer = objects.get("countries") or next(iter(objects.values()), {})
        for geom in layer.get("geometries", []):
            if geom.get("type") == "Polygon":
                polys = [geom["arcs"]]
            elif geom.get("type") == "MultiPolygon":
                polys = geom["arcs"]
            else:
                continue
            for poly in polys:
                for r in poly:
                    _emit(_ring(r, arcs), rings)
        return rings

    # GeoJSON FeatureCollection
    for feat in raw.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") == "Polygon":
            polys = [geom["coordinates"]]
        elif geom.get("type") == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            continue
        for poly in polys:
            for r in poly:
                _emit([(pt[0], pt[1]) for pt in r], rings)
    return rings


def load_world(url: str | None = None, use_cache: bool = True) -> list:
    """Country outlines, fetched once and cached on disk.

    Tries each source in :data:`WORLD_SOURCES` until one answers, so a blocked
    CDN is survivable. Raises :class:`OpenSarTriadError` only if every source
    fails; callers that merely want a backdrop should catch it.
    """
    global _world_cache
    if _world_cache is not None:
        return _world_cache

    cache = _cache_path()
    raw = None
    if use_cache and cache.exists():
        try:
            raw = json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            raw = None

    if raw is None:
        from .client import OpenSarTriadError, _fetch_json
        sources = [url] if url else list(WORLD_SOURCES)
        errors = []
        for src in sources:
            try:
                raw = _fetch_json(src)
                break
            except Exception as e:  # try the next mirror
                errors.append(f"{src}: {e}")
        if raw is None:
            raise OpenSarTriadError(
                "Could not load country outlines for the basemap from any source:\n  "
                + "\n  ".join(errors))
        if use_cache:
            try:
                cache.write_text(json.dumps(raw))
            except OSError:
                pass  # a read-only cache dir must not break plotting

    _world_cache = parse_world(raw)
    return _world_cache


def draw_basemap(ax, *, facecolor="#2a2f3a", edgecolor="#5a6273",
                 linewidth=0.4, zorder=0, required=False):
    """Draw country outlines onto an existing Axes.

    With ``required=False`` (the default) a basemap that cannot be fetched is
    reported as a warning and skipped, so the scenes still plot. That matters on
    networks where public CDNs are blocked.
    """
    _require_matplotlib()
    from matplotlib.collections import PolyCollection

    try:
        rings = load_world()
    except Exception as e:
        if required:
            raise
        import warnings
        warnings.warn(f"Basemap unavailable, plotting without it. {e}", stacklevel=2)
        return ax

    ax.add_collection(PolyCollection(
        rings, facecolors=facecolor, edgecolors=edgecolor,
        linewidths=linewidth, zorder=zorder))
    return ax


#: A SAR footprint is a few kilometres across. Drawn at true scale on a
#: continental or world view that is a fraction of one pixel, which is why a
#: search returning ninety scenes could look like an empty map. Anything that
#: would come out smaller than this many pixels is drawn as a marker instead.
MIN_FOOTPRINT_PX = 7.0

#: Repeat tasking means many scenes share one target. Merging markers that land
#: within this many pixels of each other, and scaling the merged marker by how
#: many scenes it stands for, turns ninety overlapping invisible boxes into one
#: dot whose size says ninety.
MARKER_MERGE_PX = 9.0


def _deg_per_pixel(ax, view):
    """Degrees of longitude per pixel at the given view.

    Computed from the axes rectangle rather than a rendered figure so it works
    before the first draw. ``set_aspect('equal', adjustable='box')`` shrinks the
    axes to whichever dimension is binding, so the larger of the two ratios is
    the scale that actually applies.
    """
    fig = ax.figure
    pos = ax.get_position()
    w_in, h_in = fig.get_size_inches()
    w_px = max(pos.width * w_in * fig.dpi, 1.0)
    h_px = max(pos.height * h_in * fig.dpi, 1.0)
    return max((view[2] - view[0]) / w_px, (view[3] - view[1]) / h_px)


def _merge_markers(points, cell):
    """Group ``(x, y)`` points that fall within ``cell`` degrees of each other.

    Returns ``(xs, ys, counts)``, each group placed at the mean of its members
    so a merged marker sits on the scenes it represents.

    Grouping is by distance to a group's running centroid, not by which cell of
    a fixed grid a point lands in. A grid would cut any cluster unlucky enough
    to straddle a boundary, so the same target would draw as one dot or two
    depending on where it sits on the globe, and marker size would stop meaning
    what it claims to mean. The grid survives only as a lookup: a point can join
    a group seeded in any of the nine cells around it, which keeps this linear
    rather than comparing every point to every group.
    """
    import math

    if cell <= 0:
        return [x for x, _ in points], [y for _, y in points], [1] * len(points)

    grid: dict = {}
    groups: list = []
    limit = cell * cell
    for x, y in points:
        cx, cy = math.floor(x / cell), math.floor(y / cell)
        best, best_d = None, limit
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for g in grid.get((i, j), ()):
                    dx, dy = x - g[1] / g[0], y - g[2] / g[0]
                    d = dx * dx + dy * dy
                    if d < best_d:
                        best, best_d = g, d
        if best is None:
            g = [1, x, y]
            groups.append(g)
            grid.setdefault((cx, cy), []).append(g)
        else:
            best[0] += 1
            best[1] += x
            best[2] += y

    xs = [g[1] / g[0] for g in groups]
    ys = [g[2] / g[0] for g in groups]
    counts = [g[0] for g in groups]
    return xs, ys, counts


def _marker_area(count, base):
    """Marker area in points squared for a bin holding ``count`` scenes.

    Area grows with the logarithm of the count, so a target imaged a hundred
    times reads as clearly busier than one imaged once without swamping the map,
    and a single scene still lands on the visibility floor.
    """
    import math
    return base * (1.0 + 1.5 * math.log10(count)) ** 2


def _style_map_axes(ax, bbox=None, *, background="#161a21", grid=True):
    if bbox is None:
        ax.set_xlim(-180, 180)
        ax.set_ylim(-90, 90)
    else:
        ax.set_xlim(bbox[0], bbox[2])
        ax.set_ylim(bbox[1], bbox[3])
    ax.set_facecolor(background)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal", adjustable="box")
    if grid:
        ax.grid(alpha=.12, linewidth=.5)
    return ax


# --------------------------------------------------------------------------- #
# Plots
# --------------------------------------------------------------------------- #
def plot_coverage(scenes, ax=None, *, basemap=True, footprints=False,
                  bbox=None, alpha=0.35, linewidth=0.4, figsize=(13, 6.5),
                  legend=True, title=None, markers=True,
                  min_footprint_px=MIN_FOOTPRINT_PX, marker_size=26):
    """Scene coverage on a world map, coloured by provider.

    By default this draws each scene's bounding box, which comes free with the
    search index. Pass ``footprints=True`` for true acquisition polygons, which
    is more accurate but downloads the full per-provider records first.

    Footprints too small to see at the current zoom are drawn as markers, and
    markers landing on the same spot are merged and scaled by how many scenes
    they stand for. Without that a wide view of real SAR data renders nothing:
    the scenes are there, they are just thinner than a pixel. Pass
    ``markers=False`` for the literal geometry and nothing else.
    """
    plt = _require_matplotlib()
    from matplotlib.collections import PolyCollection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    if ax is None:
        _, ax = plt.subplots(figsize=figsize)

    # Set the limits before drawing: the pixel scale, and so the decision about
    # what is too small to see, depends on the view being final.
    _style_map_axes(ax, bbox)
    if basemap:
        draw_basemap(ax)

    view = (bbox[0], bbox[1], bbox[2], bbox[3]) if bbox else (-180, -90, 180, 90)
    deg_per_px = _deg_per_pixel(ax, view)
    min_deg = min_footprint_px * deg_per_px
    merge_deg = MARKER_MERGE_PX * deg_per_px

    polys_by: dict[str, list] = {}
    dots_by: dict[str, list] = {}
    totals: dict[str, int] = {}
    for s in scenes:
        totals[s.provider] = totals.get(s.provider, 0) + 1
        w, so, e, n = s.bbox
        if markers and max(e - w, n - so) < min_deg:
            dots_by.setdefault(s.provider, []).append(((w + e) / 2, (so + n) / 2))
            continue
        if footprints:
            geom = s.geometry
            if geom.get("type") == "Polygon":
                polys = [geom["coordinates"][0]]
            elif geom.get("type") == "MultiPolygon":
                polys = [p[0] for p in geom["coordinates"]]
            else:
                continue
            polys_by.setdefault(s.provider, []).extend(
                [[(x, y) for x, y, *_ in ring] for ring in polys])
        else:
            polys_by.setdefault(s.provider, []).append(
                [(w, so), (e, so), (e, n), (w, n)])

    merged_any = False
    handles = []
    for provider in sorted(totals, key=lambda p: -totals[p]):
        colour = PROVIDER_COLORS.get(provider, "#cccccc")
        polys = polys_by.get(provider)
        if polys:
            ax.add_collection(PolyCollection(
                polys, facecolors=colour, edgecolors=colour, alpha=alpha,
                linewidths=linewidth, zorder=2))
        dots = dots_by.get(provider)
        if dots:
            xs, ys, counts = _merge_markers(dots, merge_deg)
            merged_any = merged_any or any(c > 1 for c in counts)
            ax.scatter(xs, ys, s=[_marker_area(c, marker_size) for c in counts],
                       facecolors=colour, edgecolors="#0d1016", linewidths=.5,
                       alpha=.85, zorder=3)
        handles.append(Patch(facecolor=colour, edgecolor=colour,
                             alpha=min(1, alpha * 2),
                             label=f"{provider} ({totals[provider]:,})"))

    if merged_any:
        handles.append(Line2D([], [], linestyle="none", marker="o",
                              markerfacecolor="#8b93a3", markeredgecolor="#0d1016",
                              markersize=6, label="larger dot = more scenes"))

    ax.set_title(title or f"Scene coverage ({len(scenes):,} scenes)")
    if legend and handles:
        ax.legend(handles=handles, loc="lower left", framealpha=.85, fontsize=9)
    return ax


def plot_footprint(scene, ax=None, *, basemap=True, pad=6.0,
                   figsize=(7, 6), title=None, locator=True):
    """One scene's footprint, with enough surrounding context to place it.

    ``pad`` degrees of context around a footprint a few kilometres wide leaves
    the footprint smaller than a pixel, so a ring is drawn around it to say
    where to look. Pass ``locator=False`` for the polygon alone.
    """
    plt = _require_matplotlib()
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)

    geom = scene.geometry
    if geom.get("type") == "Polygon":
        rings = [geom["coordinates"][0]]
    elif geom.get("type") == "MultiPolygon":
        rings = [p[0] for p in geom["coordinates"]]
    else:
        rings = []

    w, s, e, n = scene.bbox
    view = (w - pad, s - pad, e + pad, n + pad)
    _style_map_axes(ax, view)
    if basemap:
        draw_basemap(ax)

    colour = PROVIDER_COLORS.get(scene.provider, "#cccccc")
    for ring in rings:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        ax.fill(xs, ys, facecolor=colour, edgecolor=colour, alpha=.4,
                linewidth=1.6, zorder=3)

    if locator:
        deg_per_px = _deg_per_pixel(ax, view)
        if max(e - w, n - s) < MIN_FOOTPRINT_PX * deg_per_px:
            ax.scatter([(w + e) / 2], [(s + n) / 2], s=260, facecolors="none",
                       edgecolors=colour, linewidths=1.2, alpha=.9, zorder=4)

    ax.set_title(title or f"{scene.provider} · {scene.date} · {scene.mode or 'n/a'}",
                 fontsize=10)
    return ax


def plot_timeline(scenes, ax=None, *, freq="month", stacked=True,
                  figsize=(12, 4), title=None):
    """Acquisitions over time, stacked by provider. ``freq`` is 'month' or 'year'."""
    plt = _require_matplotlib()
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)

    cut = 7 if freq == "month" else 4
    buckets: dict[str, dict[str, int]] = {}
    providers: set[str] = set()
    for s in scenes:
        if not s.date:
            continue
        key = s.date[:cut]
        buckets.setdefault(key, {})
        buckets[key][s.provider] = buckets[key].get(s.provider, 0) + 1
        providers.add(s.provider)

    keys = sorted(buckets)
    if not keys:
        ax.set_title("No dated scenes")
        return ax

    order = sorted(providers, key=lambda p: -sum(b.get(p, 0) for b in buckets.values()))
    bottom = [0] * len(keys)
    for provider in order:
        vals = [buckets[k].get(provider, 0) for k in keys]
        ax.bar(keys, vals, bottom=bottom if stacked else None,
               label=provider, color=PROVIDER_COLORS.get(provider, "#cccccc"),
               width=.85, zorder=2)
        if stacked:
            bottom = [b + v for b, v in zip(bottom, vals)]

    step = max(1, len(keys) // 14)
    ax.set_xticks(range(0, len(keys), step))
    ax.set_xticklabels(keys[::step], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("scenes")
    ax.set_title(title or f"Acquisitions per {freq} ({len(scenes):,} scenes)")
    ax.grid(axis="y", alpha=.15, zorder=0)
    ax.legend(fontsize=9)
    return ax


def plot_providers(scenes, ax=None, *, figsize=(7, 3.6), title=None):
    """Scene count per provider."""
    plt = _require_matplotlib()
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)

    counts: dict[str, int] = {}
    for s in scenes:
        counts[s.provider] = counts.get(s.provider, 0) + 1
    items = sorted(counts.items(), key=lambda kv: kv[1])

    names = [k for k, _ in items]
    vals = [v for _, v in items]
    ax.barh(names, vals, color=[PROVIDER_COLORS.get(n, "#cccccc") for n in names],
            zorder=2)
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v:,}", va="center", fontsize=9)
    ax.set_xlabel("scenes")
    ax.set_title(title or f"Scenes by provider ({len(scenes):,} total)")
    ax.grid(axis="x", alpha=.15, zorder=0)
    ax.margins(x=.12)
    return ax
