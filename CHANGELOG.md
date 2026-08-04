# Changelog

All notable changes to open-sar-triad are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Product families** replace the raw format row, and the control moved out of the
  Export tray to sit under the download list where the scenes are. Pick
  `Detected imagery`, `Complex (SLC-type)`, `Phase history` or `Visual extras`
  and every scene contributes its own provider's equivalent product. This fixes
  a real hole: asking for `SLC` by name returned **zero** Umbra scenes, even
  though all 11,892 carry complex data as `SICD`. Resolution is preferred-first
  with fallback — Detected prefers `GEO` (terrain corrected), falling back to
  `GRD` for ICEYE and `GEC` for Umbra; Complex prefers `SLC`, falling back to
  `SICD` for Umbra — so each scene yields one file per family rather than
  near-duplicates.
- Per-scene format badge on every download-list row, showing the concrete
  product that scene will contribute. Clicking it pins a different format for
  that one scene, from only what that scene actually publishes; `Use default`
  clears the pin. One badge per row, so it reads the same at 3 rows or 300.
- `Exact formats` disclosure keeps the original nine-format row for when a
  specific product is required. Switching between the two modes clears the
  other's selection, so they can never hold conflicting state.
- Scene selection, via a **Download list** tray. Hand-pick which scenes the
  exports act on instead of always taking the whole filter: `Click map to add`
  turns map clicks into add/remove, `Add all filtered (N)` takes the current
  filter in one click, rows have a per-row remove, and `Clear list` empties it.
  The scene detail panel gains an add/remove button, and where footprints
  overlap the "N scenes here" picker becomes a checklist with an `Add all N to
  the download list` shortcut. Picked footprints render with a heavier dashed
  outline. Picks survive filter changes. Both the download script and the STAC
  export use the list when it has anything in it, and fall back to all filtered
  scenes when it doesn't.
- `Show only these on map`. Hides every other footprint so the map shows just
  your list, including picks the current filter excludes. Coverage numbers and
  stats keep describing the filter, and a banner says so while it is on.
- Hover feedback when picking scenes. Hovering a row in the download list or in
  the "N scenes here" overlap picker lights up that footprint and lifts it above
  its neighbours, so choosing between stacked, near-identical scene IDs no longer
  relies on guesswork.
- Data-format selection for the download script. It can fetch a chosen product —
  `GRD`, `GEC`, `GEO`, `SLC`, `CSI`, `SICD`, `SIDD`, `CPHD` or `VID` — for every
  scene, instead of only each scene's primary asset. Several can be selected at
  once; chips report how many scenes publish each and disable when none do.
  Scenes publishing none of them are skipped and counted in the script header and
  the export hint. With nothing selected the script is unchanged from before.
- Metadata sidecars in the download script. Every data file is now fetched with
  the metadata the provider publishes beside it, into the same directory: `.xml`
  for ICEYE SICD/SIDD, `.json` for ICEYE's other formats and for Capella
  (`_extended.json`), and one `stac.v2.json` per Umbra acquisition — downloaded
  once even when several Umbra formats are selected. Assets that publish no
  sidecar (ICEYE CPHD, Capella SICD/SIDD/CPHD) are skipped and counted in the
  script header.

### Changed
- The download-list actions are now an icon dock **on the map**, sitting directly
  beneath the scene-preview controls, instead of four full-width labelled buttons
  in the sidebar tray. Counts moved into the tooltips, with a compact
  `N IN LIST` readout beside the icons. The tray keeps the scene list and the
  format control.

- Scene/provider actions now open Radiant Earth STAC Browser sources instead of
  retired or generic provider pages. ICEYE and Umbra links resolve to scene-level
  STAC items when their IDs/assets allow it; Capella links to the live Capella STAC
  catalog root.

### Removed
- The `Add to selection` button in the scene detail panel. Pick mode on the map,
  the overlap checklist and the download-list tray already cover it.

### Fixed
- The download script could silently overwrite files. Umbra republishes some
  acquisitions under a second prefix (`sar-data/tasks/<campaign>/…` as well as
  `sar-data/task-data/…`), so two distinct scenes can share an asset basename —
  590 destinations collided in a full default script, and `curl -o` wrote them to
  the same path. Clashing files now get their own scene-id folder; everything
  else keeps the flat layout.
- The country picker and the scene-selection mode now switch each other off
  rather than both claiming map clicks.

## [2.1.2] — 2026-07-11

Patch release focused on onboarding clarity.

### Changed
- Refreshed the desktop and mobile onboarding tours for the current interface:
  MAP/SAT basemap switching, `03 Recent`, georeferenced preview drapes,
  `05 Scene` metadata, and Capella's cleaned format selector/provider action
  are now covered with shorter step descriptions.
- Bumped the tour cache keys so returning visitors receive the updated tour.

## [2.1.1] — 2026-07-11

Patch release focused on scene preview georeferencing and detail-panel metadata.

### Added
- **Georeferenced scene preview on the map** — clicking a scene now drapes its SAR
  preview image onto the basemap, aligned to provider-specific scene geometry
  and clipped to the exact footprint polygon,
  then fits the map to it, EO-viewer style. On-map controls: an opacity slider (blend
  against the basemap), a hide/show toggle, a fullscreen button (reuses the zoom/pan
  lightbox), and a close button; Home/reset also clears it. While a scene is draped,
  all other footprints are hidden for a clean view. The side panel now shows the
  scene's full metadata instead of a thumbnail. Umbra and Capella previews are
  rendered from COG overviews on the fly; ICEYE browse previews use the provider's
  KML corner order for correct thumbnail placement.
- **Satellite basemap** — a MAP / SAT toggle switches the basemap to Esri World
  Imagery, so a draped SAR preview can be compared against optical ground truth to
  confirm georeferencing.

### Fixed
- Capella detail-panel metadata no longer repeats the same format list under both
  `Collection` and `Formats`; it now shows a single `Available formats` row.
- The `View on Capella` action now opens a live Capella page instead of the retired
  Capella open-data program URL.
- Capella provider URLs generated by the catalog fetch script now use the current
  Capella homepage.

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

[2.1.2]: https://github.com/pmuguda/open-sar-triad/releases/tag/v2.1.2
[2.1.1]: https://github.com/pmuguda/open-sar-triad/releases/tag/v2.1.1
[2.1.0]: https://github.com/pmuguda/open-sar-triad/releases/tag/v2.1.0
[2.0.0]: https://github.com/pmuguda/open-sar-triad/releases/tag/v2.0.0
[1.0.0]: https://github.com/pmuguda/open-sar-triad/releases/tag/v1.0.0
