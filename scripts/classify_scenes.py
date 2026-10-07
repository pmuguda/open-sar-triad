#!/usr/bin/env python3
"""
Tags every scene with what a map says is at its location.

This classifies the *place*, not the radar imagery. Nothing here looks at a
pixel: it intersects each scene's footprint against published geographic
vectors and reports what they contain. A footprint tagged `airport` covers an
airport; whether the aircraft are visible in the data is a separate question
this script has no opinion on.

Tags are a list, not a single value, because a SAR footprint is several
kilometres across and routinely spans more than one thing. A scene over
Rotterdam is a port *and* urban *and* partly water, and collapsing that to one
label would throw away the most interesting part.

Two sources, in two passes:

  Natural Earth   public domain, no attribution burden, downloaded once and
                  cached. Gives airport, port, urban, mountain, desert,
                  plateau, plain, wetland, ice, water and offshore.

  OpenStreetMap   via Overpass, optional and off by default. Adds the land use
                  Natural Earth has no layer for at all: agriculture, forest,
                  industrial and military. ODbL, so enabling it makes the
                  catalog a derived database under share-alike terms. It is
                  also a network dependency on a rate-limited public API, so it
                  fails soft: an unreachable Overpass leaves the Natural Earth
                  tags untouched rather than failing the run.

Usage:
    python3 scripts/classify_scenes.py                 # Natural Earth only
    python3 scripts/classify_scenes.py --osm           # also enrich from OSM
    python3 scripts/classify_scenes.py --dry-run       # report, write nothing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
CATALOG = ROOT / "data" / "scenes.geojson"
CACHE = ROOT / "data" / ".ne-cache"

NE_BASE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
           "/master/geojson")

#: The published order, which is also the order the legend reads in. Human
#: infrastructure first, because that is what people search for, then terrain,
#: then water.
TAGS = ["airport", "port", "industrial", "military", "urban", "agriculture",
        "forest", "mountain", "plateau", "plain", "desert", "wetland", "ice",
        "water", "offshore"]

#: Natural Earth's region polygons are *named* features — "the Alps", "the
#: Sahara" — so some of its classes describe where you are rather than what is
#: there. Being inside the Continent polygon for Africa says nothing, and
#: Island/Coast/Peninsula are shape, not land use. Only the classes that answer
#: "what is this ground" are mapped; the rest are deliberately dropped.
TERRAIN = {
    "Range/mtn": "mountain", "Foothills": "mountain", "Gorge": "mountain",
    "Plateau": "plateau",
    "Plain": "plain", "Lowland": "plain", "Basin": "plain",
    "Valley": "plain", "Depression": "plain",
    "Desert": "desert",
    "Wetlands": "wetland", "Delta": "wetland",
    "Tundra": "wetland",
}

#: Point layers have no extent, so a scene counts as covering one when the
#: point falls within this many degrees of the footprint. Roughly 5 km, which
#: is about the radius within which an airport's apron, runways and approach
#: still read as "this scene is of the airport".
POINT_REACH = 0.045

#: Fractions of a footprint's area that must fall inside a polygon layer. A
#: footprint clipping the edge of a lake is not a water scene; one that is
#: mostly sea is.
WATER_FRACTION = 0.30
OCEAN_FRACTION = 0.60

LAYERS = {
    "airports":  "ne_10m_airports",
    "ports":     "ne_10m_ports",
    "urban":     "ne_10m_urban_areas",
    "regions":   "ne_10m_geography_regions_polys",
    "ice":       "ne_10m_glaciated_areas",
    "lakes":     "ne_10m_lakes",
    "ocean":     "ne_10m_ocean",
}


# --------------------------------------------------------------------------- #
# Natural Earth
# --------------------------------------------------------------------------- #
def fetch_layers(cache: Path, timeout: int = 180) -> dict:
    """Download the Natural Earth layers, reusing anything already cached."""
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, name in LAYERS.items():
        path = cache / f"{name}.geojson"
        if not path.exists() or path.stat().st_size == 0:
            url = f"{NE_BASE}/{name}.geojson"
            req = urllib.request.Request(
                url, headers={"User-Agent": "open-sar-triad-classifier"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                path.write_bytes(resp.read())
            print(f"  downloaded {name} ({path.stat().st_size / 1e6:.1f} MB)")
        out[key] = json.loads(path.read_text())["features"]
    return out


def _index(features, keyfn=None):
    """(STRtree, geoms, keys) for a layer, skipping anything unusable."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    geoms, keys = [], []
    for f in features:
        if not f.get("geometry"):
            continue
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        if g.is_empty:
            continue
        geoms.append(g)
        keys.append(keyfn(f.get("properties") or {}) if keyfn else None)
    return STRtree(geoms), geoms, keys


