"""
Python client for the open-sar-triad static API.

Design notes worth knowing if you are reading this to learn:

* There is no server. Every endpoint is a file on GitHub Pages, rebuilt weekly.
  `search()` therefore filters locally, over a 0.6 MB (gzipped) index that is
  fetched once and cached. At 14,798 scenes that is faster than a round trip.

* Download URLs are NOT in the index; they live in the larger per-provider
  files. The client fetches those lazily, only when you ask for a URL or call
  `download()`, and caches them per provider.

* Zero required dependencies. Everything here is the standard library, so
  `pip install open-sar-triad` pulls in nothing. pandas/geopandas are optional
  and only used by the `to_dataframe()` helpers.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import urllib.error
import urllib.request
from collections.abc import Sequence as _SequenceABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

DEFAULT_BASE_URL = "https://www.pmuguda.com/open-sar-triad/api/v1"
USER_AGENT = "open-sar-triad-python/1.0 (+https://github.com/pmuguda/open-sar-triad)"

#: Product families and the order each resolves in. Different providers name the
#: same kind of product differently, so asking for ``"complex"`` returns ``SLC``
#: where it exists and falls back to ``SICD`` (which is what Umbra publishes).
FAMILIES = {
    "detected": ["GEO", "GRD", "GEC", "SIDD"],
    "complex": ["SLC", "SICD"],
    "phase": ["CPHD"],
    "visual": ["CSI", "VID"],
}

PROVIDERS = ("iceye", "umbra", "capella")


class OpenSarTriadError(RuntimeError):
    """Any failure talking to the API."""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _fetch(url: str, timeout: int = 60) -> bytes:
    """GET a URL, transparently decompressing gzip.

    GitHub Pages will gzip these files if asked, which turns the 2.5 MB index
    into roughly 0.6 MB on the wire. urllib does not request or decode that by
    default, so we do both here.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw
    except urllib.error.HTTPError as e:
        raise OpenSarTriadError(f"HTTP {e.code} fetching {url}") from e
    except urllib.error.URLError as e:
        raise OpenSarTriadError(f"Could not reach {url}: {e.reason}") from e


def _fetch_json(url: str, timeout: int = 60):
    try:
        return json.loads(_fetch(url, timeout))
    except json.JSONDecodeError as e:
        raise OpenSarTriadError(f"Invalid JSON from {url}: {e}") from e


def _as_date(value) -> str | None:
    """Accept 'YYYY-MM-DD', a date, or a datetime; return an ISO day string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:10]
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    raise TypeError(f"Cannot interpret {value!r} as a date")


def _bbox_intersects(a: Sequence[float], b: Sequence[float]) -> bool:
    """Rectangle overlap. Note: does not special-case the antimeridian; a box
    spanning +/-180 should be passed as two boxes."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


# --------------------------------------------------------------------------- #
# Scene
# --------------------------------------------------------------------------- #
@dataclass
class Scene:
    """One SAR acquisition.

    The cheap fields (id, provider, date, mode, orbit, look, formats, bbox) come
    straight from the search index. Asset URLs are resolved lazily from the
    provider's full record the first time you ask for one.
    """

    id: str
    provider: str
    date: str | None
    mode: str | None
    orbit: str | None
    look: str | None
    formats: list[str] = field(default_factory=list)
    bbox: list[float] = field(default_factory=list)
    _catalog: "Catalog | None" = field(default=None, repr=False, compare=False)

    @property
    def year(self) -> int | None:
        return int(self.date[:4]) if self.date else None

    def _record(self) -> dict:
        if self._catalog is None:
            raise OpenSarTriadError("Scene is detached from a Catalog")
        return self._catalog._record_for(self.provider, self.id)

    @property
    def assets(self) -> dict:
        """Full STAC asset map, fetched on demand."""
        return self._record().get("assets", {})

    @property
    def properties(self) -> dict:
        """Full STAC properties (polarization, resolution, angles, ...)."""
        return self._record().get("properties", {})

    @property
    def geometry(self) -> dict:
        return self._record().get("geometry", {})

    def url(self, fmt: str) -> str | None:
        """Download URL for one exact format, e.g. ``scene.url("SLC")``."""
        a = self.assets.get(fmt.upper())
        return a.get("href") if a else None

    def metadata_url(self, fmt: str) -> str | None:
        """URL of the provider's metadata sidecar for that format, if published."""
        a = self.assets.get(f"{fmt.upper()}_metadata")
        return a.get("href") if a else None

    def resolve(self, family: str) -> str | None:
        """The format this scene contributes for a product family.

        ``scene.resolve("complex")`` gives ``"SLC"`` for ICEYE/Capella and
        ``"SICD"`` for Umbra, because that is what each provider actually
        publishes.
        """
        order = FAMILIES.get(family.lower())
        if order is None:
            raise ValueError(f"Unknown family {family!r}; expected one of {list(FAMILIES)}")
        return next((f for f in order if f in self.formats), None)

    def __repr__(self) -> str:
        return (f"Scene({self.id!r}, provider={self.provider!r}, date={self.date!r}, "
                f"mode={self.mode!r}, formats={self.formats})")


