# Changelog

All notable changes to open-sar-triad are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [2.1.0] — 2026-07-07

Feature release building on v2.0.0. All changes are backward-compatible.

### Added
- **Fullscreen preview lightbox** — every scene preview has a fullscreen button that
  opens a centered, fit-to-screen viewer with zoom (buttons, wheel, pinch),
  drag-to-pan, and reset.
- **On-the-fly Umbra previews** — Umbra ships no thumbnails, but its imagery is
  Cloud-Optimized GeoTIFFs on a CORS/range-enabled bucket. The app now fetches only
  the smallest overview (a few hundred KB) and renders a stretched grayscale preview
  client-side — no backend, no storage. ~71% of Umbra scenes now show a preview,
  marked "RENDERED FROM COG".
- **Recent Activity feed** — a new sidebar section surfaces newly-ingested scenes.
  The weekly pipeline stamps a `first_seen` date on new arrivals and groups them into
  "This week" / "Earlier this month" (scrollable, newest first). Until ingestion
  history builds up, it falls back to the last 30 days of acquisitions.
- **"N scenes here" overlap picker** — clicking a spot where several footprints
  overlap opens a disambiguation list (provider · date · mode) instead of selecting
  only the topmost scene.
- **Capella data-format picker** — a Data Format selector in the detail panel lets
  you choose which format (GEC, GEO, SLC, SICD, SIDD, CPHD, CSI) to download.

### Changed
- Capella acquisitions published in multiple formats are now collapsed into one scene
  per acquisition (carrying a `{format: download_url}` map) instead of appearing as
  6–7 duplicate footprints. Scene totals are now honest distinct-acquisition counts:
  Capella 9,931 → 2,460, overall 22,102 → 14,631.

### Fixed
- Lightbox centering and pan clamping (transform-origin / absolute base-position bugs).
- Pan gesture no longer closes the fullscreen view.
- Service-worker cache busting so returning visitors reliably receive updates.

## [2.0.0] — 2026-06-05

Major redesign into a full discovery console.

### Added
- Complete console-style UI redesign: map-first workspace, sidebar trays, dark/paper
  themes, provider toggles, compact acquisition controls.
- Scene footprints rendered as acquisition polygons with zoom-dependent scaling
  (true size when zoomed in; wide/sliding-spotlight modes handled specially), drawn on
  Canvas with cached scaled geometries.
- Responsive mobile layout with mobile-specific tour coverage.
- Installable PWA support (`manifest.json`, icons, service worker).
- Shareable URL state (filters, date range, map view, bbox/country AOIs).
- Repository branding, updated logo, footer links.
- `CITATION.cff` and DOI citation metadata (Zenodo `10.5281/zenodo.20562327`).

### Changed
- Hardened client-side security: stricter CSP, safer URL handling, safer generated
  download scripts, narrower service-worker caching.

## [1.0.0] — 2026-06-05

First public release.

### Added
- Interactive Leaflet map with scene footprints for ICEYE, Umbra, and Capella.
- Filter by sensor mode, orbit direction, and look direction.
- Draw bbox/polygon AOI, upload GeoJSON, click a country to filter.
- Full-catalog date range slider.
- Collapsible Filters and Stats trays with live bar / mode-breakdown charts.
- Export visible scenes as a STAC-compliant GeoJSON collection.
- Onboarding tour and weekly automated data refresh via GitHub Actions.
- Security hardening: CSP, X-Content-Type-Options, SRI hashes, XSS protections,
  URL-scheme validation.

[2.1.0]: https://github.com/pmuguda/open-sar-triad/releases/tag/v2.1.0
[2.0.0]: https://github.com/pmuguda/open-sar-triad/releases/tag/v2.0.0
[1.0.0]: https://github.com/pmuguda/open-sar-triad/releases/tag/v1.0.0
