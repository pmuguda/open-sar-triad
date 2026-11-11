#!/usr/bin/env python3
"""
Reads the three providers' own open-data buckets, so the catalog does not wait
on anyone else's schedule.

Why this exists
---------------
The catalog was built entirely from one upstream aggregator, which crawls the
providers once a week, on Sundays. Anything a provider uploaded after that
crawl waited for the next one and then for our Monday run: Umbra published 19
scenes on a Tuesday and Wednesday in November 2026 and they could not reach
the site for six days. ICEYE was worse in a different way: 24 scenes sat in its
bucket for weeks because ICEYE had not yet added them to its own catalog,
which is the only thing the aggregator reads.

The aggregator is still the bulk source, since it has already fetched and
normalised fifteen thousand records and re-fetching them weekly would be
wasteful and rude to the providers. This module is the supplement: it lists
each provider's bucket, works out which scenes there the catalog does not
already have, and fetches only those.

What each provider publishes
----------------------------
umbra    One ``<stem>.stac.v2.json`` per scene under ``sar-data/``, beside the
         product files. Its ``assets`` point into a private bucket, so the
         public assets are built from the files that are actually there.
iceye    Catalogued scenes have an item under ``stac-items/YYYY/MM/``. Scenes
         it has uploaded but not yet catalogued have none, but every product
         in ``data/<mode>/<id>/`` ships a sidecar (``<id>_GRD.json`` and so
         on) that is itself a STAC Feature with the same footprint and time,
         so a scene can be read from its folder alone.
capella  One item per product under ``stac/capella-open-data-by-datetime/``,
         named for its id.

Everything here fails soft. A bucket that cannot be listed, or an item that
will not download, costs the supplement and nothing else: the aggregator's
scenes are untouched and the failure is returned for the caller to record.

Nothing in this file imports pandas or pyarrow, so it is tested as plain Python.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

#: Hosts match the ones the providers' own asset links use, so a synthesised
#: link is on the same host as every other link for that provider.
BUCKETS = {
    "umbra": "https://umbra-open-data-catalog.s3.us-west-2.amazonaws.com",
    "iceye": "https://iceye-open-data-catalog.s3.amazonaws.com",
    "capella": "https://capella-open-data.s3.amazonaws.com",
}

#: What to list. Umbra and Capella need one prefix; ICEYE needs its catalogue
#: and its data folders, because the point is to find data the catalogue lacks.
PREFIXES = {
    "umbra": ["sar-data/"],
    "iceye": ["stac-items/", "data/"],
    "capella": ["stac/capella-open-data-by-datetime/"],
}

UMBRA_STAC_SUFFIX = ".stac.v2.json"
#: Public product files that sit beside an Umbra item, and the asset key each
#: becomes. The same table the aggregator uses, applied only to files that exist.
UMBRA_PRODUCTS = {
    "gec": "_GEC.tif",
    "csi": "_CSI.tif",
    "sicd": "_SICD.nitf",
    "sidd": "_SIDD.nitf",
    "cphd": "_CPHD.cphd",
}

#: ICEYE sidecars to read a scene from, best first. Any of them carries the
#: footprint and acquisition time; GRD is the one every scene has.
ICEYE_SIDECARS = ["GRD", "SLC", "CSI", "QLK", "VID"]
#: ICEYE_<token>_<acquired>Z_<frame>_X<satellite>_<mode>. The mode suffix has
#: digits in it for some products (SLF3L, SLF1L), which a letters-only pattern
#: silently skipped: 64 catalogued scenes did not match the first version.
ICEYE_ID = re.compile(r"^ICEYE_[A-Z0-9]+_\d{8}T\d{6}Z_\d+_X\d+_[A-Z0-9]+$")

#: A folder still being uploaded has some of its files and not others. ICEYE
#: scenes read from a folder are only taken once the folder has been quiet for
#: this long.
SETTLE_HOURS = 6

#: Ceiling on items fetched per provider per run. In a normal week the number
#: is a few dozen; this bounds the run if the aggregator ever stops entirely
#: and every scene a provider has looks new at once.
MAX_ITEMS = 3000

_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


class BucketError(RuntimeError):
    """A bucket could not be listed."""


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #
def list_bucket(base: str, prefix: str, get, max_pages: int = 2000) -> list[tuple[str, str]]:
    """Every (key, last_modified) under ``prefix``.

    ``get`` is a callable taking a URL and returning bytes; the caller's own
    retrying downloader in production, a fake in tests. Raises BucketError on
    any failure, including a truncated listing, because half a listing would
    make scenes that are there look like scenes that were removed.
    """
    out: list[tuple[str, str]] = []
    token = None
    for _ in range(max_pages):
        query = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            query["continuation-token"] = token
        url = f"{base}/?{urllib.parse.urlencode(query)}"
        try:
            root = ET.fromstring(get(url))
        except Exception as e:
            raise BucketError(f"could not list {base}/{prefix}: {e}") from e
        for c in root.findall("s3:Contents", _NS):
            key = c.findtext("s3:Key", default="", namespaces=_NS)
            if key:
                out.append((key, c.findtext("s3:LastModified", default="", namespaces=_NS)))
        if (root.findtext("s3:IsTruncated", default="false", namespaces=_NS) or "").lower() != "true":
            return out
        token = root.findtext("s3:NextContinuationToken", default="", namespaces=_NS)
        if not token:
            raise BucketError(f"listing of {base}/{prefix} was truncated without a continuation token")
    raise BucketError(f"listing of {base}/{prefix} did not finish in {max_pages} pages")


def public_url(provider: str, key: str) -> str:
    """The URL a key is served at. Umbra's task folders have spaces in them."""
    return f"{BUCKETS[provider]}/{urllib.parse.quote(key)}"


