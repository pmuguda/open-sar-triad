#!/usr/bin/env python3
"""
Fetches open SAR scene metadata from pre-built GeoParquet files maintained by
Jack-Hayes/commerical-sar-stac (updated weekly). Outputs a merged GeoJSON to
data/scenes.geojson.

Credit: https://github.com/Jack-Hayes/commerical-sar-stac
"""

import io
import json
import re
import sys
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")  # suppress numpy version warnings in some envs

try:
    import pyarrow.parquet as pq
except ImportError:
    print("ERROR: pyarrow is required. Install with: pip install pyarrow", file=sys.stderr)
    sys.exit(1)

try:
    from shapely import wkb as shapely_wkb
    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False

OUT_PATH = Path(__file__).parent.parent / "data" / "scenes.geojson"

BASE_URL = "https://raw.githubusercontent.com/Jack-Hayes/commerical-sar-stac/refs/heads/main/parquets"

# Ordered list of (display_label, asset_key) for each provider's downloadable formats.
ICEYE_FORMATS = [
    ("GRD", "grd-cog"),
    ("CSI", "csi-cog"),
    ("SLC", "slc-cog"),
    ("SICD", "sicd"),
    ("SIDD", "sidd"),
    ("CPHD", "cphd"),
    ("VID", "vid-cog"),
]
UMBRA_FORMATS = [
    ("GEC", "gec"),
    ("CSI", "csi"),
    ("SICD", "sicd"),
    ("SIDD", "sidd"),
    ("CPHD", "cphd"),
]

PROVIDER_META = {
    "iceye": {
        "label":        "ICEYE",
        "color":        "#00FF87",
        "provider_url": "https://radiantearth.github.io/stac-browser/#/external/iceye-open-data-catalog.s3.amazonaws.com/collections/iceye-sar.json",
        "parquets":     [f"{BASE_URL}/iceye/iceye.parquet"],
    },
    "umbra": {
        "label":        "Umbra",
        "color":        "#00C9FF",
        "provider_url": "https://radiantearth.github.io/stac-browser/#/external/s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json",
        "parquets":     [f"{BASE_URL}/umbra/umbra.parquet"],
    },
    "capella": {
        "label":        "Capella",
        "color":        "#FF6B35",
        "provider_url": "https://radiantearth.github.io/stac-browser/#/external/capella-open-data.s3.us-west-2.amazonaws.com/stac/catalog.json",
        "parquets": [
            f"{BASE_URL}/capella/capella_GEC.parquet",
            f"{BASE_URL}/capella/capella_GEO.parquet",
            f"{BASE_URL}/capella/capella_SLC.parquet",
            f"{BASE_URL}/capella/capella_SICD.parquet",
            f"{BASE_URL}/capella/capella_SIDD.parquet",
            f"{BASE_URL}/capella/capella_CSI.parquet",
            f"{BASE_URL}/capella/capella_CPHD.parquet",
        ],
    },
}


