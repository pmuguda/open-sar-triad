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

OUT_PATH = Path(__file__).parent.parent / "data" / "scenes.geojson"
MAX_SCENES_PER_PROVIDER = 2000

PROVIDERS = {
    "umbra": {
        "label": "Umbra",
        "color": "#00C9FF",
        "catalog_url": "https://s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json",
    },
    "capella": {
        "label": "Capella",
        "color": "#FF6B35",
        "catalog_url": "https://capella-open-data.s3.us-west-2.amazonaws.com/stac/catalog.json",
    },
    "iceye": {
        "label": "ICEYE",
        "color": "#00FF87",
        "catalog_url": "https://iceye-open-data-catalog.s3-us-west-2.amazonaws.com/catalog.json",
    },
}


def fetch_json(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "open-sar-triad/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def resolve_url(base_url, href):
    """Resolve a relative or absolute STAC link href against a base URL."""
    if href.startswith("http"):
        return href
    base = base_url.rsplit("/", 1)[0]
    return f"{base}/{href}"


def walk_catalog(catalog_url, provider_id, features, depth=0, max_depth=4):
    """Recursively walk a STAC catalog, collecting item features."""
    if len(features) >= MAX_SCENES_PER_PROVIDER:
        return
    if depth > max_depth:
        return

    catalog = fetch_json(catalog_url)
    if not catalog:
        return

    links = catalog.get("links", [])

    for link in links:
        if len(features) >= MAX_SCENES_PER_PROVIDER:
            break
        rel = link.get("rel", "")
        href = link.get("href", "")
        if not href:
            continue

        abs_url = resolve_url(catalog_url, href)

        if rel == "item":
            item = fetch_json(abs_url)
            if item:
                feature = normalize_item(item, provider_id)
                if feature:
                    features.append(feature)
        elif rel in ("child", "collection", "items"):
            walk_catalog(abs_url, provider_id, features, depth + 1, max_depth)


def walk_items_endpoint(items_url, provider_id, features):
    """Walk a /items endpoint with pagination."""
    url = items_url
    while url and len(features) < MAX_SCENES_PER_PROVIDER:
        data = fetch_json(url)
        if not data:
            break

        items = []
        if data.get("type") == "FeatureCollection":
            items = data.get("features", [])
        elif isinstance(data.get("items"), list):
            items = data["items"]

        for item in items:
            if len(features) >= MAX_SCENES_PER_PROVIDER:
                break
            feature = normalize_item(item, provider_id)
            if feature:
                features.append(feature)

        # follow next link
        next_url = None
        for link in data.get("links", []):
            if link.get("rel") == "next":
                next_url = link.get("href")
                break
        url = next_url


def normalize_item(item, provider_id):
    """Convert a STAC item into a normalized GeoJSON feature."""
    if not item or item.get("type") not in ("Feature", None):
        if item and "geometry" not in item:
            return None

    geometry = item.get("geometry")
    if not geometry:
        return None

    props = item.get("properties", {})
    assets = item.get("assets", {})

    # Extract thumbnail
    thumbnail = None
    for key in ("thumbnail", "overview", "browse", "quicklook"):
        if key in assets:
            thumbnail = assets[key].get("href")
            break

    # Extract download link (prefer GeoTIFF COG)
    download = None
    for key in ("data", "cog", "geotiff", "GRD", "SLC", "HH", "VV"):
        if key in assets:
            download = assets[key].get("href")
            break
    if not download and assets:
        first = next(iter(assets.values()))
        download = first.get("href")

    # Normalize date
    dt = props.get("datetime") or props.get("start_datetime") or item.get("datetime")
    if dt and dt != "null":
        try:
            parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            date_str = parsed.strftime("%Y-%m-%d")
            year = parsed.year
        except Exception:
            date_str = dt[:10] if dt else None
            year = int(dt[:4]) if dt else None
    else:
        date_str = None
        year = None

    provider_info = PROVIDERS[provider_id]

    # Provider-specific metadata extraction
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
        or None
    )
    polarization = props.get("sar:polarizations") or props.get("polarization") or None
    if isinstance(polarization, list):
        polarization = ", ".join(polarization)

    # Build provider URL
    item_id = item.get("id", "")
    provider_url = None
    if provider_id == "umbra":
        provider_url = "https://umbra.space/open-data/"
    elif provider_id == "capella":
        provider_url = "https://www.capellaspace.com/community/capella-open-data-program/"
    elif provider_id == "iceye":
        provider_url = "https://www.iceye.com/open-data-initiative"

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "id": item_id,
            "provider": provider_id,
            "provider_label": provider_info["label"],
            "color": provider_info["color"],
            "date": date_str,
            "year": year,
            "sensor_mode": sensor_mode,
            "resolution": resolution,
            "polarization": polarization,
            "thumbnail": thumbnail,
            "download": download,
            "provider_url": provider_url,
            "collection": item.get("collection"),
        },
    }


def fetch_umbra(features):
    print("  Fetching Umbra catalog...")
    catalog = fetch_json(PROVIDERS["umbra"]["catalog_url"])
    if not catalog:
        return

    for link in catalog.get("links", []):
        if len(features) >= MAX_SCENES_PER_PROVIDER:
            break
        rel = link.get("rel", "")
        href = link.get("href", "")
        if rel in ("child", "collection", "item") and href:
            abs_url = resolve_url(PROVIDERS["umbra"]["catalog_url"], href)
            if rel == "item":
                item = fetch_json(abs_url)
                if item:
                    f = normalize_item(item, "umbra")
                    if f:
                        features.append(f)
            else:
                walk_catalog(abs_url, "umbra", features, depth=1)


def fetch_capella(features):
    print("  Fetching Capella catalog...")
    walk_catalog(PROVIDERS["capella"]["catalog_url"], "capella", features)


def fetch_iceye(features):
    print("  Fetching ICEYE catalog...")
    walk_catalog(PROVIDERS["iceye"]["catalog_url"], "iceye", features)


def main():
    all_features = {"umbra": [], "capella": [], "iceye": []}

    # Try to load existing data as fallback
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

    fetch_umbra(all_features["umbra"])
    fetch_capella(all_features["capella"])
    fetch_iceye(all_features["iceye"])

    # Fall back to existing data for any provider that returned nothing
    for pid in ("umbra", "capella", "iceye"):
        if not all_features[pid] and existing.get(pid):
            print(f"  [INFO] Using cached data for {pid} ({len(existing[pid])} scenes).")
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
    print(f"\nWrote {len(merged)} total scenes to {OUT_PATH}")


if __name__ == "__main__":
    main()
