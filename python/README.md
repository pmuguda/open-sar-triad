# open-sar-triad (Python client)

Discover and download open Synthetic Aperture Radar scenes from **ICEYE**, **Umbra** and **Capella** through one interface.

```python
from opensartriad import Catalog

cat = Catalog()
scenes = cat.search(bbox=(5.9, 47.2, 10.5, 55.1), start="2025-01-01", family="complex")
scenes.download("data/", family="complex", dry_run=True)
```

- **No API key, no account, no rate limits.** The backend is a static catalog on a CDN.
- **No required dependencies.** The client is standard library only.
- **Imagery is never proxied.** Downloads go straight to each provider's own storage.

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Searching](#searching)
- [Working with a scene](#working-with-a-scene)
- [Product families](#product-families)
- [Downloading](#downloading)
- [Exporting](#exporting)
- [STAC interoperability](#stac-interoperability)
- [How it works](#how-it-works)
- [API reference](#api-reference)
- [Licence and attribution](#licence-and-attribution)

---

## Install

```bash
pip install open-sar-triad
```

The client has **no required dependencies**: it is standard library only, so this
pulls in nothing else.

Optional extras:

```bash
pip install "open-sar-triad[pandas]"     # to_dataframe()
pip install "open-sar-triad[geo]"        # geopandas / shapely workflows
pip install "open-sar-triad[stac]"       # pystac interop
pip install "open-sar-triad[notebooks]"  # everything the example notebooks use
```

From source:

```bash
git clone https://github.com/pmuguda/open-sar-triad.git
pip install -e open-sar-triad/python
```

---

## Quick start

```python
from opensartriad import Catalog

cat = Catalog()

# What is in the catalog?
stats = cat.stats()
print(stats["total"])            # 14798
print(stats["by_provider"])      # {'umbra': 11892, 'capella': 2469, 'iceye': 437}
print(stats["temporal_extent"])  # ['2020-12-31', '2026-08-19']

# Find scenes over an area, in a date window
scenes = cat.search(
    bbox=(5.9, 47.2, 10.5, 55.1),   # west, south, east, north (EPSG:4326)
    start="2025-01-01",
    end="2026-01-01",
)
print(scenes)
# SceneCollection(129 scenes; umbra 128, iceye 1; 2025-01-30 to 2026-07-09)

# Look at one
s = scenes[0]
print(s.id, s.provider, s.date, s.mode, s.formats)
```

The first `search()` downloads a ~0.6 MB index and caches it on the `Catalog`
instance. Later searches are local and instant, so reuse one `Catalog`.

---

## Searching

All filters combine with **AND**. Omitted filters do nothing.

```python
scenes = cat.search(
    bbox=(72.8, 18.9, 73.1, 19.3),  # (west, south, east, north)
    start="2025-06-01",             # inclusive; str, date or datetime
    end="2025-12-31",               # inclusive
    providers=["umbra", "capella"], # or a single string
    mode="spotlight",               # sensor mode, case-insensitive
    orbit="ascending",              # or "descending"
    look="right",                   # or "left"
    family="complex",               # only scenes that can satisfy this family
    formats=["SLC", "SICD"],        # or exact formats
    limit=100,                      # stop after N matches
)
```

`SceneCollection` behaves like a list:

```python
len(scenes)
scenes[0]
scenes[:10]
for s in scenes:
    ...
[s for s in scenes if s.year == 2025]
```

**bbox and the antimeridian.** Matching is a bounding-box intersection test, so
it is inclusive at the edges: a scene whose footprint box touches your box is
returned even if the polygon itself does not overlap. A box crossing ±180°
should be passed as two searches.

---

## Working with a scene

Cheap fields come from the search index and cost nothing:

```python
s.id          # provider scene id
s.provider    # 'iceye' | 'umbra' | 'capella'
s.date        # 'YYYY-MM-DD'
s.year        # int
s.mode        # sensor mode, lowercase
s.orbit       # 'ascending' | 'descending' | None
s.look        # 'left' | 'right' | None
s.formats     # ['GEC', 'CSI', 'SICD', 'SIDD', 'CPHD']
s.bbox        # [west, south, east, north]
```

These trigger a lazy fetch of that provider's full records the first time (then cached):

```python
s.url("SICD")           # download URL for an exact format
s.metadata_url("SICD")  # provider metadata sidecar, or None if not published
s.properties            # polarization, resolution, incidence angle, ...
s.geometry              # GeoJSON footprint
s.assets                # full STAC asset map
```

---

## Product families

Each provider names the same kind of product differently. Asking for `SLC` by
name returns **nothing** from Umbra, even though all of its scenes carry complex
data, because Umbra labels it `SICD`.

Families solve this. Ask for what the product **is**, and each scene resolves to
its own provider's equivalent:

| Family | Resolves in order |
|---|---|
| `detected` | `GEO` → `GRD` → `GEC` → `SIDD` |
| `complex` | `SLC` → `SICD` |
| `phase` | `CPHD` |
| `visual` | `CSI` → `VID` |

```python
s.resolve("complex")    # 'SICD' for Umbra, 'SLC' for ICEYE/Capella
s.resolve("detected")   # 'GEC' for Umbra, 'GRD' for ICEYE, 'GEO' for Capella
s.resolve("phase")      # 'CPHD', or None if that scene has none
```

Each scene contributes **one file per family**, so you get the best available
product rather than several near-duplicates.

---

## Downloading

> **SAR products are large.** A single GRD can exceed 1 GB. Always `dry_run` first.

```python
# See what would happen; downloads nothing
scenes.download("data/", family="complex", dry_run=True)
# 6 file(s): 3 data + 3 metadata, from 3 scene(s)
#   [data    ] umbra/2025-07-15-06-10-12_UMBRA-08_SICD.nitf
#   [metadata] umbra/2025-07-15-06-10-12_UMBRA-08.stac.v2.json

# Then commit
paths = scenes.download("data/", family="complex")
```

Files land in `data/<provider>/`, with each provider's **metadata sidecar saved
next to every data file**, so your archive arrives documented rather than as a
pile of anonymous rasters.

```python
scenes.download(
    dest="data/",
    family="complex",       # or formats=["SLC", "SICD"]
    metadata=True,          # fetch sidecars (default)
    skip_existing=True,     # resume-friendly (default)
    dry_run=False,
    quiet=False,
)
```

Inspect the plan without downloading:

```python
jobs = scenes.download_urls(family="detected")
len(jobs)
jobs[0]  # {'scene': ..., 'provider': 'umbra', 'format': 'GEC', 'url': ..., 'kind': 'data'}
```

Downloads are resumable: existing non-empty files are skipped, partial files are
written to `.part` and only renamed on success, and one failed asset does not
abort the batch.

---

## Exporting

```python
scenes.to_geojson()             # GeoJSON FeatureCollection (dict)
scenes.save_geojson("aoi.geojson")
scenes.to_stac()                # STAC ItemCollection, for pystac / stackstac
scenes.to_dataframe()           # pandas DataFrame (needs pandas)
```

```python
df = scenes.to_dataframe()
df.groupby("provider").size()
df.groupby(df["date"].str[:7]).size()   # scenes per month
```

---

## STAC interoperability

The backend **is** a STAC catalog, so standard tools work without this client:

```python
import pystac

cat = pystac.Catalog.from_file(
    "https://www.pmuguda.com/open-sar-triad/api/v1/catalog.json"
)
for child in cat.get_children():
    print(child.id, child.title)
```

Or take search results straight into a STAC workflow:

```python
items = scenes.to_stac()["features"]
```

Raw endpoints, if you want to skip the client entirely:

| Endpoint | What |
|---|---|
| `/api/v1/catalog.json` | STAC root catalog |
| `/api/v1/collections/{provider}.json` | STAC Collection |
| `/api/v1/items/{provider}.json` | STAC ItemCollection |
| `/api/v1/index.json` | compact search index |
| `/api/v1/stats.json` | counts and extents |
| `/api/v1/scenes/{provider}.geojson` | raw FeatureCollection |

Base URL: `https://www.pmuguda.com/open-sar-triad/api/v1`

---

## How it works

There is no server. Every endpoint is a file, rebuilt weekly by CI and served
from a CDN. That has real consequences worth knowing:

- **Search is local.** `search()` filters a cached index in memory, so it is
  instant and unmetered. There is nothing to rate limit.
- **Detail is lazy.** Download URLs live in the larger per-provider files, which
  are fetched only when you first ask for a URL, then cached.
- **Reuse one `Catalog`.** A new instance means a new cold cache.
- **Data refreshes weekly.** `stats()["generated"]` tells you when.

```python
cat = Catalog()                              # default hosted API
cat = Catalog("http://localhost:8000/api/v1")  # a local build, for testing
```

---

## API reference

### `Catalog(base_url=DEFAULT_BASE_URL, timeout=60)`

| Method | Returns | Notes |
|---|---|---|
| `search(...)` | `SceneCollection` | see [Searching](#searching) |
| `get(scene_id)` | `Scene \| None` | exact id lookup |
| `all()` | `SceneCollection` | every scene |
| `stats()` | `dict` | counts, extents, families |
| `stac_catalog()` | `dict` | STAC root |
| `stac_collection(provider)` | `dict` | STAC Collection |
| `stac_items(provider)` | `dict` | full ItemCollection (large) |
| `license()` | `dict` | attribution notice |

### `Scene`

Attributes `id`, `provider`, `date`, `year`, `mode`, `orbit`, `look`, `formats`, `bbox`
(free), and `assets`, `properties`, `geometry` (lazy).
Methods `url(fmt)`, `metadata_url(fmt)`, `resolve(family)`.

### `SceneCollection`

A `Sequence` of `Scene`. Methods `download(...)`, `download_urls(...)`,
`to_geojson()`, `save_geojson(path)`, `to_stac()`, `to_dataframe()`.

### Exceptions

`OpenSarTriadError` for any network or API failure.

---

## Licence and attribution

The **client code** is MIT.

**Scene metadata is CC-BY 4.0**, published by ICEYE, Umbra and Capella under
their open data programs. **When you publish derived work, credit the
originating provider.**

```python
print(Catalog().license()["attribution"])
```

Imagery is **not** redistributed by this project. Every download URL points at
the provider's own storage and remains subject to that provider's terms.

This is an independent third-party catalog, not endorsed by or affiliated with
ICEYE, Umbra or Capella. Scene records are normalized from the STAC catalogs
maintained by [Jack-Hayes/commerical-sar-stac](https://github.com/Jack-Hayes/commerical-sar-stac) (MIT).