def fetch_bytes(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "open-sar-triad/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def read_parquet(url):
    """Download a parquet file and return a pandas DataFrame."""
    try:
        data = fetch_bytes(url)
        tbl  = pq.read_table(io.BytesIO(data))
        return tbl.to_pandas()
    except Exception as e:
        print(f"  [WARN] Could not read {url.split('/')[-1]}: {e}", file=sys.stderr)
        return None


def parse_assets(assets_val, provider_id=None):
    """Return (thumbnail_url, download_url, products_dict) from assets field.

    products_dict is a {FormatLabel: url} map for providers with multiple
    downloadable formats (ICEYE, Umbra). None for Capella (handled separately).
    """
    if assets_val is None:
        return None, None, None
    if isinstance(assets_val, str):
        try:
            assets = json.loads(assets_val)
        except Exception:
            return None, None, None
    else:
        assets = dict(assets_val)

    # Normalize to lowercase keys for case-insensitive lookup (new schema uses lowercase)
    lc = {k.lower(): v for k, v in assets.items()}

    def _href(v):
        """Extract href string from an asset value, skipping empty strings."""
        h = v.get("href") if isinstance(v, dict) else None
        return h.strip() if h and h.strip() else None

    thumbnail = None
    for key in ("thumbnail", "qlk-cog", "overview", "browse", "quicklook", "preview"):
        if key in lc:
            thumbnail = _href(lc[key])
            if thumbnail:
                break

    # Build per-format products dict for ICEYE and Umbra
    products = None
    if provider_id == "iceye":
        p = {}
        for label, key in ICEYE_FORMATS:
            if key in lc:
                url = _href(lc[key])
                if url:
                    p[label] = url
        if p:
            products = p
    elif provider_id == "umbra":
        p = {}
        for label, key in UMBRA_FORMATS:
            if key in lc:
                url = _href(lc[key])
                if url:
                    p[label] = url
        if p:
            products = p

    # Primary download: first available format, or fall back to generic keys
    download = None
    if products:
        download = next(iter(products.values()))
    else:
        for key in ("gec", "grd-cog", "csi-cog", "slc-cog", "data", "cog",
                    "grd", "slc", "hh", "vv", "sicd", "amplitude", "csi"):
            if key in lc:
                download = _href(lc[key])
                if download:
                    break

    return thumbnail, download, products


def normalize_row(row, provider_id):
    """Convert a parquet row to a GeoJSON Feature."""
    info = PROVIDER_META[provider_id]

    # Geometry — new schema stores raw WKB bytes; old schema had geometry_geojson string
    geom_raw = row.get("geometry")
    geom_str = row.get("geometry_geojson")
    geometry = None
    if geom_raw is not None and _HAS_SHAPELY:
        try:
            geom = shapely_wkb.loads(bytes(geom_raw))
            geometry = geom.__geo_interface__
        except Exception:
            pass
    if geometry is None and geom_str:
        try:
            geometry = json.loads(geom_str)
        except Exception:
            pass
    if geometry is None:
        return None

    # Date
    dt_val = row.get("datetime") or row.get("start_datetime")
    date_str = year = None
    if dt_val is not None:
        try:
            if hasattr(dt_val, "isoformat"):
                date_str = dt_val.strftime("%Y-%m-%d")
                year = dt_val.year
            else:
                s = str(dt_val)
                date_str = s[:10]
                year = int(s[:4])
        except Exception:
            pass
    # Reject clearly erroneous future dates (> 1 year ahead)
    now = datetime.now(timezone.utc)
    cutoff_future = now.replace(year=now.year + 1).strftime("%Y-%m-%d")
    if date_str and date_str > cutoff_future:
        return None

    # Sensor metadata
    sensor_mode  = row.get("sar:instrument_mode") or row.get("instrument_mode") or "N/A"
    resolution   = row.get("sar:resolution_range") or row.get("sar:pixel_spacing_range") or row.get("gsd")
    polarization = row.get("sar:polarizations")
    if isinstance(polarization, str):
        try:
            polarization = ", ".join(json.loads(polarization))
        except Exception:
            pass
    elif isinstance(polarization, list):
        polarization = ", ".join(polarization)

    if resolution is not None:
        try:
            import math
            f = float(resolution)
            resolution = round(f, 2) if math.isfinite(f) else None
        except Exception:
            resolution = None

    thumbnail, download, products = parse_assets(row.get("assets"), provider_id)

    # Incidence angle / off-nadir (ICEYE uses these instead of resolution)
    import math
    def safe_float(v, decimals=1):
        if v is None: return None
        try:
            f = float(v)
            return round(f, decimals) if math.isfinite(f) else None
        except Exception:
            return None
    incidence_angle = safe_float(row.get("view:incidence_angle"))
    off_nadir       = safe_float(row.get("view:off_nadir"))

    # Skip collection footprint entries (not real acquisitions)
    item_id = str(row.get("id", ""))
    if "footprint" in item_id.lower() or "collection" in item_id.lower():
        return None

    def clean_str(v):
        if v is None: return None
        s = str(v).strip().lower()
        return None if s in ('', 'nan', 'none') else s

    def sanitize_str(v):
        """Strip HTML tags and normalize; returns None for empty/nan values."""
        if v is None: return None
        s = re.sub(r'<[^>]*>', '', str(v)).strip()
        return None if s.lower() in ('', 'nan', 'none') else s

    def safe_url(v):
        """Accept only http(s) URLs; reject anything else (javascript:, data:, etc.)."""
        if v is None: return None
        s = str(v).strip()
        return s if s.startswith(('https://', 'http://')) else None

    orbit = clean_str(row.get("sat:orbit_state"))
    look  = clean_str(row.get("sar:observation_direction"))

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "id":              sanitize_str(row.get("id")) or "",
            "provider":        provider_id,
            "provider_label":  info["label"],
            "color":           info["color"],
            "date":            date_str,
            "year":            year,
            "sensor_mode":     sanitize_str(sensor_mode) or "n/a",
            "resolution":      resolution,
            "polarization":    sanitize_str(polarization),
            "incidence_angle": incidence_angle,
            "off_nadir":       off_nadir,
            "thumbnail":       safe_url(thumbnail),
            "download":        safe_url(download),
            "products":        {k: safe_url(v) for k, v in products.items()} if products else None,
            "provider_url":    info["provider_url"],
            "collection":      sanitize_str(row.get("collection") or row.get("sar:product_type")) or "",
            "orbit_state":     orbit,
            "look_dir":        look,
            # Date this scene first appeared in our catalog. Filled in by main()
            # by diffing against the previous run; None for pre-tracking scenes.
            "first_seen":      None,
        },
    }


