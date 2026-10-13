#!/usr/bin/env python3
"""
Fetches open SAR scene metadata from pre-built GeoParquet files maintained by
Jack-Hayes/commerical-sar-stac (updated weekly). Outputs a merged GeoJSON to
data/scenes.geojson.

Credit: https://github.com/Jack-Hayes/commerical-sar-stac
"""

import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

warnings.filterwarnings("ignore")  # suppress numpy version warnings in some envs


#: Polarizations arrive in whatever shape the upstream parquet cell happened to
#: hold. Most often that is a *Python* repr of a list, "['VV']", which is not
#: JSON: json.loads rejects the single quotes, and the original code swallowed
#: that in a bare except, so every scene in the catalog carried the literal
#: characters "['VV']" as its polarization for display, for STAC and for
#: anything trying to filter on it.
#:
#: Rather than guess which serialisation format produced the cell, this ignores
#: the punctuation entirely: split on any plausible separator and keep the
#: letters. A channel name is only ever letters, so there is nothing to lose,
#: and it handles JSON, Python reprs, bare strings and real lists alike. An
#: earlier version tried json.loads then ast.literal_eval first; those branches
#: turned out to agree with this on every shape the catalog contains, and to be
#: worse on "['VV+VH']", which literal_eval reads as the single token "VVVH".
_NON_CHANNEL = {"NAN", "NONE", "NULL", "NA"}


def parse_polarizations(value):
    """Normalize a polarization cell to a list like ``["VV"]``.

    Accepts a list, a JSON array string, a Python-repr string, a delimited
    string, or None. Returns [] for anything with no channels in it, so a
    caller can tell "no polarization" from "some polarization" without
    inspecting types.
    """
    if value is None:
        return []
    items = (value if isinstance(value, (list, tuple))
             else re.split(r"[,;/+]", str(value)))
    out = []
    for it in items:
        tok = re.sub(r"[^A-Za-z]", "", str(it)).upper()
        if tok and tok not in _NON_CHANNEL and tok not in out:
            out.append(tok)
    return out


# Deliberately not imported at module scope. This used to be a bare
# `import pyarrow ... except ImportError: sys.exit(1)` out here, which made the
# module impossible to import without the full pipeline dependencies installed:
# anything that just wanted a helper from this file — a test, another script —
# killed the interpreter instead. Under pytest that surfaced as a collection
# INTERNALERROR that took the entire suite down with it, not just the one file.
# The check belongs where the dependency is actually used.
def _parquet():
    """pyarrow.parquet, or a clear error at the point it is needed."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("ERROR: pyarrow is required. Install with: pip install pyarrow",
              file=sys.stderr)
        raise SystemExit(1)
    return pq

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


class FetchError(RuntimeError):
    """A parquet could not be downloaded or read. Carries the file name."""


#: HTTP statuses worth a second attempt. Anything else in the 4xx range means
#: the request itself is wrong (the file moved, say) and retrying is noise.
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


def fetch_bytes(url, timeout=60, attempts=3, backoff=2.0):
    """Download ``url``, retrying transient failures with exponential backoff.

    The weekly run reads ten files from raw.githubusercontent.com. Before this
    retried, one dropped connection on one of them meant the provider silently
    served last week's cached scenes, and nothing in the run said so: a single
    bad second cost a week of data with a green tick on it.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "open-sar-triad/1.0"})
    last = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in _RETRY_STATUSES:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        if attempt < attempts:
            wait = backoff * 2 ** (attempt - 1)
            print(f"    retry {attempt}/{attempts - 1} in {wait:g}s: {last}", file=sys.stderr)
            time.sleep(wait)
    raise last