def classify_natural_earth(feats: list[dict], layers: dict) -> None:
    """Set ``properties['landuse']`` on every feature, in place."""
    from shapely.geometry import shape

    air_t, air_g, _ = _index(layers["airports"])
    por_t, por_g, _ = _index(layers["ports"])
    urb_t, urb_g, _ = _index(layers["urban"])
    reg_t, reg_g, reg_k = _index(layers["regions"],
                                 lambda p: TERRAIN.get(p.get("FEATURECLA")))
    ice_t, ice_g, _ = _index(layers["ice"])
    lak_t, lak_g, _ = _index(layers["lakes"])
    oce_t, oce_g, _ = _index(layers["ocean"])

    def any_hit(tree, geoms, g):
        return any(geoms[i].intersects(g) for i in tree.query(g))

    def area_fraction(tree, geoms, g, area):
        if not area:
            return 0.0
        covered = 0.0
        for i in tree.query(g):
            try:
                covered += geoms[i].intersection(g).area
            except Exception:         # a self-intersecting source polygon
                continue
        return covered / area

    for f in feats:
        # Already carried forward from the previous catalog.
        if "landuse" in (f.get("properties") or {}):
            continue
        try:
            g = shape(f["geometry"])
        except Exception:
            f.setdefault("properties", {})["landuse"] = []
            continue

        tags = set()
        # Point layers: reach out from the footprint rather than requiring the
        # point to land inside it, since an airport's extent is not a point.
        near = g.buffer(POINT_REACH)
        if any_hit(air_t, air_g, near):
            tags.add("airport")
        if any_hit(por_t, por_g, near):
            tags.add("port")

        if any_hit(urb_t, urb_g, g):
            tags.add("urban")
        if any_hit(ice_t, ice_g, g):
            tags.add("ice")

        for i in reg_t.query(g):
            tag = reg_k[i]
            if tag and reg_g[i].intersects(g):
                tags.add(tag)

        area = g.area
        if area_fraction(lak_t, lak_g, g, area) >= WATER_FRACTION:
            tags.add("water")
        if area_fraction(oce_t, oce_g, g, area) >= OCEAN_FRACTION:
            tags.add("offshore")

        f.setdefault("properties", {})["landuse"] = [t for t in TAGS if t in tags]


# --------------------------------------------------------------------------- #
# OpenStreetMap enrichment (optional, ODbL)
# --------------------------------------------------------------------------- #
OVERPASS = "https://overpass-api.de/api/interpreter"

#: Each tag is one Overpass filter. Kept narrow on purpose: broad queries over
#: a 10 km box time out, and a timeout that silently returns nothing would look
#: exactly like "there is no farmland here".
OSM_RULES = {
    "agriculture": ['way["landuse"~"^(farmland|orchard|vineyard|allotments)$"]'],
    "forest":      ['way["landuse"="forest"]', 'way["natural"="wood"]'],
    "industrial":  ['way["landuse"="industrial"]'],
    "military":    ['way["landuse"="military"]'],
}


def _cell(lon: float, lat: float, size: float) -> tuple:
    return (round(lon / size), round(lat / size))