class SchemaError(RuntimeError):
    """Raised when upstream rows were fetched but none parsed — a schema change."""


def fetch_provider(provider_id):
    info = PROVIDER_META[provider_id]
    all_rows = []

    for url in info["parquets"]:
        fname = url.split("/")[-1]
        print(f"  [{info['label']}] Fetching {fname}…")
        df = read_parquet(url)
        if df is not None:
            print(f"    → {len(df)} rows")
            all_rows.append(df)

    if not all_rows:
        return []

    import pandas as pd
    merged = pd.concat(all_rows, ignore_index=True)

    # Deduplicate by id
    if "id" in merged.columns:
        merged = merged.drop_duplicates(subset=["id"])

    features = []
    for _, row in merged.iterrows():
        feat = normalize_row(row, provider_id)
        if feat:
            features.append(feat)

    print(f"  [{info['label']}] ✓ {len(features)} scenes")

    # Fail loudly on a schema change: if the upstream parquet had rows but none
    # of them parsed into scenes, the fallback would silently freeze the catalog
    # (this happened once when the geometry column moved to WKB and shapely was
    # missing). Raise so the run goes red instead of quietly serving stale data.
    if len(merged) and not features:
        raise SchemaError(
            f"{info['label']}: fetched {len(merged)} rows but parsed 0 scenes — "
            "upstream parquet schema may have changed"
        )
    return features


# Capella publishes each acquisition in several data formats (GEC, GEO, SLC,
# SICD, SIDD, CPHD, CSI) as separate upstream parquets, so the same scene shows
# up 6–7 times. Collapse those into one feature per acquisition that carries a
# {format: download_url} map, so users pick the format in the detail panel
# instead of seeing duplicate footprints on the map.
_CAPELLA_TOKEN = re.compile(r"_(GEC|GEO|SLC|SICD|SIDD|CPHD|CSI)_")
_FORMAT_ORDER = ["GEC", "GEO", "SLC", "SICD", "SIDD", "CPHD", "CSI"]