# --------------------------------------------------------------------------- #
# SceneCollection
# --------------------------------------------------------------------------- #
class SceneCollection(_SequenceABC):
    """An immutable, list-like result set with export and download helpers."""

    def __init__(self, scenes: Iterable[Scene], catalog: "Catalog | None" = None):
        self._scenes: list[Scene] = list(scenes)
        self._catalog = catalog

    # -- Sequence protocol ------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._scenes)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return SceneCollection(self._scenes[i], self._catalog)
        return self._scenes[i]

    def __iter__(self) -> Iterator[Scene]:
        return iter(self._scenes)

    def __repr__(self) -> str:
        if not self._scenes:
            return "SceneCollection(empty)"
        by_p: dict[str, int] = {}
        for s in self._scenes:
            by_p[s.provider] = by_p.get(s.provider, 0) + 1
        dates = sorted(s.date for s in self._scenes if s.date)
        span = f"{dates[0]} to {dates[-1]}" if dates else "undated"
        counts = ", ".join(f"{k} {v}" for k, v in sorted(by_p.items(), key=lambda kv: -kv[1]))
        return f"SceneCollection({len(self._scenes)} scenes; {counts}; {span})"

    # -- Export ------------------------------------------------------------ #
    def to_geojson(self) -> dict:
        """A GeoJSON FeatureCollection. Fetches full records, so it is heavier
        than the search itself."""
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": s.id, "geometry": s.geometry,
                 "properties": {"id": s.id, "provider": s.provider, "date": s.date,
                                "mode": s.mode, "orbit": s.orbit, "look": s.look,
                                **s.properties}}
                for s in self._scenes
            ],
        }

    def to_stac(self) -> dict:
        """A STAC ItemCollection, ready for pystac or stackstac."""
        return {
            "type": "FeatureCollection",
            "stac_version": "1.0.0",
            "features": [s._record() for s in self._scenes],
        }

    def to_dataframe(self):
        """A pandas DataFrame of the index-level fields. Requires pandas."""
        try:
            import pandas as pd
        except ImportError as e:
            raise OpenSarTriadError("to_dataframe() needs pandas: pip install pandas") from e
        return pd.DataFrame([
            {"id": s.id, "provider": s.provider, "date": s.date, "mode": s.mode,
             "orbit": s.orbit, "look": s.look, "formats": ",".join(s.formats),
             "west": s.bbox[0], "south": s.bbox[1], "east": s.bbox[2], "north": s.bbox[3]}
            for s in self._scenes
        ])

    def save_geojson(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_geojson()))
        return path

    # -- Download ---------------------------------------------------------- #
    def download_urls(self, family: str | None = None,
                      formats: Sequence[str] | None = None,
                      metadata: bool = True) -> list[dict]:
        """Resolve what would be downloaded, without downloading anything.

        Give either ``family`` ("detected", "complex", "phase", "visual") or an
        explicit list of ``formats``. Each scene contributes one file per family.
        """
        if family and formats:
            raise ValueError("Pass either family or formats, not both")
        if not family and not formats:
            family = "detected"

        jobs: list[dict] = []
        seen_meta: set[str] = set()
        for s in self._scenes:
            wanted = ([s.resolve(family)] if family
                      else [f.upper() for f in formats if f.upper() in s.formats])
            for fmt in filter(None, wanted):
                url = s.url(fmt)
                if not url:
                    continue
                jobs.append({"scene": s.id, "provider": s.provider, "format": fmt,
                             "url": url, "kind": "data"})
                if metadata:
                    m = s.metadata_url(fmt)
                    # Umbra publishes one sidecar per acquisition, shared by all
                    # its formats, so the same URL can come up more than once.
                    if m and m not in seen_meta:
                        seen_meta.add(m)
                        jobs.append({"scene": s.id, "provider": s.provider, "format": fmt,
                                     "url": m, "kind": "metadata"})
        return jobs

    def download(self, dest: str | Path = ".", family: str | None = None,
                 formats: Sequence[str] | None = None, metadata: bool = True,
                 dry_run: bool = False, skip_existing: bool = True,
                 quiet: bool = False) -> list[Path]:
        """Download resolved products into ``dest/<provider>/``.

        Set ``dry_run=True`` first. SAR products are large (a single GRD can
        exceed 1 GB), so always check the file count before committing.
        """
        dest = Path(dest)
        jobs = self.download_urls(family=family, formats=formats, metadata=metadata)
        data_n = sum(1 for j in jobs if j["kind"] == "data")
        meta_n = len(jobs) - data_n

        if not quiet:
            print(f"{len(jobs)} file(s): {data_n} data + {meta_n} metadata, "
                  f"from {len(self._scenes)} scene(s)")
        if dry_run:
            if not quiet:
                for j in jobs[:20]:
                    print(f"  [{j['kind']:8}] {j['provider']}/{j['url'].rsplit('/', 1)[-1]}")
                if len(jobs) > 20:
                    print(f"  ... and {len(jobs) - 20} more")
            return []

        written: list[Path] = []
        for i, j in enumerate(jobs, 1):
            out_dir = dest / j["provider"]
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / j["url"].rsplit("/", 1)[-1].split("?")[0]
            if skip_existing and out.exists() and out.stat().st_size > 0:
                if not quiet:
                    print(f"  [{i}/{len(jobs)}] exists, skipping {out.name}")
                written.append(out)
                continue
            if not quiet:
                print(f"  [{i}/{len(jobs)}] {out.name}")
            tmp = out.with_suffix(out.suffix + ".part")
            try:
                req = urllib.request.Request(j["url"], headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
                    shutil.copyfileobj(r, fh, length=1024 * 256)
                tmp.replace(out)
                written.append(out)
            except Exception as e:  # keep going; one bad asset should not stop a batch
                tmp.unlink(missing_ok=True)
                print(f"      failed: {e}")
        return written


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #
class Catalog:
    """Entry point.

    >>> from opensartriad import Catalog
    >>> cat = Catalog()
    >>> cat.stats()["total"]
    14798
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._index: list[Scene] | None = None
        self._stats: dict | None = None
        self._records: dict[str, dict[str, dict]] = {}  # provider -> id -> STAC item

    # -- endpoints --------------------------------------------------------- #
    def stats(self) -> dict:
        """Counts by provider, mode, year and format, plus the temporal extent."""
        if self._stats is None:
            self._stats = _fetch_json(f"{self.base_url}/stats.json", self.timeout)
        return self._stats

    def _load_index(self) -> list[Scene]:
        if self._index is not None:
            return self._index
        doc = _fetch_json(f"{self.base_url}/index.json", self.timeout)
        fields = doc["fields"]
        # This is the payoff of the compact wire format: zip each row against the
        # field names so callers get attributes, not positional indexing.
        self._index = [
            Scene(**dict(zip(fields, row)), _catalog=self) for row in doc["scenes"]
        ]
        return self._index

    def _record_for(self, provider: str, scene_id: str) -> dict:
        """Full STAC item, fetching and caching that provider's file on first use."""
        if provider not in self._records:
            doc = _fetch_json(f"{self.base_url}/items/{provider}.json", self.timeout)
            self._records[provider] = {f["id"]: f for f in doc.get("features", [])}
        rec = self._records[provider].get(scene_id)
        if rec is None:
            raise OpenSarTriadError(f"Scene {scene_id!r} not found in {provider} items")
        return rec

    # -- search ------------------------------------------------------------ #
    def search(self, bbox: Sequence[float] | None = None,
               start=None, end=None,
               providers: str | Sequence[str] | None = None,
               mode: str | None = None,
               orbit: str | None = None,
               look: str | None = None,
               formats: str | Sequence[str] | None = None,
               family: str | None = None,
               limit: int | None = None) -> SceneCollection:
        """Find scenes. All filters combine with AND; omitted filters do nothing.

        Parameters
        ----------
        bbox : (west, south, east, north) in EPSG:4326. Matches scenes whose
            footprint bounding box intersects it.
        start, end : 'YYYY-MM-DD' strings, or date/datetime objects. Inclusive.
        providers : 'umbra' or ['umbra', 'capella'].
        mode : sensor mode, e.g. 'spotlight' (case-insensitive).
        orbit : 'ascending' or 'descending'.
        look : 'left' or 'right'.
        formats : keep scenes publishing any of these exact formats.
        family : keep scenes that can satisfy this product family.
        limit : stop after this many matches.

        Returns
        -------
        SceneCollection

        Examples
        --------
        >>> cat.search(bbox=(5.9, 47.2, 10.5, 55.1), start="2025-01-01",
        ...            providers="umbra", family="complex")
        """
        if isinstance(providers, str):
            providers = [providers]
        if isinstance(formats, str):
            formats = [formats]
        prov = {p.lower() for p in providers} if providers else None
        fmts = {f.upper() for f in formats} if formats else None
        fam_order = FAMILIES.get(family.lower()) if family else None
        if family and fam_order is None:
            raise ValueError(f"Unknown family {family!r}; expected one of {list(FAMILIES)}")
        start_s, end_s = _as_date(start), _as_date(end)
        mode_l = mode.lower() if mode else None

        out: list[Scene] = []
        for s in self._load_index():
            if prov and s.provider not in prov:
                continue
            if start_s and (not s.date or s.date < start_s):
                continue
            if end_s and (not s.date or s.date > end_s):
                continue
            if mode_l and (s.mode or "").lower() != mode_l:
                continue
            if orbit and s.orbit != orbit:
                continue
            if look and s.look != look:
                continue
            if fmts and not fmts.intersection(s.formats):
                continue
            if fam_order and not any(f in s.formats for f in fam_order):
                continue
            if bbox and not _bbox_intersects(s.bbox, bbox):
                continue
            out.append(s)
            if limit and len(out) >= limit:
                break
        return SceneCollection(out, self)

    def get(self, scene_id: str) -> Scene | None:
        """One scene by exact id."""
        return next((s for s in self._load_index() if s.id == scene_id), None)

    def all(self) -> SceneCollection:
        """Every scene in the catalog."""
        return SceneCollection(self._load_index(), self)

    # -- STAC passthrough --------------------------------------------------- #
    def stac_catalog(self) -> dict:
        """The STAC root catalog document."""
        return _fetch_json(f"{self.base_url}/catalog.json", self.timeout)

    def stac_collection(self, provider: str) -> dict:
        return _fetch_json(f"{self.base_url}/collections/{provider}.json", self.timeout)

    def stac_items(self, provider: str) -> dict:
        """A provider's full STAC ItemCollection (large; umbra is ~2.7 MB gzipped)."""
        return _fetch_json(f"{self.base_url}/items/{provider}.json", self.timeout)

    def license(self) -> dict:
        """Licence and attribution for the catalog. Scene data is CC-BY 4.0;
        credit the originating provider when you publish derived work."""
        s = self.stats()
        return {k: s[k] for k in ("license", "license_url", "attribution",
                                  "modifications", "disclaimer") if k in s}

    def __repr__(self) -> str:
        return f"Catalog({self.base_url!r})"