def osm_enrich(feats: list[dict], cell_deg: float = 0.045,
               pause: float = 1.2, timeout: int = 90,
               max_cells: int | None = None, log=print) -> dict:
    """Add OSM land-use tags, one query per distinct location.

    Scenes are grouped onto a grid first because repeat tasking means 14,920
    scenes sit on roughly 3,300 places; querying per scene would be four times
    the traffic for the same answer.

    Never raises. Overpass is a shared, rate-limited public service, and a
    classification pass is not worth failing an ingest over, so the return
    value reports what happened and the caller decides whether to care.
    """
    from shapely.geometry import shape

    cells: dict = {}
    for f in feats:
        try:
            c = shape(f["geometry"]).centroid
        except Exception:
            continue
        cells.setdefault(_cell(c.x, c.y, cell_deg), []).append(f)

    todo = list(cells.items())
    if max_cells:
        todo = todo[:max_cells]
    stats = {"cells": len(todo), "queried": 0, "failed": 0, "tagged": 0}
    log(f"  {len(feats):,} scenes in {len(cells):,} locations; querying {len(todo):,}")

    for n, (key, members) in enumerate(todo, 1):
        lon = key[0] * cell_deg
        lat = key[1] * cell_deg
        half = cell_deg / 2
        bbox = f"{lat - half},{lon - half},{lat + half},{lon + half}"
        body = "[out:json][timeout:60];(" + "".join(
            f"{sel}({bbox});" for sels in OSM_RULES.values() for sel in sels
        ) + ");out tags 40;"
        try:
            req = urllib.request.Request(
                OVERPASS, data=urllib.parse.urlencode({"data": body}).encode(),
                headers={"User-Agent": "open-sar-triad-classifier"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                doc = json.loads(resp.read())
            stats["queried"] += 1
        except (urllib.error.HTTPError, urllib.error.URLError,
                json.JSONDecodeError, OSError, TimeoutError) as e:
            stats["failed"] += 1
            if stats["failed"] <= 3:
                log(f"  overpass failed at {bbox}: {e}")
            # Give up on OSM entirely once it is clearly not answering, rather
            # than spending an hour collecting the same error.
            if stats["failed"] >= 10 and stats["queried"] == 0:
                log("  overpass is not responding; keeping Natural Earth tags only")
                break
            continue

        found = set()
        for el in doc.get("elements", []):
            t = el.get("tags") or {}
            lu, nat = t.get("landuse"), t.get("natural")
            if lu in ("farmland", "orchard", "vineyard", "allotments"):
                found.add("agriculture")
            elif lu == "forest" or nat == "wood":
                found.add("forest")
            elif lu == "industrial":
                found.add("industrial")
            elif lu == "military":
                found.add("military")

        if found:
            for f in members:
                p = f.setdefault("properties", {})
                merged = set(p.get("landuse") or []) | found
                p["landuse"] = [t for t in TAGS if t in merged]
            stats["tagged"] += len(members)
        if n % 250 == 0:
            log(f"  {n:,}/{len(todo):,} locations")
        time.sleep(pause)

    return stats


def carry_forward(feats: list[dict], catalog: Path, log=print) -> int:
    """Reuse tags already committed for scenes we have seen before.

    The fetch rewrites the whole catalog every week and drops these properties,
    so without this the classifier would redo all 14,920 scenes — nine minutes
    of geometry — to re-derive an answer that cannot have changed. Only genuinely
    new scenes need work, which makes a weekly run seconds rather than minutes.

    Falls back to classifying everything if the previous catalog cannot be read,
    since a slow run is better than a wrong one.
    """
    try:
        rel = catalog.resolve().relative_to(ROOT.resolve())
        raw = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel.as_posix()}"],
                             capture_output=True, check=True).stdout
        previous = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError,
            FileNotFoundError, OSError):
        log("  no previous catalog to carry tags from; classifying everything")
        return 0

    known = {}
    for f in previous.get("features") or []:
        p = f.get("properties") or {}
        if p.get("id") and "landuse" in p:
            known[p["id"]] = p["landuse"]
    if not known:
        return 0

    n = 0
    for f in feats:
        p = f.setdefault("properties", {})
        if "landuse" not in p and p.get("id") in known:
            p["landuse"] = known[p["id"]]
            n += 1
    log(f"  carried tags forward for {n:,} previously classified scenes")
    return n


# --------------------------------------------------------------------------- #
def summarise(feats: list[dict]) -> Counter:
    c = Counter()
    for f in feats:
        tags = (f.get("properties") or {}).get("landuse") or []
        if not tags:
            c["(untagged)"] += 1
        for t in tags:
            c[t] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--catalog", type=Path, default=CATALOG)
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--osm", action="store_true",
                    help="also query OpenStreetMap (ODbL) for land use")
    ap.add_argument("--osm-max-cells", type=int, default=None,
                    help="stop after this many OSM locations; for a smoke test")
    ap.add_argument("--all", action="store_true",
                    help="reclassify every scene instead of only new ones")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        import shapely  # noqa: F401
    except ImportError:
        print("ERROR: shapely is required (pip install shapely)", file=sys.stderr)
        return 1

    if not args.catalog.exists():
        print(f"ERROR: {args.catalog} not found", file=sys.stderr)
        return 1

    doc = json.loads(args.catalog.read_text())
    feats = doc.get("features") or []
    print(f"Classifying {len(feats):,} scenes")

    if not args.all:
        carry_forward(feats, args.catalog)
    todo = sum(1 for f in feats if "landuse" not in (f.get("properties") or {}))
    print(f"  {todo:,} scene(s) need classifying")

    if todo:
        layers = fetch_layers(args.cache)
        classify_natural_earth(feats, layers)
    ne_counts = summarise(feats)

    if args.osm:
        print("OSM enrichment (ODbL):")
        stats = osm_enrich(feats, max_cells=args.osm_max_cells)
        print(f"  queried {stats['queried']:,}, failed {stats['failed']:,}, "
              f"scenes enriched {stats['tagged']:,}")

    counts = summarise(feats)
    print("\nTags:")
    for tag in TAGS + ["(untagged)"]:
        if counts.get(tag):
            print(f"  {tag:12} {counts[tag]:6,}  ({counts[tag]/len(feats):5.1%})")
    if args.osm:
        gained = sum(counts[t] - ne_counts.get(t, 0)
                     for t in ("agriculture", "forest", "industrial", "military"))
        print(f"  OSM added {gained:,} tags Natural Earth could not")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    doc["landuse_tags"] = TAGS
    args.catalog.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"\nWrote {args.catalog} ({args.catalog.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
