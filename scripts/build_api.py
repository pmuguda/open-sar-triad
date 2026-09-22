#!/usr/bin/env python3
"""
Builds the static API under api/v1/ from data/scenes.geojson.

There is no server. Every "endpoint" is a file computed here and served by
GitHub Pages, which is enough because the catalog only changes once a week.
Run by .github/workflows/fetch-data.yml right after the catalog refresh.

Layout produced:

  api/v1/index.json                  lite search index (compact, ~0.5 MB gzipped)
  api/v1/stats.json                  counts by provider / mode / year
  api/v1/catalog.json                STAC root catalog
  api/v1/collections/{provider}.json STAC Collection per provider
  api/v1/items/{provider}.json       STAC ItemCollection per provider
  api/v1/scenes/{provider}.geojson   raw per-provider FeatureCollection

Why ItemCollections rather than one file per STAC Item: one file per item would
be 14,800 files that churn every Monday. Three ItemCollections stay clean in git
and pystac reads them just as happily.
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT     = Path(__file__).parent.parent
SRC      = ROOT / "data" / "scenes.geojson"
OUT      = ROOT / "api" / "v1"
BASE_URL = "https://www.pmuguda.com/open-sar-triad/api/v1"

# Mirrors FAMILIES in js/app.js. Each provider names the same kind of product
# differently, so a family resolves to the first format that provider publishes.
FAMILIES = {
    "detected": ["GEO", "GRD", "GEC", "SIDD"],
    "complex":  ["SLC", "SICD"],
    "phase":    ["CPHD"],
    "visual":   ["CSI", "VID"],
}
FORMAT_ORDER = ["GRD", "GEC", "GEO", "SLC", "CSI", "SICD", "SIDD", "CPHD", "VID"]

PROVIDERS = {
    "iceye":   {"title": "ICEYE",   "url": "https://www.iceye.com/open-data-initiative"},
    "umbra":   {"title": "Umbra",   "url": "https://umbra.space/open-data/"},
    "capella": {"title": "Capella", "url": "https://www.capellaspace.com/community/capella-open-data-program/"},
}

# Media type per format, so STAC assets are self-describing.
MEDIA = {
    "GRD": "image/tiff; application=geotiff", "GEC": "image/tiff; application=geotiff",
    "GEO": "image/tiff; application=geotiff", "SLC": "image/tiff; application=geotiff",
    "CSI": "image/tiff; application=geotiff", "VID": "image/tiff; application=geotiff",
    "SICD": "application/octet-stream", "SIDD": "application/octet-stream",
    "CPHD": "application/octet-stream",
}


def bbox_of(geom):
    if geom["type"] == "Polygon":
        pts = [p for ring in geom["coordinates"] for p in ring]
    else:
        pts = [p for poly in geom["coordinates"] for ring in poly for p in ring]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


def metadata_url(provider, asset_url):
    """Provider metadata sidecar next to a data asset. Same derivation as
    metadataUrl() in js/app.js, so the API and the web app never disagree."""
    if not asset_url:
        return None
    if provider == "iceye":
        if asset_url.endswith(".tif"):
            return asset_url[:-4] + ".json"
        if asset_url.endswith(".nitf"):
            return asset_url[:-5] + ".xml"
        return None
    if provider == "capella":
        return asset_url[:-4] + "_extended.json" if asset_url.endswith(".tif") else None
    if provider == "umbra":
        import re
        m = re.match(r"^(.*)/([^/]+?)_(?:CSI|GEC|SICD|SIDD|CPHD|SLC|GRD)(?:_[^.]*)?\.[^./]+$", asset_url, re.I)
        return f"{m.group(1)}/{m.group(2)}.stac.v2.json" if m else None
    return None


def products_of(props):
    """{FORMAT: url} for a scene, falling back to the primary asset."""
    p = props.get("products")
    if p:
        return {k: v for k, v in p.items() if isinstance(v, str) and v.startswith("http")}
    dl = props.get("download")
    return {"DATA": dl} if isinstance(dl, str) and dl.startswith("http") else {}


# CC-BY 4.0 requires the licence notice to travel WITH the material, and requires
# adaptations to be declared. Any of these files can be fetched on its own, so
# every one carries the full notice rather than relying on the root catalog.
ATTRIBUTION = {
    "license": "CC-BY-4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": (
        "Scene metadata (c) ICEYE, Umbra and Capella, published under their open data "
        "programs and licensed CC-BY 4.0. Catalog assembled by open-sar-triad "
        "(https://github.com/pmuguda/open-sar-triad) from normalized STAC records "
        "maintained by Jack-Hayes/commerical-sar-stac (MIT). When redistributing this "
        "data or publishing derived work, credit the originating provider."
    ),
    "modifications": (
        "Adapted from the providers' published STAC catalogs: sensor modes normalized to "
        "lowercase, Capella's per-format records collapsed to one record per acquisition "
        "carrying a format-to-URL map, bounding boxes recomputed, records restructured "
        "into STAC, and provider metadata sidecar URLs derived from asset URLs."
    ),
    "disclaimer": (
        "An independent third-party catalog. Not endorsed by, affiliated with, or "
        "operated by ICEYE, Umbra or Capella. Imagery is not redistributed here: every "
        "asset href points at the provider's own storage and remains subject to that "
        "provider's terms."
    ),
}


def write(path, obj):
    # Inject the notice at the top level of every file unless it already has one
    # (STAC Collections carry `license` natively).
    payload = dict(obj)
    for k, v in ATTRIBUTION.items():
        payload.setdefault(k, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")))
    return path.stat().st_size


def to_stac_item(feat, provider):
    """A STAC Item. The key part is `assets`: one entry per downloadable format,
    each with its metadata sidecar alongside, so a client never has to know the
    provider's file-naming rules."""
    props = feat["properties"]
    assets = {}
    for fmt, url in products_of(props).items():
        assets[fmt] = {
            "href": url,
            "type": MEDIA.get(fmt, "application/octet-stream"),
            "title": f"{fmt} product",
            "roles": ["data"],
        }
        side = metadata_url(provider, url)
        if side:
            assets[f"{fmt}_metadata"] = {
                "href": side,
                "type": "application/xml" if side.endswith(".xml") else "application/json",
                "title": f"{fmt} metadata sidecar",
                "roles": ["metadata"],
            }
    if props.get("thumbnail"):
        assets["thumbnail"] = {"href": props["thumbnail"], "type": "image/png", "roles": ["thumbnail"]}

    date = props.get("date")
    stac_props = {"datetime": f"{date}T00:00:00Z" if date else None}
    for src, dst in (("sensor_mode", "sar:instrument_mode"), ("polarization", "sar:polarizations"),
                     ("resolution", "sar:resolution_range"), ("orbit_state", "sat:orbit_state"),
                     ("look_dir", "sar:observation_direction"), ("incidence_angle", "view:incidence_angle"),
                     ("off_nadir", "view:off_nadir"), ("first_seen", "ost:first_seen")):
        if props.get(src) not in (None, "", "n/a"):
            stac_props[dst] = props[src]
    stac_props["ost:formats"] = sorted(products_of(props), key=lambda f: FORMAT_ORDER.index(f) if f in FORMAT_ORDER else 99)

    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "stac_extensions": [
            "https://stac-extensions.github.io/sar/v1.0.0/schema.json",
            "https://stac-extensions.github.io/sat/v1.0.0/schema.json",
        ],
        "id": props.get("id"),
        "collection": provider,
        "geometry": feat["geometry"],
        "bbox": bbox_of(feat["geometry"]),
        "properties": stac_props,
        "assets": assets,
        "links": [
            {"rel": "root", "href": f"{BASE_URL}/catalog.json", "type": "application/json"},
            {"rel": "collection", "href": f"{BASE_URL}/collections/{provider}.json", "type": "application/json"},
        ],
    }


