#!/usr/bin/env python3
"""
Fetches open SAR scene footprints from ICEYE, Umbra, and Capella STAC catalogs.
Outputs a merged GeoJSON to data/scenes.geojson.
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

OUT_PATH = Path(__file__).parent.parent / "data" / "scenes.geojson"
MAX_SCENES_PER_PROVIDER = 2000
WORKERS = 20

PROVIDERS = {
    "umbra": {
        "label": "Umbra",
        "color": "#00C9FF",
        "catalog_url": "https://s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json",
        "provider_url": "https://umbra.space/open-data/",
    },
    "capella": {
        "label": "Capella",
        "color": "#FF6B35",
        "catalog_url": "https://capella-open-data.s3.us-west-2.amazonaws.com/stac/catalog.json",
        "provider_url": "https://www.capellaspace.com/community/capella-open-data-program/",
    },
    "iceye": {
        "label": "ICEYE",
        "color": "#00FF87",
        "catalog_url": "https://iceye-open-data-catalog.s3-us-west-2.amazonaws.com/catalog.json",
        "provider_url": "https://www.iceye.com/open-data-initiative",
    },
}


def fetch_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "open-sar-triad/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  [WARN] {url[:80]}: {e}", file=sys.stderr)
        return None


def resolve_url(base_url, href):
    if href.startswith("http"):
        return href
    if href.startswith("./"):
        href = href[2:]
    base = base_url.rsplit("/", 1)[0]
    while href.startswith("../"):
        href = href[3:]
        base = base.rsplit("/", 1)[0]
    return f"{base}/{href}"


def collect_item_urls(catalog_url, depth=0, max_depth=5):
    """Recursively collect all item URLs from a static STAC catalog tree."""
    if depth > max_depth:
        return [], []

    catalog = fetch_json(catalog_url)
    if not catalog:
        return [], []

    item_urls = []
    child_urls = []

    for link in catalog.get("links", []):
        rel  = link.get("rel", "")
        href = link.get("href", "")
        if not href:
            continue
        abs_url = resolve_url(catalog_url, href)
        if rel == "item":
            item_urls.append(abs_url)
        elif rel in ("child", "collection", "items"):
            child_urls.append(abs_url)

    # Recurse into children
    for child_url in child_urls:
        sub_items, _ = collect_item_urls(child_url, depth + 1, max_depth)
        item_urls.extend(sub_items)
        if len(item_urls) >= MAX_SCENES_PER_PROVIDER * 3:
            break  # collect more than needed, we'll trim after

    return item_urls, []


def fetch_items_parallel(item_urls, provider_id, max_items):
    """Fetch up to max_items STAC item JSONs in parallel."""
    # Spread evenly across catalog (take every nth) to get good geographic spread
    if len(item_urls) > max_items * 2:
        step = len(item_urls) // max_items
        item_urls = item_urls[::step][:max_items]
    else:
        item_urls = item_urls[:max_items]

    features = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_json, url): url for url in item_urls}
        for fut in as_completed(futures):
            item = fut.result()
            if item:
                feat = normalize_item(item, provider_id)
                if feat:
                    features.append(feat)
    return features


def normalize_item(item, provider_id):
    if not item:
        return None
    geometry = item.get("geometry")
    if not geometry:
        return None

    props  = item.get("properties", {})
    assets = item.get("assets", {})
    info   = PROVIDERS[provider_id]

    # Thumbnail
    thumbnail = None
    for key in ("thumbnail", "overview", "browse", "quicklook"):
        if key in assets:
            thumbnail = assets[key].get("href")
            break

    # Download (prefer COG/GeoTIFF)
    download = None
    for key in ("data", "cog", "geotiff", "GRD", "SLC", "HH", "VV", "amplitude"):
        if key in assets:
            download = assets[key].get("href")
            break
    if not download and assets:
        download = next(iter(assets.values())).get("href")

    # Date
    dt = props.get("datetime") or props.get("start_datetime") or item.get("datetime")
    date_str = year = None
    if dt and dt not in ("null", None):
        try:
            parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            date_str = parsed.strftime("%Y-%m-%d")
            year     = parsed.year
        except Exception:
            date_str = dt[:10] if dt else None
            year = int(dt[:4]) if dt and len(dt) >= 4 else None

    sensor_mode = (
        props.get("sar:instrument_mode")
        or props.get("instrument_mode")
        or props.get("mode")
        or props.get("product_type")
        or "N/A"
    )
    resolution = (
        props.get("sar:pixel_spacing_range")
        or props.get("gsd")
        or props.get("resolution")
        or props.get("pixel_spacing")
    )
    polarization = props.get("sar:polarizations") or props.get("polarization")
    if isinstance(polarization, list):
        polarization = ", ".join(polarization)

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "id":             item.get("id", ""),
            "provider":       provider_id,
            "provider_label": info["label"],
            "color":          info["color"],
            "date":           date_str,
            "year":           year,
            "sensor_mode":    sensor_mode,
            "resolution":     resolution,
            "polarization":   polarization,
            "thumbnail":      thumbnail,
            "download":       download,
            "provider_url":   info["provider_url"],
            "collection":     item.get("collection"),
        },
    }


def fetch_provider(provider_id):
    info = PROVIDERS[provider_id]
    print(f"  [{info['label']}] Collecting item URLs…")
    item_urls, _ = collect_item_urls(info["catalog_url"])
    total_found = len(item_urls)
    print(f"  [{info['label']}] Found {total_found} item URLs — fetching up to {MAX_SCENES_PER_PROVIDER}…")
    features = fetch_items_parallel(item_urls, provider_id, MAX_SCENES_PER_PROVIDER)
    print(f"  [{info['label']}] ✓ {len(features)} scenes fetched")
    return features


def main():
    # Load existing data as fallback per provider
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
    for pid in ("umbra", "capella", "iceye"):
        try:
            all_features[pid] = fetch_provider(pid)
        except Exception as e:
            print(f"  [{pid}] ERROR: {e}", file=sys.stderr)
            all_features[pid] = []

    # Fall back to cached data if a provider returned nothing
    for pid in ("umbra", "capella", "iceye"):
        if not all_features[pid] and existing.get(pid):
            print(f"  [{pid}] Using cached data ({len(existing[pid])} scenes).")
            all_features[pid] = existing[pid]

    merged = []
    for pid, feats in all_features.items():
        merged.extend(feats)
        print(f"  {PROVIDERS[pid]['label']}: {len(feats)} scenes")

    geojson = {
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "features": merged,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(geojson, separators=(",", ":")))
    print(f"\nWrote {len(merged)} total scenes → {OUT_PATH}")


if __name__ == "__main__":
    main()
