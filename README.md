# open-sar-triad

An interactive map explorer for open SAR datasets from **ICEYE**, **Umbra**, and **Capella** — the three leading commercial SAR providers with public open data programs.

🌐 **Live**: [pmuguda.github.io/open-sar-triad](https://pmuguda.github.io/open-sar-triad)

---

## Features

- 🗺️ Leaflet map with footprint polygons for every open scene
- 🔍 Filter by provider, date range, sensor mode, and drawn bounding box
- 📍 Location search (powered by Nominatim)
- 🖼️ Scene detail panel with thumbnail, metadata, download link, and provider page
- 🔄 Weekly auto-refresh via GitHub Actions (every Monday 03:00 UTC)
- ⚡ Fully static — hosted on GitHub Pages, no backend

## Data sources

| Provider | Open Data Program | STAC Catalog |
|----------|-------------------|--------------|
| [ICEYE](https://www.iceye.com/open-data-initiative) | CC-BY 4.0 | `iceye-open-data-catalog.s3-us-west-2.amazonaws.com/catalog.json` |
| [Umbra](https://umbra.space/open-data/) | CC-BY 4.0 | `s3.us-west-2.amazonaws.com/umbra-open-data-catalog/stac/catalog.json` |
| [Capella](https://www.capellaspace.com/community/capella-open-data-program/) | CC-BY 4.0 | `capella-open-data.s3.us-west-2.amazonaws.com/stac/catalog.json` |

## Local development

```bash
# Fetch latest scene data
python3 scripts/fetch_catalog.py

# Serve locally
python3 -m http.server 8767
# open http://localhost:8767
```

## Refresh cadence

The `fetch-data` GitHub Action runs every Monday and commits an updated `data/scenes.geojson`. You can also trigger it manually from the Actions tab.

## License

Code: MIT. Scene data: CC-BY 4.0 per each provider's open data license.