def main():
    if not SRC.exists():
        print(f"ERROR: {SRC} not found; run scripts/fetch_catalog.py first.", file=sys.stderr)
        return 1
    data = json.loads(SRC.read_text())
    feats = data.get("features", [])
    if not feats:
        print("ERROR: catalog has no features.", file=sys.stderr)
        return 1

    generated = datetime.now(timezone.utc).isoformat()
    by_provider = defaultdict(list)
    for f in feats:
        by_provider[f["properties"]["provider"]].append(f)

    # ---- lite search index -------------------------------------------------
    # Rows are arrays, not objects, and `fields` names the positions. Repeating
    # every key 14,798 times would more than double the size; the client zips
    # them back into objects so callers still get named attributes.
    fields = ["id", "provider", "date", "mode", "orbit", "look", "formats", "bbox"]
    rows = []
    for f in feats:
        p = f["properties"]
        rows.append([
            p.get("id"), p.get("provider"), p.get("date"),
            (p.get("sensor_mode") or "").lower() or None,
            p.get("orbit_state"), p.get("look_dir"),
            sorted(products_of(p), key=lambda x: FORMAT_ORDER.index(x) if x in FORMAT_ORDER else 99),
            bbox_of(f["geometry"]),
        ])
    n_index = write(OUT / "index.json", {
        "api_version": "1", "generated": generated, "count": len(rows),
        "source": f"{BASE_URL}/catalog.json", "fields": fields, "scenes": rows,
    })

    # ---- stats -------------------------------------------------------------
    modes = Counter((f["properties"].get("sensor_mode") or "n/a").lower() for f in feats)
    years = Counter(f["properties"]["date"][:4] for f in feats if f["properties"].get("date"))
    fmts  = Counter(k for f in feats for k in products_of(f["properties"]))
    dates = sorted(f["properties"]["date"] for f in feats if f["properties"].get("date"))
    n_stats = write(OUT / "stats.json", {
        "api_version": "1", "generated": generated, "total": len(feats),
        "by_provider": {k: len(v) for k, v in sorted(by_provider.items(), key=lambda kv: -len(kv[1]))},
        "by_mode": dict(modes.most_common()),
        "by_year": dict(sorted(years.items())),
        "by_format": dict(fmts.most_common()),
        "families": FAMILIES,
        "temporal_extent": [dates[0], dates[-1]] if dates else [None, None],
    })

    # ---- STAC collections, items, and raw slices ---------------------------
    total_items = 0
    for pid, pfeats in by_provider.items():
        meta  = PROVIDERS.get(pid, {"title": pid, "url": ""})
        pdates = sorted(f["properties"]["date"] for f in pfeats if f["properties"].get("date"))
        bboxes = [bbox_of(f["geometry"]) for f in pfeats]
        extent = [min(b[0] for b in bboxes), min(b[1] for b in bboxes),
                  max(b[2] for b in bboxes), max(b[3] for b in bboxes)]

        write(OUT / "collections" / f"{pid}.json", {
            "type": "Collection", "stac_version": "1.0.0", "id": pid,
            "title": f"{meta['title']} open SAR scenes",
            "description": f"Open SAR acquisitions published by {meta['title']} under CC-BY 4.0. "
                           f"Catalog assembled by open-sar-triad.",
            "license": "CC-BY-4.0",
            "providers": [
                {"name": meta["title"], "roles": ["producer", "licensor"], "url": meta["url"]},
                {"name": "open-sar-triad", "roles": ["processor"], "url": "https://github.com/pmuguda/open-sar-triad"},
            ],
            "extent": {
                "spatial": {"bbox": [extent]},
                "temporal": {"interval": [[f"{pdates[0]}T00:00:00Z" if pdates else None,
                                           f"{pdates[-1]}T00:00:00Z" if pdates else None]]},
            },
            "links": [
                {"rel": "root", "href": f"{BASE_URL}/catalog.json", "type": "application/json"},
                {"rel": "items", "href": f"{BASE_URL}/items/{pid}.json", "type": "application/geo+json"},
            ],
        })

        items = [to_stac_item(f, pid) for f in pfeats]
        total_items += len(items)
        write(OUT / "items" / f"{pid}.json",
              {"type": "FeatureCollection", "stac_version": "1.0.0", "features": items})
        write(OUT / "scenes" / f"{pid}.geojson",
              {"type": "FeatureCollection", "generated": generated, "features": pfeats})

    # ---- STAC root ---------------------------------------------------------
    write(OUT / "catalog.json", {
        "type": "Catalog", "stac_version": "1.0.0", "id": "open-sar-triad",
        "title": "open-sar-triad: open SAR from ICEYE, Umbra and Capella",
        "description": "A static STAC catalog of open Synthetic Aperture Radar scenes "
                       "published by ICEYE, Umbra and Capella. Rebuilt weekly. "
                       "Assets link directly to each provider's own storage.",
        "links": [
            {"rel": "self", "href": f"{BASE_URL}/catalog.json", "type": "application/json"},
            {"rel": "root", "href": f"{BASE_URL}/catalog.json", "type": "application/json"},
            *[{"rel": "child", "href": f"{BASE_URL}/collections/{p}.json",
               "type": "application/json", "title": PROVIDERS.get(p, {}).get("title", p)}
              for p in by_provider],
            {"rel": "about", "href": "https://github.com/pmuguda/open-sar-triad", "type": "text/html"},
        ],
    })

    print(f"Wrote api/v1: {len(feats)} scenes, {total_items} STAC items, "
          f"{len(by_provider)} collections")
    print(f"  index.json {n_index/1e6:.1f} MB · stats.json {n_stats/1e3:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