# --------------------------------------------------------------------------- #
# Discovery: which scenes does the bucket hold?
# --------------------------------------------------------------------------- #
def discover(provider: str, listing: list[tuple[str, str]], now: datetime | None = None) -> dict[str, dict]:
    """Scenes found in a listing, by the key the catalog can recognise them by.

    Each value says where the scene's metadata is (``item_key``), which files
    sit beside it (``files``: name -> key) and when it was last touched.
    """
    return {"umbra": _discover_umbra, "iceye": _discover_iceye,
            "capella": _discover_capella}[provider](listing, now or datetime.now(timezone.utc))


def _by_dir(listing):
    dirs: dict[str, dict[str, str]] = {}
    stamps: dict[str, str] = {}
    for key, modified in listing:
        d, _, name = key.rpartition("/")
        if not name:
            continue
        dirs.setdefault(d, {})[name] = key
        if modified > stamps.get(d, ""):
            stamps[d] = modified
    return dirs, stamps


def _discover_umbra(listing, now):
    dirs, stamps = _by_dir(listing)
    found = {}
    for d, files in dirs.items():
        for name, key in files.items():
            if name.endswith(UMBRA_STAC_SUFFIX):
                found[key] = {"item_key": key, "dir": d, "files": files,
                              "stem": name[:-len(UMBRA_STAC_SUFFIX)],
                              "modified": stamps[d], "kind": "item"}
    return found


