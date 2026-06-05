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

OUT_PATH = Path(__file__).parent.parent / "data" / "scenes.geojson"

BASE_URL = "https://raw.githubusercontent.com/Jack-Hayes/commerical-sar-stac/refs/heads/main/parquets/viz"

PROVIDER_META = {
    "iceye": {
        "label":        "ICEYE",
        "color":        "#00FF87",
        "provider_url": "https://www.iceye.com/open-data-initiative",
        "parquets":     [f"{BASE_URL}/iceye/iceye.parquet"],
    },
    "umbra": {
        "label":        "Umbra",
        "color":        "#00C9FF",
        "provider_url": "https://umbra.space/open-data/",
        "parquets":     [f"{BASE_URL}/umbra/umbra.parquet"],
    },
    "capella": {
        "label":        "Capella",
        "color":        "#FF6B35",
        "provider_url": "https://www.capellaspace.com/community/capella-open-data-program/",
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


def parse_assets(assets_val):
    """Return (thumbnail_url, download_url) from assets field."""
    if assets_val is None:
        return None, None
    if isinstance(assets_val, str):
        try:
            assets = json.loads(assets_val)
        except Exception:
            return None, None
    else:
        assets = dict(assets_val)

    thumbnail = None
    for key in ("thumbnail", "overview", "browse", "quicklook", "preview"):
        if key in assets:
            v = assets[key]
            thumbnail = v.get("href") if isinstance(v, dict) else None
            if thumbnail:
                break

    download = None
    for key in ("data", "cog", "GRD", "SLC", "HH", "VV", "GEC", "SICD", "amplitude"):
        if key in assets:
            v = assets[key]
            download = v.get("href") if isinstance(v, dict) else None
            if download:
                break
    if not download and assets:
        first = next(iter(assets.values()))
        download = first.get("href") if isinstance(first, dict) else None

    return thumbnail, download


def normalize_row(row, provider_id):
    """Convert a parquet row to a GeoJSON Feature."""
    info = PROVIDER_META[provider_id]

    # Geometry — prefer pre-serialised geojson string
    geom_str = row.get("geometry_geojson")
    if geom_str:
        try:
            geometry = json.loads(geom_str)
        except Exception:
            return None
    else:
        return None  # skip rows without geometry

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

    thumbnail, download = parse_assets(row.get("assets"))

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
            "provider_url":    info["provider_url"],
            "collection":      sanitize_str(row.get("collection") or row.get("sar:product_type")) or "",
            "orbit_state":     orbit,
            "look_dir":        look,
        },
    }


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
    return features


def main():
    # Load existing data as fallback
    existing = {}
    if OUT_PATH.exists():
        try:
            old = json.loads(OUT_PATH.read_text())
            for f in old.get("features", []):
                p = f["properties"]["provider"]
                existing.setdefault(p, []).append(f)
            print(f"Loaded {sum(len(v) for v in existing.values())} existing scenes as fallback.")
        except Exception:
            pass

    all_features = {}
    for pid in ("iceye", "umbra", "capella"):
        try:
            all_features[pid] = fetch_provider(pid)
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

    geojson = {
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://github.com/Jack-Hayes/commerical-sar-stac",
        "features": merged,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geojson, separators=(",", ":")))
    print(f"\nWrote {len(merged)} total scenes → {OUT_PATH}")


if __name__ == "__main__":
    main()
