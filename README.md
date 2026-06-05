<p align="center">
  <img src="assets/logo.svg" alt="open-sar-triad" width="720">
</p>

<p align="center">
  <a href="https://github.com/pmuguda/open-sar-triad/actions/workflows/deploy.yml"><img alt="Deploy to GitHub Pages" src="https://github.com/pmuguda/open-sar-triad/actions/workflows/deploy.yml/badge.svg"></a>
  <a href="https://github.com/pmuguda/open-sar-triad/actions/workflows/fetch-data.yml"><img alt="Fetch SAR Catalog Data" src="https://github.com/pmuguda/open-sar-triad/actions/workflows/fetch-data.yml/badge.svg"></a>
  <a href="https://pmuguda.github.io/open-sar-triad"><img alt="GitHub Pages" src="https://img.shields.io/badge/GitHub%20Pages-live-00c9ff?logo=githubpages&logoColor=white"></a>
  <img alt="Code license: MIT" src="https://img.shields.io/badge/code-MIT-f0b94f">
  <img alt="Scene data license: CC BY 4.0" src="https://img.shields.io/badge/scene%20data-CC%20BY%204.0-49e68d">
  <img alt="Built-in UI tour" src="https://img.shields.io/badge/UI-built--in%20tour-ff7848">
</p>

An interactive, browser-based map explorer for discovering and filtering open Synthetic Aperture Radar (SAR) scene catalogs from three major commercial satellite operators: **ICEYE**, **Umbra**, and **Capella**. Every scene footprint is rendered as a polygon on a world map. Users can filter by provider, date range, sensor mode, orbit pass direction, radar look direction, and geographic area of interest, then export results in STAC format.

**Live application:** https://pmuguda.github.io/open-sar-triad