def _discover_iceye(listing, now):
    dirs, stamps = _by_dir(listing)
    found = {}
    for d, files in dirs.items():
        if d.startswith("stac-items/"):
            for name, key in files.items():
                scene_id = name[:-len(".json")] if name.endswith(".json") else ""
                if ICEYE_ID.match(scene_id):
                    found[scene_id] = {"item_key": key, "dir": d, "files": {},
                                       "modified": stamps[d], "kind": "item"}
    settled = (now - timedelta(hours=SETTLE_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    for d, files in dirs.items():
        scene_id = d.rpartition("/")[2]
        if not d.startswith("data/") or not ICEYE_ID.match(scene_id) or scene_id in found:
            continue
        sidecar = next((files[f"{scene_id}_{s}.json"] for s in ICEYE_SIDECARS
                        if f"{scene_id}_{s}.json" in files), None)
        if sidecar is None or stamps[d][:19] > settled:
            continue                     # nothing to read it from, or still arriving
        found[scene_id] = {"item_key": sidecar, "dir": d, "files": files,
                           "modified": stamps[d], "kind": "folder"}
    return found


def _discover_capella(listing, now):
    found = {}
    for key, modified in listing:
        d, _, name = key.rpartition("/")
        scene_id = d.rpartition("/")[2]
        if scene_id.startswith("CAPELLA_") and name == f"{scene_id}.json":
            found[scene_id] = {"item_key": key, "dir": d, "files": {},
                               "modified": modified, "kind": "item"}
    return found


# --------------------------------------------------------------------------- #
# Recognition: which of those does the catalog already have?
# --------------------------------------------------------------------------- #
def known_key(provider: str, scene_id: str | None, urls: list[str]) -> str | None:
    """The discovery key a catalog scene corresponds to.

    Umbra is recognised by where its files are, because its ids live only
    inside the item file and reading twelve thousand of those to compare ids
    would defeat the purpose. The others are recognised by id: for Capella the
    id of the product, which is the folder its file sits in, since the catalog
    collapses a scene's products into one record with a shortened id.
    """
    if provider == "iceye":
        return scene_id
    for url in urls:
        if not url:
            continue
        path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).lstrip("/")
        if provider == "capella":
            return path.rpartition("/")[0].rpartition("/")[2] or None
        for suffix in UMBRA_PRODUCTS.values():
            if path.endswith(suffix):
                return path[:-len(suffix)] + UMBRA_STAC_SUFFIX
    return None


def known_keys(provider: str, features: list[dict]) -> set[str]:
    """Discovery keys for everything already in a list of catalog features."""
    out = set()
    for f in features:
        p = f.get("properties") or {}
        products = p.get("products") or {}
        if provider == "capella":
            # One key per product: a scene may be known as GEC and new as SLC.
            for url in list(products.values()) + [p.get("download")]:
                k = known_key(provider, p.get("id"), [url])
                if k:
                    out.add(k)
            continue
        k = known_key(provider, p.get("id"), list(products.values()) + [p.get("download")])
        if k:
            out.add(k)
    return out


# --------------------------------------------------------------------------- #
# Reading a scene
# --------------------------------------------------------------------------- #
def _umbra_assets(found: dict) -> dict:
    return {key: {"href": public_url("umbra", found["files"][found["stem"] + suffix])}
            for key, suffix in UMBRA_PRODUCTS.items() if found["stem"] + suffix in found["files"]}


def _iceye_folder_assets(scene_id: str, found: dict) -> dict:
    """Asset keys in ICEYE's own scheme (``grd-cog``, ``slc-json``, ...) for the
    files in a scene's folder, so a scene read from its folder is
    indistinguishable from one read from the catalogue."""
    kinds = {"tif": "cog", "json": "json", "kml": "kml", "mp4": "mp4", "gif": "gif"}
    assets = {}
    for name, key in found["files"].items():
        m = re.fullmatch(re.escape(scene_id) + r"_([A-Z]+)\.([a-z0-9]+)", name)
        if not m:
            continue
        product, ext = m.group(1).lower(), m.group(2)
        if ext in kinds:
            assets[f"{product}-{kinds[ext]}"] = {"href": public_url("iceye", key)}
        elif ext == "png" and product in ("thm", "csi", "qlk") and "thumbnail" not in assets:
            assets["thumbnail"] = {"href": public_url("iceye", key)}
    return assets


def to_row(provider: str, key: str, found: dict, item: dict) -> dict:
    """Flatten a provider's item into the shape of one of the aggregator's rows,
    so fetch_catalog.normalize_row treats both exactly alike."""
    if not isinstance(item, dict) or not isinstance(item.get("geometry"), dict):
        raise ValueError("not a STAC item with a geometry")
    row = dict(item.get("properties") or {})
    row["geometry_geojson"] = json.dumps(item["geometry"])
    row["collection"] = item.get("collection")
    if provider == "umbra":
        row["id"] = item.get("id")
        row["assets"] = _umbra_assets(found)
        if not row["assets"]:
            raise ValueError("no public product files beside the item")
    elif provider == "iceye" and found["kind"] == "folder":
        row["id"] = key                  # the sidecar's own id names the product, not the scene
        row["assets"] = _iceye_folder_assets(key, found)
    else:
        row["id"] = item.get("id")
        row["assets"] = item.get("assets") or {}
    if not row["id"]:
        raise ValueError("item has no id")
    return row


def supplement(provider: str, known: set[str], get, now: datetime | None = None,
               max_items: int = MAX_ITEMS, workers: int = 8) -> tuple[list[dict], dict]:
    """Rows for scenes the bucket holds and the catalog does not.

    Returns ``(rows, stats)``. Never raises: ``stats["error"]`` says why the
    bucket could not be listed, ``stats["failed"]`` counts items that could not
    be read. ``stats["listing"]`` is the set of keys seen, for callers that
    want to check links against it; it is not meant for the catalog header.
    """
    stats = {"listed": 0, "missing": 0, "added": 0, "failed": 0, "from_folders": 0,
             "error": None, "truncated": False, "listing": None}
    try:
        listing = []
        for prefix in PREFIXES[provider]:
            listing += list_bucket(BUCKETS[provider], prefix, get)
    except BucketError as e:
        stats["error"] = str(e)
        return [], stats

    found = discover(provider, listing, now)
    stats["listed"] = len(found)
    stats["listing"] = {key for key, _ in listing}
    missing = sorted((k for k in found if k not in known),
                     key=lambda k: found[k]["modified"], reverse=True)
    stats["missing"] = len(missing)
    if len(missing) > max_items:
        stats["truncated"] = True
        missing = missing[:max_items]    # newest first, so the cap drops the oldest

    def read(key):
        try:
            item = json.loads(get(public_url(provider, found[key]["item_key"])))
            return to_row(provider, key, found[key], item)
        except Exception:
            return None

    if missing:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(read, missing))
    else:
        results = []
    rows = []
    for key, row in zip(missing, results):
        if row is None:
            stats["failed"] += 1
            continue
        rows.append(row)
        if found[key]["kind"] == "folder":
            stats["from_folders"] += 1
    stats["added"] = len(rows)
    return rows, stats