def read_parquet(url):
    """Download a parquet file and return a pandas DataFrame.

    Raises FetchError rather than returning None. The None path let a provider
    with several files (Capella has seven) carry on with whichever subset had
    arrived, which produced a catalog missing the acquisitions that live only
    in the failed file and missing that format from every other scene, and
    recorded none of it. A provider is fetched whole or not at all.
    """
    fname = url.split("/")[-1]
    try:
        data = fetch_bytes(url)
    except Exception as e:
        raise FetchError(f"{fname}: could not download ({e})") from e
    try:
        return _parquet().read_table(io.BytesIO(data)).to_pandas()
    except Exception as e:
        raise FetchError(f"{fname}: could not read as parquet ({e})") from e


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

    # Skip collection footprint entries (not real acquisitions). Checked before
    # anything is parsed, so a footprint row costs nothing and cannot trip a
    # parser on its odd shape.
    item_id = str(row.get("id", ""))
    if "footprint" in item_id.lower() or "collection" in item_id.lower():
        return None

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
    # Reject clearly erroneous future dates (> 1 year ahead). Umbra's parquet
    # carries a handful of scenes dated 2035. Added as a timedelta: the earlier
    # `now.replace(year=now.year + 1)` raises on 29 February.
    cutoff_future = (datetime.now(timezone.utc) + timedelta(days=366)).strftime("%Y-%m-%d")
    if date_str and date_str > cutoff_future:
        return None

    # Sensor metadata
    sensor_mode  = row.get("sar:instrument_mode") or row.get("instrument_mode") or "N/A"
    resolution   = row.get("sar:resolution_range") or row.get("sar:pixel_spacing_range") or row.get("gsd")
    polarization = ", ".join(parse_polarizations(row.get("sar:polarizations")))

    if resolution is not None:
        try:
            f = float(resolution)
            resolution = round(f, 2) if math.isfinite(f) else None
        except Exception:
            resolution = None

    thumbnail, download, products = parse_assets(row.get("assets"), provider_id)

    # Incidence angle / off-nadir (ICEYE uses these instead of resolution)
    def safe_float(v, decimals=1):
        if v is None: return None
        try:
            f = float(v)
            return round(f, decimals) if math.isfinite(f) else None
        except Exception:
            return None
    incidence_angle = safe_float(row.get("view:incidence_angle"))
    off_nadir       = safe_float(row.get("view:off_nadir"))

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

    # Any file failing fails the provider (read_parquet raises), and main()
    # then serves the cached copy whole. Merging the files that did arrive
    # would quietly produce a smaller catalog.
    for url in info["parquets"]:
        fname = url.split("/")[-1]
        print(f"  [{info['label']}] Fetching {fname}…")
        df = read_parquet(url)
        print(f"    → {len(df)} rows")
        all_rows.append(df)

    import pandas as pd
    merged = pd.concat(all_rows, ignore_index=True)

    # Deduplicate by id. Umbra lists a few hundred scenes twice, under two S3
    # prefixes for the same objects (task-data/<id>/ and tasks/<name>/<id>/);
    # both resolve, so keeping the first is fine.
    if "id" in merged.columns:
        merged = merged.drop_duplicates(subset=["id"])

    features = []
    skipped = 0
    for _, row in merged.iterrows():
        feat = normalize_row(row, provider_id)
        if feat:
            features.append(feat)
        else:
            skipped += 1

    print(f"  [{info['label']}] ✓ {len(features)} scenes"
          + (f" ({skipped} rows skipped: collection footprints, "
             "unparseable geometry or implausible dates)" if skipped else ""))

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
        except Exception as e:
            # Not fatal, but not silent either: with no cache, a provider that
            # fails to fetch below has nothing to fall back on.
            print(f"  [WARN] could not load {OUT_PATH} as fallback: {e}", file=sys.stderr)

    all_features = {}
    schema_failures = []
    # Providers that could not be fetched this run, with the reason. Written
    # into the catalog header so the validator can warn and the health issue
    # opens the same day. Before this the fallback was a line in a log nobody
    # reads, and the first signal was the provider's first_seen going stale
    # three weeks later.
    fetch_failures = {}
    for pid in ("iceye", "umbra", "capella"):
        reason = None
        try:
            all_features[pid] = fetch_provider(pid)
        except SchemaError as e:
            # Not transient: surface it so the run fails after writing fallback.
            print(f"  [{pid}] SCHEMA ERROR: {e}", file=sys.stderr)
            schema_failures.append(pid)
            all_features[pid] = []
            reason = str(e)
        except Exception as e:
            print(f"  [{pid}] ERROR: {e}", file=sys.stderr)
            all_features[pid] = []
            reason = str(e)

        if not all_features[pid]:
            fetch_failures[pid] = reason or "fetched no scenes"
            if existing.get(pid):
                print(f"  [{pid}] Falling back to cached data ({len(existing[pid])} scenes).")
                all_features[pid] = existing[pid]
            else:
                print(f"  [{pid}] No cached copy to fall back on; provider omitted.",
                      file=sys.stderr)

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
        # {provider: reason} for providers that were not fetched this run;
        # their scenes, if any, are the previous run's cached copy. Empty on a
        # clean run.
        "fetch_failures": fetch_failures,
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