**Author:** [Pavan Muguda Sanjeevamurthy](https://pmuguda.github.io/)

**Support this project:** https://ko-fi.com/pavan_muguda

---

## Built-in Tour

The application includes an interactive onboarding tour, so separate demo GIFs are no longer needed in the README. First-time desktop visitors are guided through the provider toggles, collapsible sidebar trays, AOI tools, home view control, timeline, and scene preview panel directly inside the live tool.

Use the `?` button to replay the tour at any time. On mobile, the tour is opt-in so the first screen stays map-first and usable. Shared links skip the tour automatically so recipients land directly on the filtered map view.

---

## Desktop and Mobile

open-sar-triad works on both desktop and mobile browsers. The desktop layout provides the clearest experience for exploring dense SAR footprints, comparing coverage numbers, adjusting the acquisition window, and reviewing scene metadata. Mobile uses a compact map-first layout with a bottom sheet for filters, coverage, export, preview, and sharing; it is useful for quick checks and shared links, but desktop is recommended for the most readable analysis workflow.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Data Sources](#data-sources)
- [Upstream Data Repository](#upstream-data-repository)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [Data Pipeline](#data-pipeline)
- [Filter Reference](#filter-reference)
- [Local Development](#local-development)
- [Deployment](#deployment)
- [Progressive Web App](#progressive-web-app)
- [Security Notes](#security-notes)
- [Automated Data Refresh](#automated-data-refresh)
- [Dependencies](#dependencies)
- [License](#license)

---

## Overview

open-sar-triad is a fully static web application with no backend. All scene data is stored as a single GeoJSON file (`data/scenes.geojson`) and loaded directly in the browser. Filtering, map rendering, and export are handled entirely client-side using vanilla JavaScript and the Leaflet mapping library. The scene catalog is automatically refreshed every Monday via a GitHub Actions workflow.

The three providers represented in this tool each operate public open data programs under CC-BY 4.0 licenses:

| Provider | Brand Color |
|----------|-------------|
| ICEYE | Green (#00FF87) |
| Umbra | Blue (#00C9FF) |
| Capella | Orange (#FF6B35) |

---

## Features

**Map and visualization**
- Leaflet map with a CartoDB dark basemap
- Scene footprints rendered as colored polygons, one per acquisition
- Home button clears AOI/country filters and returns the map to the opening world view
- Clickable scenes open a detail panel with a numbered Preview section, thumbnail, metadata, and a direct download link

**Filtering** — collapsible Filters tray in the sidebar
- Toggle individual providers on or off
- Filter by date range using an interactive slider
- Filter by sensor mode (Stripmap, Spotlight, ScanSAR, etc.)
- Filter by orbit pass direction (ascending / descending)
- Filter by radar look direction (left / right)
- Draw a bounding box or polygon on the map to filter by geographic area
- Click a country to filter scenes that intersect its territory
- Upload a custom GeoJSON file to use as an area of interest

**Statistics** — collapsible Stats tray in the sidebar
- Bar chart showing scene count per provider for the current filter state
- Stacked horizontal bar chart showing sensor mode breakdown per provider

**Onboarding tour**
- Step-by-step UI tour that auto-starts for first-time desktop visitors
- Mobile-specific opt-in tour that targets the compact map controls, bottom sheet, coverage, export, AOI toolbar, and acquisition window
- Skipped automatically when opening a shared link (recipient lands directly on the filtered view)
- Skip or replay via the `?` button in the bottom-right corner

**Export & Share** — collapsible Export tray in the sidebar
- Export all currently visible scenes as a STAC-compliant GeoJSON collection
- Generate a bash download script that saves scene assets into `iceye/`, `umbra/`, and `capella/` subdirectories (with `--dry-run` support)
- Copy a shareable link that encodes the full filter state and map view into the URL hash — recipients open the exact same view

**Progressive Web App**
- Installable from Chrome/Edge without an app store or approval process
- Opens in a standalone app window with its own dock/taskbar entry
- Service worker caches the app shell, catalog, icons, and core map dependencies for offline repeat visits

**Automation**
- Scene catalog refreshed every Monday at 03:00 UTC via GitHub Actions
- Deployed automatically to GitHub Pages on every push to `main`

---

## Data Sources

Scene metadata originates from the public STAC catalogs maintained by each provider.

| Provider | Open Data Program | STAC Catalog Endpoint | License |
|----------|-------------------|-----------------------|---------|
| ICEYE | https://www.iceye.com/open-data-initiative | `iceye-open-data-catalog.s3-us-west-2.amazonaws.com/catalog.json` | CC-BY 4.0 |
| Umbra | https://umbra.space/open-data/ | `s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json` | CC-BY 4.0 |
| Capella | https://www.capellaspace.com/community/capella-open-data-program/ | `capella-open-data.s3.us-west-2.amazonaws.com/stac/catalog.json` | CC-BY 4.0 |

---

## Upstream Data Repository

The data pipeline in this project does **not** crawl the STAC catalogs directly. Instead, it consumes pre-built GeoParquet files maintained by the repository:

**https://github.com/Jack-Hayes/commerical-sar-stac**

That repository processes the three providers' STAC catalogs on a regular cadence and publishes normalized GeoParquet files under the path `parquets/viz/`. This project's fetch script downloads those files directly and converts them to GeoJSON.

The specific Parquet files consumed are:

| Provider | File(s) |
|----------|---------|
| ICEYE | `parquets/viz/iceye/iceye.parquet` |
| Umbra | `parquets/viz/umbra/umbra.parquet` |
| Capella | `parquets/viz/capella/capella_GEC.parquet` |
| | `parquets/viz/capella/capella_GEO.parquet` |
| | `parquets/viz/capella/capella_SLC.parquet` |
| | `parquets/viz/capella/capella_SICD.parquet` |
| | `parquets/viz/capella/capella_SIDD.parquet` |
| | `parquets/viz/capella/capella_CSI.parquet` |
| | `parquets/viz/capella/capella_CPHD.parquet` |

All scene data credit goes to Jack-Hayes/commerical-sar-stac and the original providers.

---

## Project Structure

```
open-sar-triad/
├── .github/
│   └── workflows/
│       ├── deploy.yml          # GitHub Pages deployment workflow
│       └── fetch-data.yml      # Weekly data refresh workflow
├── assets/
│   ├── logo.svg                # Repository logo used by the README
│   ├── pwa-icon.svg            # Source PWA install icon
│   ├── pwa-icon-192.png        # 192px install icon
│   └── pwa-icon-512.png        # 512px install icon
├── css/
│   ├── style.css               # All application styles
│   └── tour.css                # Onboarding tour overlay and tooltip styles
├── data/
│   └── scenes.geojson          # Generated scene catalog (~17 MB, git-tracked)
├── js/
│   ├── app.js                  # Main application logic
│   └── tour.js                 # Step-by-step onboarding tour
├── scripts/
│   └── fetch_catalog.py        # Data pipeline: downloads Parquet, outputs GeoJSON
├── index.html                  # Application shell and UI layout
├── manifest.json               # PWA install metadata
├── sw.js                       # Service worker for offline caching
├── requirements.txt            # Python dependencies for the fetch script
└── .gitignore
```

---

## Architecture

**Frontend**

The application is a single HTML page with no build step and no framework. `index.html` defines the UI layout; `css/style.css` applies a dark theme; `js/app.js` contains all application logic.

On page load, `app.js` fetches `data/scenes.geojson` and stores the full feature array in memory. All filtering operates on this in-memory array — no server requests are made after the initial load. Leaflet GeoJSON layers are added and removed from the map on every filter change.

Key global state managed by `app.js`:

| Variable | Description |
|----------|-------------|
| `allFeatures` | Full array of GeoJSON features loaded at startup |
| `activeLayers` | Leaflet layer objects currently rendered on the map |
| `aoiBbox` | `[west, south, east, north]` from a drawn area of interest |
| `countryLayer` | TopoJSON-derived Leaflet layer for country boundaries |
| `selectedCountry` | Currently selected country `{ layer, bbox, name }` |
| `MONTHS`, `tlFrom`, `tlTo` | Timeline month list and active acquisition-window indices |
| `providerActive` | `{ iceye, umbra, capella }` boolean flags |
| `orbitFilter` | Active orbit filter: `''` (all), `'ascending'`, or `'descending'` |
| `lookFilter` | Active look-direction filter: `''` (all), `'left'`, or `'right'` |

Key functions in `app.js`:

| Function | Purpose |
|----------|---------|
| `getFilters()` | Reads all UI controls and returns the current filter state |
| `render()` | Applies filters to `allFeatures`, rebuilds map layers, updates stats |
| `initCollapsibleTrays()` | Wires the numbered sidebar trays so Acquisition, Coverage, and Export can collapse independently |
| `centroid()` | Computes the centroid of a polygon for bbox intersection checks |
| `updateCoverage()` | Updates provider scene-count numbers and bars |
| `updateModes()` | Renders the stacked sensor-mode breakdown |
| `showDetail()` | Populates the right-side `04 Preview` detail panel with scene metadata |
| `bboxFromGeometry()` | Extracts a bounding box from any GeoJSON geometry |
| `unwrapAntimeridian()` | Corrects polygon coordinates that cross the ±180° meridian |
| `loadCountries()` | Fetches world-atlas TopoJSON and creates interactive country polygons |
| `startDraw()` | Activates Leaflet-Draw rectangle or polygon drawing mode |
| `buildTimelineHist()` | Builds the acquisition-window histogram from the loaded scene dates |
| `setTimelineRange()` | Updates the active acquisition-window range |
| `populateModes()` | Populates the sensor mode dropdown from the loaded feature set |

**Data file**

`data/scenes.geojson` is a standard GeoJSON FeatureCollection. Each feature represents one SAR scene acquisition. The top-level object includes:

```json
{
  "type": "FeatureCollection",
  "generated_at": "<ISO 8601 timestamp>",
  "source": "https://github.com/Jack-Hayes/commerical-sar-stac",
  "features": [...]
}
```

Each feature carries the following properties:

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | Provider-assigned scene identifier |
| `provider` | string | `iceye`, `umbra`, or `capella` |
| `provider_label` | string | Display name for the provider |
| `color` | string | Hex color used on the map |
| `date` | string | Acquisition date in `YYYY-MM-DD` format |
| `year` | integer | Acquisition year |
| `sensor_mode` | string | Sensor mode in lowercase (e.g. `stripmap`, `spotlight`) |
| `resolution` | number | Ground range resolution in metres, if available |
| `polarization` | string | Polarization channels (e.g. `VV`, `HH, HV`) |
| `incidence_angle` | number | Look angle in degrees, if available |
| `off_nadir` | number | Off-nadir angle in degrees, if available |
| `thumbnail` | string | URL to a preview image |
| `download` | string | URL to the scene data asset |
| `provider_url` | string | URL to the provider's open data program page |
| `collection` | string | Product type or collection name |
| `orbit_state` | string | Satellite pass direction: `ascending`, `descending`, or `null` |
| `look_dir` | string | Radar look direction: `left` or `right` |

---

## Data Pipeline

The script `scripts/fetch_catalog.py` is responsible for building `data/scenes.geojson`. It requires Python 3.11 or later.

**Steps:**

1. For each provider, download the corresponding Parquet file(s) from the `Jack-Hayes/commerical-sar-stac` repository on GitHub.
2. Read each Parquet file into a Pandas DataFrame using PyArrow.
3. Deduplicate rows by `id` within each provider.
4. Convert each row to a GeoJSON Feature using `normalize_row()`:
   - Parse geometry from the `geometry_geojson` column.
   - Extract acquisition date from `datetime` or `start_datetime`.
   - Normalize sensor mode to lowercase.
   - Parse asset URLs (thumbnail and data download) from the nested `assets` field.
   - Skip rows whose `id` contains `footprint` or `collection` (these are catalog metadata entries, not real acquisitions).
5. Merge all providers into a single list of features.
6. Write the merged FeatureCollection to `data/scenes.geojson` as compact JSON.

**Fallback behavior:** If a provider's Parquet files cannot be fetched (network failure, HTTP error), the script retains that provider's data from the existing `data/scenes.geojson` rather than writing an empty result.

**Python dependencies:**

```
pyarrow>=14.0
pandas>=2.0
```

---

## Filter Reference

| Filter | UI Control | Behavior |
|--------|-----------|---------|
| Provider | Pill toggles (map, top-left) | Shows or hides all scenes from a given provider |
| Date range | Dual-handle slider (bottom bar) | Inclusive filter on the `date` property |
| Sensor mode | Dropdown select in Filters tray | Exact match on the normalized `sensor_mode` value; "All modes" disables this filter |
| Orbit state | Three-way pill in Filters tray → Geometry | Filters to `ascending` or `descending` passes; "All" disables. Scenes with no recorded orbit state are excluded when a direction is selected |
| Look direction | Three-way pill in Filters tray → Geometry | Filters to `left` or `right` radar look; "All" disables |
| Bounding box | Draw rectangle or polygon on the AOI toolbar | Filters to scenes whose centroid falls within the drawn area |
| Country | Country picker on the AOI toolbar, then click a country | Filters to scenes whose centroid falls within the actual country polygon (point-in-polygon, not just the bounding box). Globe button stays amber when a country filter is active; click it again to switch countries |
| GeoJSON upload | File input on the AOI toolbar | Extracts the bounding box of the uploaded geometry and uses it as an area-of-interest filter |

All filters are combined with AND logic: a scene must pass every active filter to appear on the map and in the statistics.

### Shareable URL

Every filter change updates `window.location.hash` automatically via `history.replaceState`. The hash encodes: hidden providers, date range, sensor mode, orbit, look direction, drawn bbox or selected country name, and map centre + zoom. Example:

```
https://pmuguda.github.io/open-sar-triad#from=2024-01-01&to=2025-06-01&mode=spotlight&country=India&lat=20.59&lng=78.96&z=5
```

Clicking **Copy Share Link** in the Export panel copies the current URL to the clipboard. Opening a shared URL restores all filters immediately: bbox AOIs redraw as visible rectangles, selected countries are highlighted after country boundaries load, and date range is applied after the timeline initializes. The onboarding tour is suppressed when a shared URL is detected so the recipient lands directly on the filtered view.

---

## Local Development

**Requirements:** Python 3.11+, a browser.

```bash
# Clone the repository
git clone https://github.com/pmuguda/open-sar-triad.git
cd open-sar-triad

# (Optional) Refresh the scene catalog from upstream
pip install -r requirements.txt
python3 scripts/fetch_catalog.py

# Serve the application locally
python3 -m http.server 8767
# Then open http://localhost:8767 in a browser
```

The `data/scenes.geojson` file is already committed to the repository, so you do not need to run the fetch script to get the application working locally.

---

## Deployment

The application is hosted on GitHub Pages as a static site. There is no build step.

The workflow at `.github/workflows/deploy.yml` triggers on every push to the `main` branch and on manual dispatch. It uploads the entire repository as a Pages artifact and deploys it. No compilation, bundling, or server configuration is required.

---

## Progressive Web App

open-sar-triad includes a lightweight PWA setup:

- `manifest.json` defines the installable app name, standalone display mode, theme colors, and icons.
- `sw.js` precaches the static app shell, scene catalog, PWA icons, and core CDN dependencies.
- `index.html` exposes the manifest to browsers; `js/app.js` registers the service worker.

On Chrome or Edge, users can install the live site directly from the browser address bar. The installed app opens in a standalone window without browser chrome and appears in the operating system dock or taskbar. After the first successful load, repeat visits can open the cached app shell and scene catalog even when offline. Third-party map tiles, provider thumbnails, and scene asset downloads are not cached by the service worker.

No app store, signing step, review, or approval process is required.

---

## Security Notes

open-sar-triad is a static, client-side application with no custom backend and no user accounts. The main security controls are:

- A restrictive Content Security Policy in `index.html`: scripts are limited to this site plus the pinned CDN dependencies, inline scripts are disabled, framing is blocked, and form submissions/base URL rewriting are disabled.
- CDN JavaScript and CSS dependencies are version-pinned, and Leaflet/Leaflet-Draw assets include Subresource Integrity attributes.
- Catalog text rendered into popups, preview panels, mode breakdowns, and exported HTML is escaped before insertion.
- Catalog asset links are accepted only when they use `http:` or `https:`; `javascript:`, `data:`, and other schemes are rejected by the fetch pipeline and checked again in the browser.
- Generated download scripts quote URLs and destination paths defensively before writing shell commands.
- The service worker precaches only the app shell, scene catalog, icons, and known CDN dependencies. It does not cache arbitrary third-party provider thumbnails or scene downloads.

The weekly data workflow writes only `data/scenes.geojson`, and the GitHub Pages deployment workflow uses the official Pages actions with least-privilege repository permissions.

---

## Automated Data Refresh

The workflow at `.github/workflows/fetch-data.yml` runs every Monday at 03:00 UTC (cron schedule `0 3 * * 1`) and can also be triggered manually from the Actions tab.

Steps performed by the workflow:

1. Check out the repository.
2. Set up Python 3.11.
3. Install `requirements.txt`.
4. Run `scripts/fetch_catalog.py`.
5. If `data/scenes.geojson` has changed, commit the file with the message `chore: update SAR scene data [YYYY-MM-DD]` and push to `main`.
6. The deploy workflow then picks up the push and publishes the updated catalog to GitHub Pages.

---

## Dependencies

**JavaScript (loaded from CDN)**

| Library | Version | Purpose | License |
|---------|---------|---------|---------|
| Leaflet | 1.9.4 | Interactive map | BSD-2-Clause |
| Leaflet-Draw | 1.0.4 | Bounding box and polygon drawing tools | MIT |
| topojson-client | 3.1.0 | Decodes TopoJSON country boundaries | BSD-3-Clause |

**External services**

| Service | Purpose |
|---------|---------|
| CartoDB (via OpenStreetMap) | Basemap tiles |
| world-atlas (jsDelivr CDN) | Country boundary TopoJSON |
| images.weserv.nl | Image proxy for ICEYE thumbnails (CORS workaround) |

**Python (data pipeline only)**

| Package | Version | Purpose |
|---------|---------|---------|
| pyarrow | >=14.0 | Read Parquet files |
| pandas | >=2.0 | DataFrame operations and deduplication |

---

## License

Code in this repository is released under the **MIT License**.

Scene data is licensed **CC-BY 4.0** per the open data program terms of each provider (ICEYE, Umbra, and Capella). Attribution must be given to the original provider when redistributing or publishing derived work.

The upstream Parquet files are sourced from [Jack-Hayes/commerical-sar-stac](https://github.com/Jack-Hayes/commerical-sar-stac).

## Acknowledgements
This project was developed with AI assistance (Claude by Anthropic) for code 
generation and debugging. Architecture decisions, refinements, ideations and
ongoing maintenance are made by me.