# --------------------------------------------------------------------------- #
# Links that point at nothing
# --------------------------------------------------------------------------- #
def prune_missing_products(provider: str, features: list[dict], listing: set[str]) -> int:
    """Drop product links whose file is not in the bucket. Returns how many.

    The aggregator writes all five Umbra formats for every scene, derived from
    the item's name, whether or not the provider published them. Checked
    against the bucket in November 2026, 5,260 of the catalog's 11,904 CSI
    links, 2,336 CPHD links and several hundred others pointed at nothing, so
    the site offered downloads that returned an error. With the listing in
    hand each link can simply be checked.

    A scene none of whose links exist is left exactly as it was: something is
    wrong that this cannot diagnose, and blanking a scene's downloads on the
    strength of a listing is the more destructive mistake.
    """
    host = urllib.parse.urlsplit(BUCKETS[provider]).netloc
    removed = 0
    for f in features:
        p = f.get("properties") or {}
        products = p.get("products")
        if not products:
            continue
        kept = {}
        for fmt, url in products.items():
            parts = urllib.parse.urlsplit(url or "")
            if parts.netloc != host or urllib.parse.unquote(parts.path).lstrip("/") in listing:
                kept[fmt] = url
        if kept and len(kept) < len(products):
            removed += len(products) - len(kept)
            p["products"] = kept
            if p.get("download") not in kept.values():
                p["download"] = next(iter(kept.values()))
    return removed