def collapse_capella_products(features):
    groups = {}   # acquisition key -> list of (format, feature)
    passthrough = []
    for f in features:
        p = f["properties"]
        m = _CAPELLA_TOKEN.search(p.get("id") or "")
        if p.get("provider") != "capella" or not m:
            passthrough.append(f)
            continue
        key = _CAPELLA_TOKEN.sub("_<P>_", p["id"])
        groups.setdefault(key, []).append((m.group(1), f))

    collapsed = []
    for items in groups.values():
        items.sort(key=lambda t: _FORMAT_ORDER.index(t[0]) if t[0] in _FORMAT_ORDER else 99)
        base = items[0][1]                       # preferred format's feature
        bp = base["properties"]
        products = {}
        for fmt, f in items:
            url = f["properties"].get("download")
            if url and fmt not in products:
                products[fmt] = url
        bp["products"] = products                 # {format: download_url}
        bp["formats"] = [fmt for fmt, _ in items] # ordered available formats
        bp["collection"] = ", ".join(bp["formats"])
        bp["id"] = _CAPELLA_TOKEN.sub("_", bp["id"])  # clean, format-agnostic id
        collapsed.append(base)

    if collapsed:
        print(f"  Capella: collapsed {sum(len(v) for v in groups.values())} "
              f"format-variants into {len(collapsed)} acquisitions")
    return passthrough + collapsed


def main():
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Load existing data — used both as a fetch fallback and to preserve the
    # first_seen date of scenes we've already catalogued.
    existing = {}
    prev_ids = set()            # every id present in the previous run
    prev_first_seen = {}        # id -> first_seen date, when already known
    if OUT_PATH.exists():
        try:
            old = json.loads(OUT_PATH.read_text())
            for f in old.get("features", []):
                props = f["properties"]
                p = props["provider"]
                existing.setdefault(p, []).append(f)
                fid = props.get("id")
                if fid:
                    prev_ids.add(fid)
                    if props.get("first_seen"):
                        prev_first_seen[fid] = props["first_seen"]
            print(f"Loaded {sum(len(v) for v in existing.values())} existing scenes as fallback.")
        except Exception:
            pass

    all_features = {}
    schema_failures = []
    for pid in ("iceye", "umbra", "capella"):
        try:
            all_features[pid] = fetch_provider(pid)
        except SchemaError as e:
            # Not transient: surface it so the run fails after writing fallback.
            print(f"  [{pid}] SCHEMA ERROR: {e}", file=sys.stderr)
            schema_failures.append(pid)
            all_features[pid] = []
        except Exception as e:
            print(f"  [{pid}] ERROR: {e}", file=sys.stderr)
            all_features[pid] = []

        if not all_features[pid] and existing.get(pid):
            print(f"  [{pid}] Falling back to cached data ({len(existing[pid])} scenes).")
            all_features[pid] = existing[pid]

    merged = []
    for pid, feats in all_features.items():
        merged.extend(feats)
        print(f"  {PROVIDER_META[pid]['label']}: {len(feats)} scenes")

    # Merge Capella's per-format duplicates into one scene per acquisition.
    merged = collapse_capella_products(merged)

    # Stamp first_seen: preserve for known scenes, mark this run's date for new
    # ones. Scenes present before tracking began stay None (unknown ingest date).
    new_count = 0
    for feat in merged:
        props = feat["properties"]
        if props.get("first_seen"):
            continue  # already stamped (e.g. fallback-cached feature)
        fid = props.get("id")
        prev = prev_first_seen.get(fid)
        if prev:
            props["first_seen"] = prev
        elif fid in prev_ids:
            props["first_seen"] = None  # existed before tracking; leave unknown
        else:
            props["first_seen"] = run_date
            new_count += 1
    print(f"\n  New scenes this run: {new_count}")

    geojson = {
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": run_date,
        "new_this_run": new_count,
        "source": "https://github.com/Jack-Hayes/commerical-sar-stac",
        "features": merged,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geojson, separators=(",", ":")))
    print(f"\nWrote {len(merged)} total scenes → {OUT_PATH}")

    # Cached fallback was written so the site keeps working, but a parse failure
    # is a real breakage — exit non-zero so the scheduled run goes red.
    if schema_failures:
        print(
            f"\nERROR: parsed 0 scenes for: {', '.join(schema_failures)}. "
            "Wrote cached fallback and failing the run so this is not ignored.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
