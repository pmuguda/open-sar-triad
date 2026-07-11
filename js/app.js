/* open-sar-triad — app.js */

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(err => {
      console.warn('Service worker registration failed', err);
    });
  });
}

const PROVIDER_COLORS = { iceye: '#00FF87', umbra: '#00C9FF', capella: '#FF6B35' };
const PROVIDER_LABELS = { iceye: 'ICEYE', umbra: 'Umbra', capella: 'Capella' };

let allFeatures     = [];
let activeLayers    = {};
let aoiBbox         = null;
let countryLayer    = null;
let countriesLoaded = false;
let countryMode     = false;
let hoveredCountry  = null;
let selectedCountry = null;   // { layer, bbox, name, geometry }
const providerActive = { iceye: true, umbra: true, capella: true };
let orbitFilter = '';   // '' | 'ascending' | 'descending'
let lookFilter  = '';   // '' | 'left' | 'right'
let dataLoaded  = false;
let pendingCountryRestore = null;

// ── Custom timeline scrubber state ─────────────────────────
let MONTHS = [];
let tlFrom = 0, tlTo = 0;

// ── Map ────────────────────────────────────────────────────
const INITIAL_CENTER = [20, 0];
const INITIAL_ZOOM = 2;
const map = L.map('map', { center: INITIAL_CENTER, zoom: INITIAL_ZOOM, zoomControl: false, preferCanvas: true });
const footprintRenderer = L.canvas({ padding: 0.5 });
// Draped scene preview sits just above the footprint vectors (zIndex 400) so its
// imagery isn't tinted by the polygon fill, but below the country/drawn panes so
// the dashed selection outline (drawnPane) still reads on top.
map.createPane('scenePane');
map.getPane('scenePane').style.zIndex = 405;
map.getPane('scenePane').style.pointerEvents = 'none';

map.createPane('countryPane');
map.getPane('countryPane').style.zIndex = 410;
map.getPane('countryPane').style.pointerEvents = 'none';

map.createPane('drawnPane');
map.getPane('drawnPane').style.zIndex = 420;   // above scene footprints
map.getPane('drawnPane').style.pointerEvents = 'none'; // clicks pass through
L.control.zoom({ position: 'bottomleft' }).addTo(map);
L.control.scale({ position: 'bottomleft', imperial: false, maxWidth: 140 }).addTo(map);

const HomeControl = L.Control.extend({
  options: { position: 'topleft' },
  onAdd() {
    const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control leaflet-control-home');
    container.id = 'home-control';
    const link = L.DomUtil.create('a', '', container);
    link.href = '#';
    link.title = 'Reset map view and AOI';
    link.setAttribute('role', 'button');
    link.setAttribute('aria-label', 'Reset map view and AOI');
    link.innerHTML = '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 7.2 8 3l5 4.2V13H9.5V9.4h-3V13H3V7.2Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    L.DomEvent.disableClickPropagation(container);
    L.DomEvent.on(link, 'click', e => {
      L.DomEvent.preventDefault(e);
      clearAll();
      map.setView(INITIAL_CENTER, INITIAL_ZOOM);
    });
    return container;
  }
});
new HomeControl().addTo(map);

const TILE = {
  dark:  'https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png',
  paper: 'https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png',
  labels: {
    dark:  'https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png',
    paper: 'https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png',
  }
};
const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';
let baseLayer  = L.tileLayer(TILE.dark,  { subdomains: 'abcd', attribution: ATTR, detectRetina: true }).addTo(map);
let labelLayer = L.tileLayer(TILE.labels.dark, { subdomains: 'abcd', detectRetina: true, opacity: 0.55, pane: 'overlayPane' }).addTo(map);

// Esri World Imagery satellite basemap — lets you compare draped SAR against
// optical ground truth to confirm georeferencing.
const ESRI_IMAGERY = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const ESRI_ATTR = 'Imagery &copy; Esri, Maxar, Earthstar Geographics';
let satLayer = null, basemapMode = 'map';
function setBasemap(mode) {
  basemapMode = mode;
  if (mode === 'sat') {
    if (!satLayer) satLayer = L.tileLayer(ESRI_IMAGERY, { attribution: ESRI_ATTR, maxZoom: 19, detectRetina: true });
    satLayer.addTo(map);
    if (map.hasLayer(baseLayer))  map.removeLayer(baseLayer);
    if (map.hasLayer(labelLayer)) map.removeLayer(labelLayer);
  } else {
    if (satLayer && map.hasLayer(satLayer)) map.removeLayer(satLayer);
    if (!map.hasLayer(baseLayer))  baseLayer.addTo(map);
    if (!map.hasLayer(labelLayer)) labelLayer.addTo(map);
  }
  document.querySelectorAll('[data-basemap-btn]').forEach(b =>
    b.setAttribute('aria-pressed', b.dataset.basemapBtn === mode));
}

const BasemapControl = L.Control.extend({
  options: { position: 'topleft' },
  onAdd() {
    const c = L.DomUtil.create('div', 'leaflet-bar leaflet-control basemap-toggle');
    c.innerHTML = `<button data-basemap-btn="map" aria-pressed="true" title="Vector basemap">MAP</button>` +
                  `<button data-basemap-btn="sat" aria-pressed="false" title="Satellite imagery">SAT</button>`;
    L.DomEvent.disableClickPropagation(c);
    c.querySelectorAll('button').forEach(b =>
      L.DomEvent.on(b, 'click', () => setBasemap(b.dataset.basemapBtn)));
    return c;
  }
});
new BasemapControl().addTo(map);

// ── Graticule ──────────────────────────────────────────────
const gridLayer = L.layerGroup().addTo(map);
function drawGraticule() {
  gridLayer.clearLayers();
  const col = getComputedStyle(document.documentElement).getPropertyValue('--grid').trim() || 'rgba(150,160,180,.15)';
  for (let lng = -180; lng <= 180; lng += 20)
    L.polyline([[-85,lng],[85,lng]], { color: col, weight: 1, interactive: false }).addTo(gridLayer);
  for (let lat = -80; lat <= 80; lat += 20)
    L.polyline([[lat,-180],[lat,180]], { color: col, weight: 1, interactive: false }).addTo(gridLayer);
}
drawGraticule();

// ── Coordinate readout ─────────────────────────────────────
function updateCoords() {
  const c = map.getCenter();
  const rdLat = document.getElementById('rdLat');
  const rdLng = document.getElementById('rdLng');
  const rdZ   = document.getElementById('rdZ');
  if (rdLat) rdLat.textContent = c.lat.toFixed(4);
  if (rdLng) rdLng.textContent = c.lng.toFixed(4);
  if (rdZ)   rdZ.textContent   = map.getZoom().toFixed(map.getZoom() % 1 ? 1 : 0);
}
map.on('move zoom', updateCoords); updateCoords();

// ── Collapsible sidebar trays ──────────────────────────────
function setTrayCollapsed(mod, collapsed) {
  const btn = mod.querySelector('.tray-toggle');
  const body = btn ? document.getElementById(btn.getAttribute('aria-controls')) : null;
  if (!btn || !body) return;
  mod.classList.toggle('is-collapsed', collapsed);
  btn.setAttribute('aria-expanded', String(!collapsed));
  body.hidden = collapsed;
}

function expandTray(selector) {
  const mod = document.querySelector(selector);
  if (mod) setTrayCollapsed(mod, false);
}
window.expandTray = expandTray;

function initCollapsibleTrays() {
  document.querySelectorAll('.mod .tray-toggle').forEach(btn => {
    const mod = btn.closest('.mod');
    btn.addEventListener('click', () => setTrayCollapsed(mod, !mod.classList.contains('is-collapsed')));
  });
}

initCollapsibleTrays();

// ── Drawn items (Leaflet-Draw) ─────────────────────────────
const drawnItems = new L.FeatureGroup().addTo(map);
const drawControl = new L.Control.Draw({
  draw: {
    rectangle: { shapeOptions: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05, pane: 'drawnPane' } },
    polygon:   { shapeOptions: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05, pane: 'drawnPane' } },
    polyline: false, circle: false, marker: false, circlemarker: false,
  },
  edit: { featureGroup: drawnItems, remove: false, edit: false },
});

// ── Antimeridian helpers ───────────────────────────────────
function unwrapAntimeridian(geom) {
  if (!geom) return;
  const fixRing = ring => {
    for (let i = 1; i < ring.length; i++) {
      const d = ring[i][0] - ring[i-1][0];
      if (d > 180)      ring[i][0] -= 360;
      else if (d < -180) ring[i][0] += 360;
    }
  };
  if (geom.type === 'Polygon')           geom.coordinates.forEach(fixRing);
  else if (geom.type === 'MultiPolygon') geom.coordinates.forEach(p => p.forEach(fixRing));
}

function flatCoords(geom) {
  if (!geom) return [];
  if (geom.type === 'Polygon')           return geom.coordinates.flat();
  if (geom.type === 'MultiPolygon')      return geom.coordinates.flat(2);
  return [];
}

function bboxFromGeometry(geom) {
  const coords = flatCoords(geom);
  if (!coords.length) return null;
  const lngs = coords.map(c => c[0]), lats = coords.map(c => c[1]);
  let minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  if (maxLng - minLng > 180) {
    const shifted = lngs.map(l => l < 0 ? l + 360 : l);
    const sMin = Math.min(...shifted), sMax = Math.max(...shifted);
    if (sMax - sMin < maxLng - minLng) { minLng = sMin; maxLng = sMax; }
  }
  return [minLng, Math.min(...lats), maxLng, Math.max(...lats)];
}

// ── Filters ────────────────────────────────────────────────
function getFilters() {
  let dateFrom = null, dateTo = null;
  if (MONTHS.length) {
    dateFrom = MONTHS[tlFrom] + '-01';
    dateTo   = MONTHS[tlTo]   + '-31';
  }
  const bbox            = (selectedCountry && selectedCountry.bbox) || aoiBbox;
  const countryGeometry = selectedCountry ? selectedCountry.geometry : null;
  return {
    iceye: providerActive.iceye, umbra: providerActive.umbra, capella: providerActive.capella,
    dateFrom, dateTo,
    mode:  document.getElementById('modeSel') ? document.getElementById('modeSel').value : '',
    bbox, countryGeometry,
    orbit: orbitFilter,
    look:  lookFilter,
  };
}

// ── Visible features ───────────────────────────────────────
function getVisibleFeatures() {
  const f = getFilters();
  return allFeatures.filter(feat => {
    const p = feat.properties;
    if (!f[p.provider]) return false;
    if (f.dateFrom && p.date && p.date < f.dateFrom) return false;
    if (f.dateTo   && p.date && p.date > f.dateTo)   return false;
    if (f.mode  && p.sensor_mode && p.sensor_mode.toLowerCase() !== f.mode) return false;
    if (f.orbit && p.orbit_state !== f.orbit) return false;
    if (f.look  && p.look_dir   !== f.look)  return false;
    if (f.bbox) {
      const c = centroid(feat.geometry);
      if (!c) return false;
      if (f.countryGeometry) {
        if (!pointInPolygon(c, f.countryGeometry)) return false;
      } else {
        const [w,s,e,n] = f.bbox;
        if (c[0]<w || c[0]>e || c[1]<s || c[1]>n) return false;
      }
    }
    return true;
  });
}

// ── Geometry cache (built once at load, avoids repeated JSON.parse) ──
const geomCache    = {};   // id → unwrapped geometry copy
const centroidCache = {};  // id → [lng, lat]
const scaledGeomCache = new Map();

function buildGeomCache(features) {
  scaledGeomCache.clear();
  features.forEach(feat => {
    const id  = feat.properties.id;
    const g   = JSON.parse(JSON.stringify(feat.geometry));
    unwrapAntimeridian(g);
    geomCache[id]    = g;
    centroidCache[id] = centroid(g);
  });
}

// ── Zoom-dependent scale factor ────────────────────────────
// At low zoom scenes are inflated so they're visible; above zoom 9 they
// render at true geographic size.
const UNSCALED_SENSOR_MODES = new Set(['stripmap', 'scan']);
const REDUCED_SCALE_SENSOR_MODES = new Set(['sliding_spotlight']);
const REDUCED_SCALE_BLEND = 0.6;

function sceneScaleFactor() {
  const z = map.getZoom();
  return z >= 9 ? 1 : Math.pow(2, (9 - z) * 0.75);
}

function featureScaleFactor(feat, zoomFactor) {
  const mode = (feat.properties.sensor_mode || '').toLowerCase();
  if (UNSCALED_SENSOR_MODES.has(mode)) return 1;
  if (REDUCED_SCALE_SENSOR_MODES.has(mode)) return 1 + (zoomFactor - 1) * REDUCED_SCALE_BLEND;
  return zoomFactor;
}

function applyScale(geom, cx, cy, factor) {
  if (factor <= 1) return geom;
  const sc = ring => ring.map(([x, y]) => [cx + (x - cx) * factor, cy + (y - cy) * factor]);
  if (geom.type === 'Polygon') {
    return { type: 'Polygon', coordinates: geom.coordinates.map(sc) };
  }
  if (geom.type === 'MultiPolygon') {
    return { type: 'MultiPolygon', coordinates: geom.coordinates.map(p => p.map(sc)) };
  }
  return geom;
}

function getDisplayGeometry(feat, zoomFactor) {
  const id = feat.properties.id;
  const geom = geomCache[id] || feat.geometry;
  const c = centroidCache[id];
  const featFactor = featureScaleFactor(feat, zoomFactor);
  if (featFactor <= 1 || !c) return geom;

  const key = `${id}:${featFactor.toFixed(3)}`;
  if (scaledGeomCache.has(key)) return scaledGeomCache.get(key);

  const scaled = applyScale(geom, c[0], c[1], featFactor);
  scaledGeomCache.set(key, scaled);
  return scaled;
}

// Re-render on zoom only when scale factor tier changes
let _lastScaleFactor = null;
map.on('zoomend', () => {
  const f = sceneScaleFactor();
  if (f !== _lastScaleFactor) { _lastScaleFactor = f; render({ geometryOnly: true }); }
});

// ── Render ─────────────────────────────────────────────────
function render(options = {}) {
  const geometryOnly = !!options.geometryOnly;
  Object.values(activeLayers).forEach(l => map.removeLayer(l));
  activeLayers = {};
  const counts  = { iceye: 0, umbra: 0, capella: 0 };
  const visible = getVisibleFeatures();
  const factor  = sceneScaleFactor();

  // Group visible features by provider into 3 geoJSON layers (far fewer DOM nodes)
  const byProvider = { iceye: [], umbra: [], capella: [] };
  visible.forEach(feat => {
    const geom = getDisplayGeometry(feat, factor);
    byProvider[feat.properties.provider].push({ type: 'Feature', geometry: geom, properties: feat.properties });
    counts[feat.properties.provider]++;
  });

  for (const [provider, feats] of Object.entries(byProvider)) {
    if (!feats.length) continue;
    const color = PROVIDER_COLORS[provider];
    const layer = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
      renderer:     footprintRenderer,
      style:        () => ({ color, weight: 1, opacity: 0.85, fillColor: color, fillOpacity: 0.18 }),
      interactive:  !countryMode,
      pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 5, color, weight: 1, fillColor: color, fillOpacity: 0.6 }),
      onEachFeature: countryMode ? undefined : (feat, lyr) => {
        // Click is handled centrally on the map (see handleSceneClick) so that
        // overlapping footprints can be disambiguated; here we only do hover.
        lyr.on('mouseover', function () { this.setStyle({ fillOpacity: 0.5, weight: 2 }); });
        lyr.on('mouseout',  function () { this.setStyle({ fillOpacity: 0.18, weight: 1 }); });
      },
    });
    layer.addTo(map);
    activeLayers[provider] = layer;
  }

  // While a scene is draped, keep the map clean: hide every other footprint.
  if (_focusMode) Object.values(activeLayers).forEach(l => map.removeLayer(l));

  const total = counts.iceye + counts.umbra + counts.capella;
  if (!geometryOnly) {
    const visEl = document.getElementById('visCount');
    if (visEl) visEl.textContent = total.toLocaleString('en-US');
    updateCoverage(counts, total);
    updateModes(visible);
    updateDownloadCount(visible);
    updateTimelineHistogram();
  }
  if (dataLoaded) history.replaceState(null, '', '#' + encodeState());
}

// ── Geometry helpers ───────────────────────────────────────
function centroid(geom) {
  if (!geom) return null;
  if (geom.type === 'Point') return geom.coordinates;
  const ring = geom.type === 'Polygon' ? geom.coordinates[0]
             : geom.type === 'MultiPolygon' ? geom.coordinates[0][0] : null;
  if (!ring) return null;
  return [ring.reduce((s,c)=>s+c[0],0)/ring.length, ring.reduce((s,c)=>s+c[1],0)/ring.length];
}

function pointInPolygon(pt, geom) {
  const [px, py] = pt;
  const inRing = ring => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if (((yi > py) !== (yj > py)) && (px < (xj - xi) * (py - yi) / (yj - yi) + xi))
        inside = !inside;
    }
    return inside;
  };
  if (geom.type === 'Polygon')           return inRing(geom.coordinates[0]);
  if (geom.type === 'MultiPolygon')      return geom.coordinates.some(p => inRing(p[0]));
  return false;
}

// ── Download count hint ────────────────────────────────────
function updateDownloadCount(visible) {
  const el = document.getElementById('dl-count');
  if (!el) return;
  const withUrl = visible.filter(f => f.properties.download).length;
  if (!visible.length) { el.textContent = ''; return; }
  if (withUrl === visible.length) {
    el.textContent = `${withUrl.toLocaleString()} scenes have direct download links`;
  } else {
    el.textContent = `${withUrl.toLocaleString()} of ${visible.length.toLocaleString()} scenes have direct download links`;
  }
}

// ── Coverage stats ─────────────────────────────────────────
function updateCoverage(counts, total) {
  const totalEl = document.getElementById('covTotal');
  if (totalEl) totalEl.textContent = total.toLocaleString('en-US') + ' SCENES';

  const maxCount = Math.max(counts.iceye, counts.umbra, counts.capella, 1);
  ['iceye', 'umbra', 'capella'].forEach(pid => {
    const numEl = document.querySelector(`.cov .num[data-cov="${pid}"]`);
    const barEl = document.querySelector(`.cov .bar i[data-covbar="${pid}"]`);
    if (numEl) numEl.textContent = counts[pid].toLocaleString('en-US');
    if (barEl) barEl.style.width = (counts[pid] / maxCount * 100) + '%';
  });
}

// ── Mode breakdown ─────────────────────────────────────────
function updateModes(visibleFeatures = getVisibleFeatures()) {
  const modes = {};
  visibleFeatures.forEach(feat => {
    const p = feat.properties;
    const m = (p.sensor_mode || 'unknown').toLowerCase();
    if (!modes[m]) modes[m] = { iceye: 0, umbra: 0, capella: 0, total: 0 };
    modes[m][p.provider]++;
    modes[m].total++;
  });

  const container = document.getElementById('modes');
  if (!container) return;
  const sorted = Object.entries(modes).sort((a, b) => b[1].total - a[1].total);

  if (!sorted.length) {
    container.innerHTML = '<div style="font-size:11px;color:var(--ink-3)">No scenes match filters</div>';
    return;
  }

  container.innerHTML = sorted.map(([name, v]) => {
    const segs = ['iceye', 'umbra', 'capella'].map(pid => {
      if (!v[pid]) return '';
      const pct = (v[pid] / v.total * 100).toFixed(1);
      return `<i style="width:${pct}%;background:var(--${pid});opacity:.85"></i>`;
    }).join('');
    return `<div class="mline">
      <div class="top"><span class="mn">${esc(name.replace(/_/g,' '))}</span><span class="mv">${v.total.toLocaleString('en-US')}</span></div>
      <div class="track">${segs}</div>
    </div>`;
  }).join('');
}

// ── Popup ──────────────────────────────────────────────────
function makePopup(p) {
  const dlUrl = safeUrl(p.download);
  const pvUrl = safeUrl(p.provider_url);
  const det = `<button class="popup-btn details-btn" data-detail-id="${esc(p.id)}">Details</button>`;
  const dl  = dlUrl ? `<a class="popup-btn" href="${esc(dlUrl)}" target="_blank" rel="noopener noreferrer">Download</a>` : '';
  const pv  = pvUrl ? `<a class="popup-btn" href="${esc(pvUrl)}" target="_blank" rel="noopener noreferrer">${esc(p.provider_label)}</a>` : '';
  return `<div class="popup-provider ${esc(p.provider)}">${esc(p.provider_label)}</div>
<div class="popup-id">${esc(p.id||'—')}</div>
<div class="popup-date">📅 ${esc(p.date||'Unknown')}</div>
<div class="popup-mode">⚡ ${esc(p.sensor_mode||'—')}</div>
<div class="popup-actions">${det}${dl}${pv}</div>`;
}

const esc = s => String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ── Overlapping-footprint disambiguation ───────────────────
// A click can land on many stacked footprints (e.g. repeated Umbra tasks over
// the same AOI). Collect every visible scene whose displayed polygon contains
// the click; one → open it, several → show a "N scenes here" picker.
function scenesAtLatLng(latlng) {
  const factor = sceneScaleFactor();
  const pt = [latlng.lng, latlng.lat];
  const hits = [];
  for (const f of getVisibleFeatures()) {
    if (pointInPolygon(pt, getDisplayGeometry(f, factor))) hits.push(f);
  }
  // Newest first so the most recent acquisition is at the top.
  hits.sort((a, b) => (b.properties.date || '').localeCompare(a.properties.date || ''));
  return hits;
}

function openScenePicker(latlng, hits) {
  const CAP = 60;
  const rows = hits.slice(0, CAP).map(f => {
    const p = f.properties;
    return `<button class="picker-row" data-detail-id="${esc(p.id)}">
      <span class="picker-dot" style="background:${PROVIDER_COLORS[p.provider]}"></span>
      <span class="picker-body">
        <span class="picker-id">${esc(p.id || '—')}</span>
        <span class="picker-sub">${esc(p.provider_label)} · ${esc(p.date || 'undated')} · ${esc(p.sensor_mode || '—')}</span>
      </span></button>`;
  }).join('');
  const more = hits.length > CAP
    ? `<div class="picker-more">+${hits.length - CAP} more — zoom in to narrow</div>` : '';
  const html =
    `<div class="scene-picker">
       <div class="picker-head">${hits.length} scenes here</div>
       <div class="picker-list">${rows}</div>${more}
     </div>`;
  L.popup({ maxWidth: 300, minWidth: 240, className: 'scene-picker-popup', autoPan: true })
    .setLatLng(latlng).setContent(html).openOn(map);
}

function handleSceneClick(e) {
  if (countryMode) return;                 // country picker owns clicks in that mode
  const hits = scenesAtLatLng(e.latlng);
  if (!hits.length) return;
  if (hits.length === 1) { showDetail(hits[0].properties); return; }
  openScenePicker(e.latlng, hits);
}
map.on('click', handleSceneClick);

function safeUrl(url) {
  if (!url) return null;
  try {
    const u = new URL(String(url));
    return (u.protocol === 'https:' || u.protocol === 'http:') ? url : null;
  } catch { return null; }
}

// ── Detail panel ───────────────────────────────────────────
window.showDetailById = id => {
  const f = allFeatures.find(f => f.properties.id === id);
  if (f) showDetail(f.properties);
};

document.getElementById('map').addEventListener('click', e => {
  const btn = e.target.closest('[data-detail-id]');
  if (!btn) return;
  window.showDetailById(btn.dataset.detailId);
  map.closePopup();
});

function proxyThumb(url, provider) {
  if (!safeUrl(url)) return null;
  if (provider === 'iceye') {
    return `https://images.weserv.nl/?url=${encodeURIComponent(url)}&w=600&output=jpg&q=80`;
  }
  return url;
}

// ── Geocoded COG preview rendering ─────────────────────────────
// Umbra publishes no thumbnails, and Capella thumbnails are browse images rather
// than reliable map drapes. Their GeoTIFFs are COGs on CORS-enabled,
// range-capable buckets, so we fetch only the smallest overview and render a
// stretched grayscale preview client-side. geotiff.js is loaded lazily.
let _geotiffPromise = null;
function loadGeoTIFF() {
  if (window.GeoTIFF) return Promise.resolve(window.GeoTIFF);
  if (!_geotiffPromise) {
    _geotiffPromise = new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/geotiff@2.1.3/dist-browser/geotiff.js';
      s.onload = () => res(window.GeoTIFF);
      s.onerror = () => rej(new Error('geotiff.js failed to load'));
      document.head.appendChild(s);
    });
  }
  return _geotiffPromise;
}

function umbraCogUrl(download) {
  if (!download) return null;
  // Prefer the geocoded (GEC) product — it's north-up and map-aligned.
  if (/_CSI\.tif$/.test(download)) return download.replace(/_CSI\.tif$/, '_GEC.tif');
  return /\.tif$/.test(download) ? download : null;
}

function capellaCogUrl(p) {
  const products = p && p.products;
  return (products && products.GEC) || (p && p.download) || null;
}

function _pctile(sorted, p) {
  const i = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[i];
}

function wgs84BoundsFromTiffImage(image) {
  if (!image || typeof image.getBoundingBox !== 'function') return null;
  try {
    const b = image.getBoundingBox(); // [west, south, east, north] for EPSG:4326 COGs.
    if (!b || b.length !== 4 || b.some(v => !Number.isFinite(v))) return null;
    const [w, s, e, n] = b;
    if (w < -180 || e > 180 || s < -90 || n > 90 || w >= e || s >= n) return null;
    return L.latLngBounds([[s, w], [n, e]]);
  } catch {
    return null;
  }
}

function geoKeyValue(fd, keyName, keyId) {
  if (!fd) return null;
  if (fd[keyName] != null) return fd[keyName];
  const keys = fd.GeoKeyDirectory;
  if (!keys) return null;
  if (!Array.isArray(keys) && typeof keys === 'object' && keys[keyName] != null) return keys[keyName];
  const n = Number(keys[3]) || 0;
  for (let i = 0; i < n; i++) {
    const off = 4 + i * 4;
    if (keys[off] === keyId) return keys[off + 3];
  }
  return null;
}

function projectedEpsgFromTiff(fd) {
  const epsg = Number(geoKeyValue(fd, 'ProjectedCSTypeGeoKey', 3072));
  return Number.isFinite(epsg) ? epsg : null;
}

function utmToLonLat(x, y, epsg) {
  const north = epsg >= 32601 && epsg <= 32660;
  const south = epsg >= 32701 && epsg <= 32760;
  if (!north && !south) return null;
  const zone = epsg - (north ? 32600 : 32700);
  const a = 6378137;
  const e = 0.08181919084262149;
  const e1sq = 0.006739496742276434;
  const k0 = 0.9996;
  const xAdj = x - 500000;
  const yAdj = south ? y - 10000000 : y;
  const m = yAdj / k0;
  const mu = m / (a * (1 - e * e / 4 - 3 * e ** 4 / 64 - 5 * e ** 6 / 256));
  const e1 = (1 - Math.sqrt(1 - e * e)) / (1 + Math.sqrt(1 - e * e));
  const j1 = 3 * e1 / 2 - 27 * e1 ** 3 / 32;
  const j2 = 21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32;
  const j3 = 151 * e1 ** 3 / 96;
  const j4 = 1097 * e1 ** 4 / 512;
  const fp = mu + j1 * Math.sin(2 * mu) + j2 * Math.sin(4 * mu) + j3 * Math.sin(6 * mu) + j4 * Math.sin(8 * mu);
  const sinfp = Math.sin(fp), cosfp = Math.cos(fp), tanfp = Math.tan(fp);
  const c1 = e1sq * cosfp * cosfp;
  const t1 = tanfp * tanfp;
  const r1 = a * (1 - e * e) / Math.pow(1 - e * e * sinfp * sinfp, 1.5);
  const n1 = a / Math.sqrt(1 - e * e * sinfp * sinfp);
  const d = xAdj / (n1 * k0);
  const q1 = n1 * tanfp / r1;
  const q2 = d * d / 2;
  const q3 = (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * e1sq) * d ** 4 / 24;
  const q4 = (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * e1sq - 3 * c1 * c1) * d ** 6 / 720;
  const lat = fp - q1 * (q2 - q3 + q4);
  const q5 = d;
  const q6 = (1 + 2 * t1 + c1) * d ** 3 / 6;
  const q7 = (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * e1sq + 24 * t1 * t1) * d ** 5 / 120;
  const lon0 = ((zone - 1) * 6 - 180 + 3) * Math.PI / 180;
  const lon = lon0 + (q5 - q6 + q7) / cosfp;
  return [lon * 180 / Math.PI, lat * 180 / Math.PI];
}

function tagLength(tag) {
  if (!tag) return 0;
  if (typeof tag.length === 'number') return tag.length;
  return Object.keys(tag).filter(k => /^\d+$/.test(k)).length;
}

function wgs84CornersFromTiffImage(image) {
  const fd = image && image.fileDirectory;
  const w = image && image.getWidth ? image.getWidth() : 0;
  const h = image && image.getHeight ? image.getHeight() : 0;
  if (!fd || !w || !h) return null;

  let xy = null;
  const m = fd.ModelTransformation;
  if (tagLength(m) >= 8) {
    xy = (col, row) => [m[0] * col + m[1] * row + m[3], m[4] * col + m[5] * row + m[7]];
  } else if (fd.ModelTiepoint && fd.ModelPixelScale) {
    const t = fd.ModelTiepoint;
    const s = fd.ModelPixelScale;
    xy = (col, row) => [t[3] + (col - t[0]) * s[0], t[4] - (row - t[1]) * s[1]];
  }
  if (!xy) return null;

  const corners = [
    xy(0, 0), xy(w, 0), xy(w, h), xy(0, h),
  ];
  let lonLat = corners;
  if (corners.some(([x, y]) => !Number.isFinite(x) || !Number.isFinite(y))) {
    return null;
  }
  if (corners.some(([x, y]) => x < -180 || x > 180 || y < -90 || y > 90)) {
    const epsg = projectedEpsgFromTiff(fd);
    if (!epsg) return null;
    lonLat = corners.map(([x, y]) => utmToLonLat(x, y, epsg));
    if (lonLat.some(v => !v)) return null;
  }
  if (lonLat.some(([x, y]) => !Number.isFinite(x) || !Number.isFinite(y) || x < -180 || x > 180 || y < -90 || y > 90)) {
    return null;
  }
  return lonLat.map(([lng, lat]) => L.latLng(lat, lng)); // TL, TR, BR, BL
}

// Render a COG's smallest overview to a stretched grayscale PNG data URL.
// Cached by URL so re-selecting a scene doesn't re-fetch/re-render.
const _cogPreviewCache = new Map();
function cogPreviewDataUrl(url) {
  if (_cogPreviewCache.has(url)) return _cogPreviewCache.get(url);
  const promise = (async () => {
    const GT = await loadGeoTIFF();
    const tiff = await GT.fromUrl(url);
    const n = await tiff.getImageCount();
    const baseImage = await tiff.getImage(0);
    const image = await tiff.getImage(Math.max(0, n - 1));  // smallest overview
    const w = image.getWidth(), h = image.getHeight();
    const bounds = wgs84BoundsFromTiffImage(baseImage);
    const corners = wgs84CornersFromTiffImage(baseImage);
    const band = (await image.readRasters())[0];

    // Robust 2–98 percentile stretch on a subsample of non-zero pixels.
    const sample = [];
    const step = Math.max(1, Math.floor(band.length / 40000));
    for (let i = 0; i < band.length; i += step) { const v = band[i]; if (v > 0) sample.push(v); }
    sample.sort((a, b) => a - b);
    if (!sample.length) throw new Error('COG preview has no non-zero pixels');
    const lo = _pctile(sample, 0.02), hi = _pctile(sample, 0.98), range = (hi - lo) || 1;

    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    const id = ctx.createImageData(w, h);
    for (let i = 0; i < band.length; i++) {
      if (band[i] <= 0) { id.data[i*4+3] = 0; continue; }
      let t = (band[i] - lo) / range; t = t < 0 ? 0 : t > 1 ? 1 : t;
      const g = (t * 255) | 0;
      id.data[i*4] = g; id.data[i*4+1] = g; id.data[i*4+2] = g; id.data[i*4+3] = 255;
    }
    ctx.putImageData(id, 0, 0);
    return { url: canvas.toDataURL('image/png'), bounds, corners };
  })().catch(err => { _cogPreviewCache.delete(url); throw err; });
  _cogPreviewCache.set(url, promise);
  return promise;
}

// Resolve a displayable preview image URL for a scene, per provider.
async function scenePreviewUrl(p) {
  if (p.provider === 'umbra') {
    const cog = umbraCogUrl(p.download);
    if (cog) {
      const preview = await cogPreviewDataUrl(cog);
      return { url: preview.url, bounds: preview.bounds, corners: preview.corners, fit: preview.corners ? 'quad' : 'bbox' };
    }
  }
  if (p.provider === 'capella') {
    const cog = capellaCogUrl(p);
    if (cog) {
      const preview = await cogPreviewDataUrl(cog);
      return { url: preview.url, bounds: preview.bounds, corners: preview.corners, fit: preview.corners ? 'quad' : 'bbox' };
    }
  }
  const thumb = proxyThumb(p.thumbnail, p.provider);
  if (thumb) return { url: thumb, fit: 'quad' };
  return null;
}

// ── Georeferenced scene preview drape ──────────────────────────
// Clicking a scene drapes its preview onto the map and fits the view to it,
// EO-viewer style. When a geocoded COG is available, we use the raster metadata
// corners. Otherwise the provider thumbnail is fitted approximately to the
// catalog footprint.
let sceneOverlay = null, sceneOutline = null, _drapeUrl = null, _drapeToken = 0, _selToken = 0;
let _focusMode = false, _drapeHidden = false;

const QuadImageOverlay = L.Layer.extend({
  initialize(url, corners, options) {
    this._url = url;
    this._corners = corners.map(c => L.latLng(c));
    L.setOptions(this, options);
  },
  onAdd(mapRef) {
    this._map = mapRef;
    this._img = L.DomUtil.create('img', 'leaflet-image-layer');
    this._img.src = this._url;
    this._img.alt = '';
    this._img.draggable = false;
    this._img.style.position = 'absolute';
    this._img.style.transformOrigin = '0 0';
    this._img.style.opacity = this.options.opacity == null ? 1 : this.options.opacity;
    this._img.style.pointerEvents = 'none';
    this._img.onload = () => this._reset();
    this.getPane().appendChild(this._img);
    mapRef.on('zoom viewreset move', this._reset, this);
    this._reset();
  },
  onRemove(mapRef) {
    mapRef.off('zoom viewreset move', this._reset, this);
    L.DomUtil.remove(this._img);
    this._img = null;
  },
  getPane() {
    return this._map.getPane(this.options.pane || 'overlayPane');
  },
  getElement() {
    return this._img;
  },
  setOpacity(opacity) {
    if (this._img) this._img.style.opacity = opacity;
    return this;
  },
  _reset() {
    if (!this._map || !this._img) return;
    const w = this._img.naturalWidth || this._img.width || 1;
    const h = this._img.naturalHeight || this._img.height || 1;
    this._img.style.width = `${w}px`;
    this._img.style.height = `${h}px`;
    const [tl, tr, , bl] = this._corners.map(c => this._map.latLngToLayerPoint(c));
    const a = (tr.x - tl.x) / w;
    const b = (tr.y - tl.y) / w;
    const c = (bl.x - tl.x) / h;
    const d = (bl.y - tl.y) / h;
    this._img.style.transform = `matrix(${a},${b},${c},${d},${tl.x},${tl.y})`;
  },
});

function clearDrape() {
  _drapeToken++;  // invalidate any in-flight image loads
  if (sceneOverlay) { map.removeLayer(sceneOverlay); sceneOverlay = null; }
  if (sceneOutline) { map.removeLayer(sceneOutline); sceneOutline = null; }
  _drapeUrl = null;
  _drapeHidden = false;
  map.getPane('scenePane').style.opacity = 1;
  const ctl = document.getElementById('drape-ctl');
  if (ctl) ctl.classList.add('hidden');
  // Restore the other scene footprints we hid for the clean view.
  if (_focusMode) { _focusMode = false; if (dataLoaded) render(); }
}

// A CSS clip-path (in image-space %) that trims the bbox-placed image to the
// exact footprint polygon — removes nodata corners and any spill beyond the
// swath, and scales automatically with the overlay on zoom.
function footprintClipPath(g, bounds) {
  const ring = g.type === 'Polygon' ? g.coordinates[0]
             : g.type === 'MultiPolygon' ? g.coordinates[0][0] : null;
  if (!ring || ring.length < 4) return null;
  const W = bounds.getWest(), E = bounds.getEast(), S = bounds.getSouth(), N = bounds.getNorth();
  const dx = (E - W) || 1, dy = (N - S) || 1;
  const pts = ring.map(c => {
    const x = (c[0] - W) / dx * 100;
    const y = (N - c[1]) / dy * 100;   // y inverted: image top = north
    return `${x.toFixed(2)}% ${y.toFixed(2)}%`;
  });
  return `polygon(${pts.join(', ')})`;
}

function quadFootprintClipPath(g, corners) {
  const ring = g.type === 'Polygon' ? g.coordinates[0]
             : g.type === 'MultiPolygon' ? g.coordinates[0][0] : null;
  if (!ring || ring.length < 4 || !corners || corners.length !== 4) return null;
  const [tl, tr, , bl] = corners.map(c => map.latLngToLayerPoint(c));
  const ux = tr.x - tl.x, uy = tr.y - tl.y;
  const vx = bl.x - tl.x, vy = bl.y - tl.y;
  const det = ux * vy - uy * vx;
  if (Math.abs(det) < 1e-9) return null;
  const pts = ring.map(c => {
    const p = map.latLngToLayerPoint([c[1], c[0]]);
    const px = p.x - tl.x, py = p.y - tl.y;
    const a = (px * vy - py * vx) / det;
    const b = (ux * py - uy * px) / det;
    return `${(a * 100).toFixed(2)}% ${(b * 100).toFixed(2)}%`;
  });
  return `polygon(${pts.join(', ')})`;
}

function footprintImageCorners(g, provider) {
  const ring = g.type === 'Polygon' ? g.coordinates[0]
             : g.type === 'MultiPolygon' ? g.coordinates[0][0] : null;
  if (!ring) return null;
  const pts = ring.slice(0, -1).length >= 4 && ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1]
    ? ring.slice(0, -1)
    : ring.slice();
  if (pts.length !== 4) return null;

  if (provider === 'iceye') {
    // ICEYE browse KML uses gx:LatLonQuad order: lower-left, lower-right,
    // upper-right, upper-left. Convert to the affine layer order: TL, TR, BR, BL.
    return [pts[3], pts[2], pts[1], pts[0]].map(c => L.latLng(c[1], c[0]));
  }

  // The upstream catalog footprints are emitted in raster-corner order for the
  // geocoded preview assets: TL, BL, BR, TR, then closed. Convert to the order
  // the affine image layer expects: TL, TR, BR, BL.
  return [pts[0], pts[3], pts[2], pts[1]].map(c => L.latLng(c[1], c[0]));
}

function drapeScene(p, preview) {
  clearDrape();
  const g = geomCache[p.id];
  const imgUrl = preview && preview.url;
  if (!imgUrl || !g) return;
  const token = ++_drapeToken;
  const previewCorners = preview.corners || null;
  const bounds = previewCorners ? L.latLngBounds(previewCorners) : (preview.bounds || L.geoJSON(g).getBounds());
  map.fitBounds(bounds, { padding: [40, 40], animate: true, maxZoom: 13 });

  // Preload so we only drape a working image (and can fall back on error).
  const probe = new Image();
  probe.onload = () => {
    if (token !== _drapeToken) return;  // superseded by a newer/cleared selection
    const corners = preview.fit === 'quad' ? (previewCorners || footprintImageCorners(g, p.provider)) : null;
    sceneOverlay = corners
      ? new QuadImageOverlay(imgUrl, corners, { opacity: 1, interactive: false, pane: 'scenePane' }).addTo(map)
      : L.imageOverlay(imgUrl, bounds, { opacity: 1, interactive: false, pane: 'scenePane' }).addTo(map);
    const clip = corners ? quadFootprintClipPath(g, corners) : footprintClipPath(g, bounds);
    const el = sceneOverlay.getElement();
    if (clip && el) el.style.clipPath = clip;

    // White dashed outline of the selected footprint.
    const ring = g.type === 'Polygon' ? g.coordinates[0]
               : g.type === 'MultiPolygon' ? g.coordinates[0][0] : null;
    if (ring) {
      sceneOutline = L.polygon(ring.map(c => [c[1], c[0]]),
        { color: '#fff', weight: 1.4, fill: false, dashArray: '4 3', interactive: false, pane: 'drawnPane' }).addTo(map);
    }

    // Clean view: hide all other scene footprints while this one is draped.
    _focusMode = true;
    Object.values(activeLayers).forEach(l => map.removeLayer(l));

    _drapeUrl = imgUrl;
    showDrapeControl(p.id);
  };
  probe.onerror = () => { if (token === _drapeToken) showHint('Preview unavailable for this scene'); };
  probe.src = imgUrl;
}

function showDrapeControl(sceneId) {
  const ctl = document.getElementById('drape-ctl');
  if (!ctl) return;
  ctl.querySelector('.drape-id').textContent = sceneId || 'scene';
  ctl.querySelector('#drape-opacity').value = '100';
  ctl.querySelector('#drape-toggle').setAttribute('aria-pressed', 'false');
  ctl.classList.remove('hidden');
}

// Drape control wiring (opacity / fullscreen / close).
(function () {
  const ctl = document.getElementById('drape-ctl');
  if (!ctl) return;
  ctl.querySelector('#drape-opacity').addEventListener('input', e => {
    map.getPane('scenePane').style.opacity = +e.target.value / 100;
  });
  ctl.querySelector('#drape-fs').addEventListener('click', () => {
    if (_drapeUrl && window.__openLightbox) window.__openLightbox(_drapeUrl);
  });
  ctl.querySelector('#drape-toggle').addEventListener('click', e => {
    _drapeHidden = !_drapeHidden;
    if (sceneOverlay) { const el = sceneOverlay.getElement(); if (el) el.style.display = _drapeHidden ? 'none' : ''; }
    if (sceneOutline) sceneOutline.setStyle({ opacity: _drapeHidden ? 0 : 1 });
    e.currentTarget.setAttribute('aria-pressed', String(_drapeHidden));
  });
  ctl.querySelector('#drape-close').addEventListener('click', clearDrape);
})();

function showDetail(p) {
  // One acquisition may be published in several data formats (Capella).
  const products = (p.products && Object.keys(p.products).length > 1) ? p.products : null;

  // Full metadata — every meaningful field, blanks skipped.
  const rows = [
    ['Acquired',        p.date],
    ['Provider',        p.provider_label],
    ['Sensor mode',     p.sensor_mode],
    ['Polarization',    p.polarization],
    ['Resolution',      p.resolution != null ? p.resolution + ' m' : null],
    ['Incidence angle', p.incidence_angle != null ? p.incidence_angle + '°' : null],
    ['Off-nadir',       p.off_nadir != null ? p.off_nadir + '°' : null],
    ['Orbit',           p.orbit_state],
    ['Look',            p.look_dir],
    ['Formats',         products ? (p.formats || Object.keys(products)).join(', ') : null],
    ['Collection',      products ? null : p.collection],
    ['First seen',      p.first_seen],
  ].filter(([,v]) => v != null && v !== '' && v !== 'n/a')
   .map(([k,v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('');

  const pvUrl = safeUrl(p.provider_url);
  const pv = pvUrl
    ? `<a class="detail-action-btn" href="${esc(pvUrl)}" target="_blank" rel="noopener noreferrer">View on ${esc(p.provider_label)}</a>` : '';

  let formatBlock = '';
  let dl = '';
  if (products) {
    const fmts = (p.formats || Object.keys(p.products)).filter(f => safeUrl(p.products[f]));
    const first = fmts[0];
    const chips = fmts.map((f, i) =>
      `<button class="fmt-chip${i === 0 ? ' is-active' : ''}" data-fmt="${esc(f)}" data-url="${esc(p.products[f])}">${esc(f)}</button>`
    ).join('');
    formatBlock = `<div class="format-select"><div class="format-label">Data format</div><div class="format-chips">${chips}</div></div>`;
    dl = `<a class="detail-action-btn primary" id="dl-asset" href="${esc(p.products[first])}" target="_blank" rel="noopener noreferrer">Download ${esc(first)}</a>`;
  } else {
    const dlUrl = safeUrl(p.download);
    dl = dlUrl
      ? `<a class="detail-action-btn primary" href="${esc(dlUrl)}" target="_blank" rel="noopener noreferrer">Download Asset</a>` : '';
  }

  document.getElementById('detail-content').innerHTML =
    `<div class="mod-h detail-h"><span class="ix">05</span><span class="ttl">Scene</span><span class="rule"></span><span class="meta">METADATA</span></div>
<div class="detail-provider ${esc(p.provider)}">${esc(p.provider_label)}</div>
<div class="detail-id">${esc(p.id||'—')}</div>
<table class="detail-table"><tbody>${rows}</tbody></table>
${formatBlock}<div class="detail-actions">${dl}${pv}</div>`;

  if (products) {
    const chipsEl = document.querySelector('#detail-content .format-chips');
    const dlBtn = document.getElementById('dl-asset');
    chipsEl.addEventListener('click', e => {
      const chip = e.target.closest('.fmt-chip');
      if (!chip) return;
      chipsEl.querySelectorAll('.fmt-chip').forEach(c => c.classList.remove('is-active'));
      chip.classList.add('is-active');
      dlBtn.href = chip.dataset.url;
      dlBtn.textContent = `Download ${chip.dataset.fmt}`;
    });
  }

  const panel = document.getElementById('detail-panel');
  panel.scrollTop = 0;
  panel.classList.remove('hidden');
  document.body.classList.remove('detail-collapsed');
  document.getElementById('detail-toggle').classList.remove('hidden');

  // Drape the preview on the map at the footprint location. Guard against a
  // slow preview (e.g. Umbra COG) resolving after a newer scene was selected.
  const sel = ++_selToken;
  scenePreviewUrl(p)
    .then(url => { if (sel === _selToken) drapeScene(p, url); })
    .catch(() => { if (sel === _selToken) showHint('Preview unavailable for this scene'); });
}

// ── Detail toggle ───────────────────────────────────────────
document.getElementById('detail-toggle').addEventListener('click', () => {
  document.body.classList.toggle('detail-collapsed');
  setTimeout(() => map.invalidateSize(), 220);
});

// ── Provider toggles (map legend) ─────────────────────────
function setSensor(s, on) {
  providerActive[s] = on;
  document.querySelectorAll(`.lg[data-sensor="${s}"]`).forEach(el => el.setAttribute('aria-pressed', on));
  render();
}
document.querySelectorAll('.lg[data-sensor]').forEach(el =>
  el.addEventListener('click', () => setSensor(el.dataset.sensor, el.getAttribute('aria-pressed') !== 'true'))
);

// ── Mode select ─────────────────────────────────────────────
document.getElementById('modeSel').addEventListener('change', e => {
  const modeVal = document.getElementById('modeVal');
  if (modeVal) modeVal.textContent = e.target.value ? e.target.value.toUpperCase() : 'ALL';
  render();
});

function populateModes(features) {
  const modes = new Set();
  features.forEach(f => {
    const m = f.properties.sensor_mode;
    if (m && m.toLowerCase() !== 'n/a') modes.add(m.toLowerCase());
  });
  const sel = document.getElementById('modeSel');
  if (!sel) return;
  [...modes].sort().forEach(m => {
    const o = document.createElement('option');
    o.value = m; o.textContent = m.replace(/_/g, ' ');
    sel.appendChild(o);
  });
}

// ── Orbit/look segmented controls ─────────────────────────
document.querySelectorAll('.seg[data-group]').forEach(seg => {
  seg.addEventListener('click', e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b === btn));
    if (seg.dataset.group === 'orbit') orbitFilter = btn.dataset.v === 'all' ? '' : (btn.dataset.v === 'asc' ? 'ascending' : 'descending');
    if (seg.dataset.group === 'look')  lookFilter  = btn.dataset.v === 'all' ? '' : btn.dataset.v;
    render();
  });
});

// ── Toast ──────────────────────────────────────────────────
let toastTimer;
function showToast(msg) {
  const el = document.getElementById('toast');
  const msgEl = document.getElementById('toastMsg');
  if (!el || !msgEl) return;
  msgEl.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2400);
}

// ── Reset ──────────────────────────────────────────────────
document.getElementById('resetBtn').addEventListener('click', () => {
  ['iceye','umbra','capella'].forEach(id => { providerActive[id] = true; });
  document.querySelectorAll('.lg[data-sensor]').forEach(el => el.setAttribute('aria-pressed', 'true'));

  if (MONTHS.length) setTimelineRange(Math.max(0, MONTHS.length - 24), MONTHS.length - 1);

  const modeSel = document.getElementById('modeSel');
  if (modeSel) modeSel.value = '';
  const modeVal = document.getElementById('modeVal');
  if (modeVal) modeVal.textContent = 'ALL';

  orbitFilter = ''; lookFilter = '';
  document.querySelectorAll('.seg[data-group] button').forEach(btn => {
    btn.setAttribute('aria-pressed', btn.dataset.v === 'all');
  });

  clearAll();
  showToast('Filters reset');
});

// ── Collapse console ───────────────────────────────────────
document.getElementById('collapseBtn').addEventListener('click', () => {
  document.getElementById('app').classList.toggle('collapsed');
  setTimeout(() => map.invalidateSize(), 340);
});

// ── Country picker ──────────────────────────────────────────
const tooltip    = document.getElementById('country-tooltip');
const hintBanner = document.getElementById('hint-banner');

function showHint(msg) { hintBanner.textContent = msg; hintBanner.classList.add('visible'); }
function hideHint()    { hintBanner.classList.remove('visible'); }

function setCountryMode(on) {
  countryMode = on;
  const _cPane = map.getPane('countryPane');
  _cPane.style.pointerEvents = on ? 'auto' : 'none';
  // pointer-events on an HTML div does NOT cascade into SVG children, and
  // Leaflet sets the attribute directly on each <path>, overriding CSS inheritance.
  // Use inline CSS (higher priority than SVG presentation attributes) on every
  // SVG element inside the pane so they are truly click-transparent when off.
  _cPane.querySelectorAll('svg, svg *').forEach(s => { s.style.pointerEvents = on ? '' : 'none'; });
  const btn = document.getElementById('tb-country');
  btn.classList.toggle('country-on', on);
  btn.classList.toggle('country-active', !on && !!selectedCountry);
  if (on) {
    document.body.classList.add('mode-country');
    showHint('Hover and click a country to filter scenes');
    render();
    loadCountries();
  } else {
    document.body.classList.remove('mode-country');
    hideHint();
    tooltip.style.display = 'none';
    render();
  }
}

document.getElementById('tb-country').addEventListener('click', () => setCountryMode(!countryMode));

const ISO_NAMES = {
  4:'Afghanistan',8:'Albania',12:'Algeria',24:'Angola',32:'Argentina',36:'Australia',
  40:'Austria',50:'Bangladesh',56:'Belgium',64:'Bhutan',68:'Bolivia',76:'Brazil',
  100:'Bulgaria',104:'Myanmar',116:'Cambodia',120:'Cameroon',124:'Canada',144:'Sri Lanka',
  152:'Chile',156:'China',170:'Colombia',178:'Congo',180:'Dem. Rep. Congo',
  188:'Costa Rica',191:'Croatia',192:'Cuba',196:'Cyprus',203:'Czechia',208:'Denmark',
  218:'Ecuador',818:'Egypt',231:'Ethiopia',246:'Finland',250:'France',276:'Germany',
  288:'Ghana',300:'Greece',320:'Guatemala',332:'Haiti',340:'Honduras',356:'India',
  360:'Indonesia',364:'Iran',368:'Iraq',372:'Ireland',376:'Israel',380:'Italy',
  388:'Jamaica',392:'Japan',400:'Jordan',398:'Kazakhstan',404:'Kenya',408:'North Korea',
  410:'South Korea',414:'Kuwait',418:'Laos',422:'Lebanon',504:'Morocco',484:'Mexico',
  528:'Netherlands',554:'New Zealand',566:'Nigeria',578:'Norway',586:'Pakistan',
  591:'Panama',604:'Peru',608:'Philippines',616:'Poland',620:'Portugal',634:'Qatar',
  642:'Romania',643:'Russia',682:'Saudi Arabia',694:'Sierra Leone',705:'Slovenia',
  706:'Somalia',710:'South Africa',724:'Spain',729:'Sudan',752:'Sweden',756:'Switzerland',
  760:'Syria',158:'Taiwan',762:'Tajikistan',764:'Thailand',792:'Turkey',800:'Uganda',
  804:'Ukraine',784:'United Arab Emirates',826:'United Kingdom',840:'United States',
  858:'Uruguay',860:'Uzbekistan',862:'Venezuela',704:'Vietnam',887:'Yemen',894:'Zambia',716:'Zimbabwe',
};

function applyPendingCountryRestore() {
  if (!pendingCountryRestore || !countryLayer) return;
  const name = pendingCountryRestore;
  const target = name.toLowerCase();
  let matched = false;
  countryLayer.eachLayer(l => {
    const featureName = l.feature && l.feature.properties.name;
    if (!featureName || featureName.toLowerCase() !== target) return;
    if (selectedCountry && selectedCountry.layer && selectedCountry.layer !== l) {
      selectedCountry.layer.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
    }
    l.setStyle({ fillColor: '#d29922', fillOpacity: 0.1, color: '#d29922', weight: 1.5 });
    selectedCountry = { layer: l, bbox: l.feature._bbox, name: featureName, geometry: l.feature.geometry };
    document.getElementById('tb-country').classList.add('country-active');
    showHint(`Filtered by ${featureName} · click 🌐 to switch country · × to clear`);
    matched = true;
  });
  if (matched) {
    pendingCountryRestore = null;
    render();
  }
}

async function loadCountries() {
  if (countriesLoaded) {
    applyPendingCountryRestore();
    return;
  }
  try {
    const res    = await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json');
    const topo   = await res.json();
    const geojson = topojson.feature(topo, topo.objects.countries);

    geojson.features.forEach(f => {
      f.properties = f.properties || {};
      if (!f.properties.name) f.properties.name = ISO_NAMES[+f.id] || `Country ${f.id}`;
      unwrapAntimeridian(f.geometry);
      f._bbox = bboxFromGeometry(f.geometry);
    });
    geojson.features = geojson.features.filter(f => +f.id !== 10);

    countryLayer = L.geoJSON(geojson, {
      renderer: L.svg({ pane: 'countryPane' }),
      pane: 'countryPane',
      style: () => ({ color: 'transparent', weight: 0, fillColor: '#ffffff', fillOpacity: 0.001 }),
      onEachFeature(feat, layer) {
        layer.on('mousemove', e => {
          if (!countryMode) return;
          tooltip.textContent = feat.properties.name;
          tooltip.style.display = 'block';
          tooltip.style.left = (e.originalEvent.clientX + 14) + 'px';
          tooltip.style.top  = (e.originalEvent.clientY - 32) + 'px';
          if (hoveredCountry && hoveredCountry !== layer && hoveredCountry !== (selectedCountry && selectedCountry.layer)) {
            hoveredCountry.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
          }
          if (layer !== (selectedCountry && selectedCountry.layer)) {
            layer.setStyle({ fillColor: '#d29922', fillOpacity: 0.15, color: '#d29922', weight: 1 });
          }
          hoveredCountry = layer;
        });
        layer.on('mouseout', () => {
          tooltip.style.display = 'none';
          if (layer !== (selectedCountry && selectedCountry.layer)) {
            layer.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
          }
          hoveredCountry = null;
        });
        layer.on('click', () => {
          if (!countryMode) return;
          if (selectedCountry) {
            selectedCountry.layer.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
          }
          const bbox = feat._bbox;
          layer.setStyle({ fillColor: '#d29922', fillOpacity: 0.1, color: '#d29922', weight: 1.5 });
          selectedCountry = { layer, bbox, name: feat.properties.name, geometry: feat.geometry };

          const safeBounds = [
            [Math.max(-85, bbox[1]), Math.max(-180, bbox[0])],
            [Math.min(85,  bbox[3]), Math.min(180,  bbox[2])],
          ];
          map.fitBounds(safeBounds, { padding: [40,40], maxZoom: 8, duration: 700 });
          setCountryMode(false);
          showHint(`Filtered by ${feat.properties.name} · click 🌐 to switch country · × to clear`);
          render();
        });
      },
    }).addTo(map);
    countriesLoaded = true;

    applyPendingCountryRestore();
  } catch(e) { console.error('Countries failed:', e); }
}

// ── Draw tools ─────────────────────────────────────────────
// Drawn AOI shapes must not intercept scene clicks. Canvas-rendered layers
// (preferCanvas:true) use options.interactive for hit-testing — pointer-events CSS
// has no effect on them. SVG layers need pointer-events:none on their _path.
function makeDrawnLayerPassthrough(l) {
  l.options.interactive = false;
  if (l._path) l._path.style.pointerEvents = 'none';
  if (typeof l.eachLayer === 'function') l.eachLayer(sub => makeDrawnLayerPassthrough(sub));
}

let activeDrawTool = null;

function startDraw(ToolClass, options, btnId) {
  if (activeDrawTool) { activeDrawTool.disable(); activeDrawTool = null; }
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  map.addControl(drawControl);
  document.body.classList.add('mode-draw');
  showHint('Click to start drawing — double-click to finish');
  const tool = new ToolClass(map, options);
  tool.enable(); activeDrawTool = tool;
  document.getElementById(btnId).classList.add('active');
}

document.getElementById('tb-bbox').addEventListener('click', () => startDraw(L.Draw.Rectangle, drawControl.options.draw.rectangle, 'tb-bbox'));
document.getElementById('tb-poly').addEventListener('click', () => startDraw(L.Draw.Polygon,   drawControl.options.draw.polygon,   'tb-poly'));

map.on(L.Draw.Event.CREATED, e => {
  drawnItems.clearLayers(); drawnItems.addLayer(e.layer);
  drawnItems.eachLayer(l => makeDrawnLayerPassthrough(l));
  const b = e.layer.getBounds();
  aoiBbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  if (selectedCountry) {
    selectedCountry.layer.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
    selectedCountry = null;
  }
  map.removeControl(drawControl); activeDrawTool = null;
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  document.body.classList.remove('mode-draw'); hideHint(); render();
});

map.on(L.Draw.Event.DRAWSTOP, () => {
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  document.body.classList.remove('mode-draw'); hideHint();
});

// ── Upload GeoJSON ─────────────────────────────────────────
document.getElementById('tb-upload').addEventListener('change', e => {
  const file = e.target.files[0]; if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      const geojson = JSON.parse(ev.target.result);
      drawnItems.clearLayers();
      const layer = L.geoJSON(geojson, { style: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05 }, pane: 'drawnPane' });
      layer.addTo(drawnItems);
      drawnItems.eachLayer(l => makeDrawnLayerPassthrough(l));
      const b = layer.getBounds();
      aoiBbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
      if (selectedCountry) {
        selectedCountry.layer.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
        selectedCountry = null;
      }
      map.fitBounds(b, { padding: [40,40] }); render();
    } catch { showToast('Invalid GeoJSON file.'); }
  };
  reader.readAsText(file); e.target.value = '';
});

// ── Clear all ──────────────────────────────────────────────
document.getElementById('tb-clear').addEventListener('click', clearAll);
function clearAll() {
  clearDrape();
  if (activeDrawTool) { activeDrawTool.disable(); activeDrawTool = null; }
  map.removeControl(drawControl);
  drawnItems.clearLayers(); aoiBbox = null;
  if (selectedCountry) {
    selectedCountry.layer.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
    selectedCountry = null;
  }
  document.getElementById('tb-country').classList.remove('country-active');
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  document.body.classList.remove('mode-draw');
  setCountryMode(false);
  hideHint();
  render();
}

function restoreBboxAoi(parts) {
  if (parts.length !== 4 || parts.some(n => !isFinite(n))) return false;
  const [w, s, e, n] = parts;
  if (w >= e || s >= n) return false;
  aoiBbox = parts;
  drawnItems.clearLayers();
  const rect = L.rectangle([[s, w], [n, e]], {
    color: '#3fb950',
    weight: 1.5,
    fillOpacity: 0.05,
    pane: 'drawnPane',
  });
  rect.addTo(drawnItems);
  makeDrawnLayerPassthrough(rect);
  return true;
}

// ── STAC export ────────────────────────────────────────────
document.querySelector('[data-export="stac"]').addEventListener('click', () => {
  const visible = getVisibleFeatures();
  const blob = new Blob([JSON.stringify({
    type:'FeatureCollection', stac_version:'1.0.0',
    id:'open-sar-triad-export',
    description:'Exported SAR scenes from open-sar-triad',
    exported_at: new Date().toISOString(),
    source:'https://github.com/Jack-Hayes/commerical-sar-stac',
    features: visible,
  }, null, 2)], { type:'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `open-sar-triad-${new Date().toISOString().slice(0,10)}.json`;
  a.click(); URL.revokeObjectURL(url);
  showToast(`STAC exported · ${visible.length.toLocaleString()} scenes`);
});

// ── Download script ────────────────────────────────────────
function fileNameFromUrl(url, fallbackId) {
  try {
    const name = new URL(url).pathname.split('/').pop();
    return safeFileName(name || fallbackId);
  } catch { return safeFileName(fallbackId); }
}

function safeFileName(value) {
  const cleaned = String(value || 'scene')
    .replace(/[\\/:*?"<>|`$]/g, '_')
    .replace(/[\x00-\x1f\x7f]/g, '')
    .trim();
  return cleaned || 'scene';
}

function shQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

document.querySelector('[data-export="script"]').addEventListener('click', () => {
  const visible = getVisibleFeatures();
  if (!visible.length) { showToast('No scenes match current filters'); return; }

  const byProvider = { iceye: [], umbra: [], capella: [] };
  visible.forEach(feat => {
    const p = feat.properties;
    if (safeUrl(p.download) && byProvider[p.provider]) byProvider[p.provider].push(p);
  });

  const counts = Object.fromEntries(
    Object.entries(byProvider).map(([k, v]) => [k, v.length])
  );
  const total   = counts.iceye + counts.umbra + counts.capella;
  const omitted = visible.length - total;
  const date    = new Date().toISOString().slice(0, 10);

  const lines = [
    '#!/usr/bin/env bash',
    `# open-sar-triad download script — generated ${new Date().toISOString()}`,
    `# Visible: ${visible.length} scenes  |  Downloadable: ${total}  (ICEYE: ${counts.iceye}, Umbra: ${counts.umbra}, Capella: ${counts.capella})`,
    ...(omitted ? [`# Note: ${omitted} scene(s) omitted — no download URL in catalog`] : []),
    '# Usage:  bash download.sh',
    '# Dry run: bash download.sh --dry-run',
    '# Requires: curl',
    '',
    'set -euo pipefail',
    'DRY_RUN=false',
    '[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true',
    '',
    'dl() {',
    '  local url="$1" dest="$2"',
    '  mkdir -p "$(dirname "$dest")"',
    '  if $DRY_RUN; then',
    '    echo "[dry-run] $dest"',
    '  else',
    '    echo "→ $dest"',
    '    curl -fsSL --retry 3 -C - -o "$dest" "$url"',
    '  fi',
    '}',
    '',
  ];

  for (const [pid, scenes] of Object.entries(byProvider)) {
    if (!scenes.length) continue;
    lines.push(`# ── ${PROVIDER_LABELS[pid]} (${scenes.length} scenes) ${'─'.repeat(40)}`);
    scenes.forEach(p => {
      const fname = fileNameFromUrl(p.download, p.id);
      lines.push(`dl ${shQuote(safeUrl(p.download))} ${shQuote(`${pid}/${fname}`)}`);
    });
    lines.push('');
  }

  lines.push('echo ""');
  lines.push(`echo "✓ Done — ${total} files"`);

  const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url;
  a.download = `open-sar-triad-download-${date}.sh`;
  a.click();
  URL.revokeObjectURL(url);
  showToast(`download.sh ready · ${total} scenes`);
});

// ── Copy share link ────────────────────────────────────────
document.querySelector('[data-export="link"]').addEventListener('click', () => {
  history.replaceState(null, '', '#' + encodeState());
  navigator.clipboard.writeText(window.location.href).then(() => {
    showToast('Share link copied to clipboard');
  });
});

// ── Theme toggle ───────────────────────────────────────────
function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  document.querySelectorAll('[data-theme-btn]').forEach(b => b.setAttribute('aria-pressed', b.dataset.themeBtn === t));
  baseLayer.setUrl(TILE[t]);
  labelLayer.setUrl(TILE.labels[t]);
  setTimeout(() => drawGraticule(), 30);
}
document.querySelectorAll('[data-theme-btn]').forEach(b =>
  b.addEventListener('click', () => setTheme(b.dataset.themeBtn))
);

// Remove persisted AOI toolbar positions from earlier draggable builds.
localStorage.removeItem('aoi-toolbar-pos');

// ── Custom timeline scrubber ───────────────────────────────
function initTimeline(features) {
  const ts = features.map(f => f.properties.date).filter(Boolean)
    .map(d => new Date(d).getTime()).filter(t => !isNaN(t));
  if (!ts.length) return;

  const minMs = Math.min(...ts);
  const maxMs = Math.max(...ts);

  let y = new Date(minMs).getFullYear(), m = new Date(minMs).getMonth() + 1;
  const endY = new Date(maxMs).getFullYear(), endM = new Date(maxMs).getMonth() + 1;
  MONTHS = [];
  while (y < endY || (y === endY && m <= endM)) {
    MONTHS.push(`${y}-${String(m).padStart(2,'0')}`);
    m++; if (m > 12) { m = 1; y++; }
  }

  const defaultFromIdx = Math.max(0, MONTHS.length - 24);
  tlFrom = defaultFromIdx;
  tlTo   = MONTHS.length - 1;

  buildTimelineHist(features);
  buildTimelineAxis();
  setTimelineRange(tlFrom, tlTo);

  const track = document.getElementById('track');
  if (!track) return;

  function dragHandle(which) {
    return function(e) {
      e.preventDefault();
      const rect = track.getBoundingClientRect();
      const move = ev => {
        const x = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - rect.left) / rect.width;
        const idx = Math.round(Math.max(0, Math.min(1, x)) * (MONTHS.length - 1));
        if (which === 'from') setTimelineRange(Math.min(idx, tlTo), tlTo);
        else setTimelineRange(tlFrom, Math.max(idx, tlFrom));
      };
      const up = () => {
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        render();
      };
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
    };
  }

  const hFrom = document.getElementById('hFrom');
  const hTo   = document.getElementById('hTo');
  if (hFrom) hFrom.addEventListener('pointerdown', dragHandle('from'));
  if (hTo)   hTo.addEventListener('pointerdown',   dragHandle('to'));

  track.addEventListener('pointerdown', e => {
    if (e.target.closest('.tl-h')) return;
    const rect = track.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const idx = Math.round(Math.max(0, Math.min(1, x)) * (MONTHS.length - 1));
    if (Math.abs(idx - tlFrom) <= Math.abs(idx - tlTo)) setTimelineRange(Math.min(idx, tlTo), tlTo);
    else setTimelineRange(tlFrom, Math.max(idx, tlFrom));
    render();
  });

  // Apply pending date restore from URL hash
  const dr = window._pendingDateRestore;
  if (dr && dr.from && dr.to) {
    const fromM = dr.from.slice(0, 7);
    const toM   = dr.to.slice(0, 7);
    const fi = MONTHS.indexOf(fromM);
    const ti = MONTHS.indexOf(toM);
    if (fi >= 0) tlFrom = fi;
    if (ti >= 0) tlTo   = ti;
    setTimelineRange(tlFrom, tlTo);
    window._pendingDateRestore = null;
  }
}

function pct(i) { return MONTHS.length > 1 ? i / (MONTHS.length - 1) * 100 : 0; }

function setTimelineRange(from, to) {
  tlFrom = Math.max(0, Math.min(from, to));
  tlTo   = Math.min(MONTHS.length - 1, Math.max(from, to));
  const a = pct(tlFrom), b = pct(tlTo);

  const sel   = document.getElementById('sel');
  const hFrom = document.getElementById('hFrom');
  const hTo   = document.getElementById('hTo');
  const gFrom = document.getElementById('gFrom');
  const gTo   = document.getElementById('gTo');
  const rFrom = document.getElementById('rngFrom');
  const rTo   = document.getElementById('rngTo');
  const rDur  = document.getElementById('rngDur');

  if (sel)   { sel.style.left = a + '%'; sel.style.width = (b - a) + '%'; }
  if (hFrom)  hFrom.style.left = a + '%';
  if (hTo)    hTo.style.left   = b + '%';
  if (gFrom)  gFrom.textContent = MONTHS[tlFrom] || '';
  if (gTo)    gTo.textContent   = MONTHS[tlTo]   || '';
  if (rFrom)  rFrom.textContent = MONTHS[tlFrom] || '';
  if (rTo)    rTo.textContent   = MONTHS[tlTo]   || '';
  if (rDur)  { const d = tlTo - tlFrom + 1; rDur.textContent = d + ' month' + (d > 1 ? 's' : ''); }

  document.querySelectorAll('#hist .b').forEach((b, i) => b.classList.toggle('out', i < tlFrom || i > tlTo));
}

function buildTimelineHist(features) {
  const hist = document.getElementById('hist');
  if (!hist || !MONTHS.length) return;
  hist.innerHTML = '';

  const data = MONTHS.map(() => ({ iceye: 0, umbra: 0, capella: 0 }));
  features.forEach(feat => {
    const d = feat.properties.date;
    if (!d) return;
    const m = d.slice(0, 7);
    const idx = MONTHS.indexOf(m);
    if (idx < 0) return;
    const pid = feat.properties.provider;
    if (data[idx][pid] !== undefined) data[idx][pid]++;
  });
  const max = Math.max(...data.map(d => d.iceye + d.umbra + d.capella), 1);

  data.forEach((d, i) => {
    const tot = d.iceye + d.umbra + d.capella;
    const bar = document.createElement('div');
    bar.className = 'b'; bar.dataset.i = i;
    bar.style.height = (tot / max * 100) + '%';
    let acc = 0, inner = '';
    ['capella', 'umbra', 'iceye'].forEach(s => {
      const h = tot ? d[s] / tot * 100 : 0;
      inner += `<div class="seg" style="height:${h}%;bottom:${acc}%;background:var(--${s});opacity:.8"></div>`;
      acc += h;
    });
    bar.innerHTML = inner;
    hist.appendChild(bar);
  });
}

function buildTimelineAxis() {
  const axis = document.getElementById('axis');
  if (!axis || !MONTHS.length) return;
  axis.innerHTML = '';
  const step = MONTHS.length > 24 ? 6 : MONTHS.length > 12 ? 3 : 1;
  for (let i = 0; i < MONTHS.length; i += step) {
    const t = document.createElement('div'); t.className = 'tl-tick';
    t.style.left = pct(i) + '%';
    t.innerHTML = `<span class="tx">${MONTHS[i]}</span>`;
    axis.appendChild(t);
  }
  const last = document.createElement('div'); last.className = 'tl-tick';
  last.style.left = '100%';
  last.innerHTML = `<span class="tx">${MONTHS[MONTHS.length - 1]}</span>`;
  axis.appendChild(last);
}

function updateTimelineHistogram() {
  document.querySelectorAll('#hist .b').forEach((b, i) => b.classList.toggle('out', i < tlFrom || i > tlTo));
}

// ── Shareable URL state ────────────────────────────────────
function encodeState() {
  const p = new URLSearchParams();
  const hidden = ['iceye','umbra','capella'].filter(id => !providerActive[id]);
  if (hidden.length) p.set('hide', hidden.join(','));
  if (MONTHS.length) {
    p.set('from', MONTHS[tlFrom]);
    p.set('to',   MONTHS[tlTo]);
  }
  const modeSel = document.getElementById('modeSel');
  const mode = modeSel ? modeSel.value : '';
  if (mode)        p.set('mode',  mode);
  if (orbitFilter) p.set('orbit', orbitFilter);
  if (lookFilter)  p.set('look',  lookFilter);
  if (selectedCountry) {
    p.set('country', selectedCountry.name);
  } else if (aoiBbox) {
    p.set('bbox', aoiBbox.map(v => Math.round(v * 1000) / 1000).join(','));
  }
  const c = map.getCenter();
  p.set('lat', Math.round(c.lat * 100) / 100);
  p.set('lng', Math.round(c.lng * 100) / 100);
  p.set('z',   map.getZoom());
  return p.toString();
}

function restoreState() {
  const raw = window.location.hash.slice(1);
  if (!raw) return;
  let p;
  try { p = new URLSearchParams(raw); } catch { return; }

  // Providers
  (p.get('hide') || '').split(',').filter(Boolean).forEach(id => {
    if (id in providerActive) {
      providerActive[id] = false;
      document.querySelectorAll(`.lg[data-sensor="${id}"]`).forEach(el => el.setAttribute('aria-pressed', 'false'));
    }
  });

  const setSegVal = (group, val) => {
    document.querySelectorAll(`.seg[data-group="${group}"] button`).forEach(btn => {
      btn.setAttribute('aria-pressed', btn.dataset.v === val);
    });
  };

  const orbit = p.get('orbit');
  if (orbit) { orbitFilter = orbit; setSegVal('orbit', orbit === 'ascending' ? 'asc' : 'desc'); }

  const look = p.get('look');
  if (look) { lookFilter = look; setSegVal('look', look); }

  const mode = p.get('mode');
  const modeSel = document.getElementById('modeSel');
  if (mode && modeSel) modeSel.value = mode;

  const bbox = p.get('bbox');
  if (bbox) {
    const parts = bbox.split(',').map(Number);
    restoreBboxAoi(parts);
  }

  const country = p.get('country');
  if (country) {
    pendingCountryRestore = country;
    loadCountries();
  }

  const lat = parseFloat(p.get('lat'));
  const lng = parseFloat(p.get('lng'));
  const z   = parseInt(p.get('z'), 10);
  if (!isNaN(lat) && !isNaN(lng) && !isNaN(z)) map.setView([lat, lng], z);

  // Date — deferred until initTimeline() builds MONTHS
  window._pendingDateRestore = { from: p.get('from'), to: p.get('to') };
}

// ── Recent activity ────────────────────────────────────────
const DAY_MS = 86400000;

function relDays(fromStr, nowMs) {
  const d = Math.floor((nowMs - Date.parse(fromStr + 'T00:00:00Z')) / DAY_MS);
  if (d <= 0) return 'today';
  if (d === 1) return 'yesterday';
  if (d < 7) return d + 'd ago';
  if (d < 14) return '1w ago';
  return Math.floor(d / 7) + 'w ago';
}

// Fly the map to a scene and open its detail panel.
function focusFeature(id) {
  // showDetail drapes the scene and fits the map to its footprint.
  window.showDetailById(id);
}

function renderRecent() {
  const list = document.getElementById('recentList');
  const meta = document.getElementById('recentMeta');
  if (!list) return;

  const nowMs   = Date.now();
  const cutoff  = new Date(nowMs - 30 * DAY_MS).toISOString().slice(0, 10);
  const weekAgo = new Date(nowMs - 7  * DAY_MS).toISOString().slice(0, 10);

  const trackingActive = allFeatures.some(f => f.properties.first_seen);

  const recent = allFeatures
    .filter(f => f.properties.first_seen && f.properties.first_seen >= cutoff)
    .sort((a, b) => (b.properties.first_seen).localeCompare(a.properties.first_seen)
                 || (b.properties.date || '').localeCompare(a.properties.date || ''))
    .slice(0, 40);

  // Ingestion tracking has run but nothing arrived in the window.
  if (!recent.length && trackingActive) {
    meta.textContent = 'NO NEW SCENES';
    list.innerHTML =
      `<p class="recent-empty">No scenes added in the last 30 days. The catalog refreshes every Monday — new arrivals will appear here.</p>`;
    return;
  }

  // No ingestion history yet: fall back to the last 30 days of acquisitions so
  // the panel is useful now, and it switches to "new this week" once tracking fills.
  if (!recent.length) {
    const latest = allFeatures
      .filter(f => f.properties.date && f.properties.date >= cutoff)
      .sort((a, b) => b.properties.date.localeCompare(a.properties.date))
      .slice(0, 200);
    meta.textContent = latest.length ? `${latest.length} · LAST 30 DAYS` : 'LATEST CAPTURES';
    const rowLatest = f => {
      const p = f.properties;
      return `<button class="recent-row" data-recent-id="${esc(p.id)}">
        <span class="recent-dot" style="background:${PROVIDER_COLORS[p.provider]}"></span>
        <span class="recent-body">
          <span class="recent-id">${esc(p.id || '—')}</span>
          <span class="recent-sub">${esc(p.provider_label)} · ${esc(p.sensor_mode || '—')}</span>
        </span>
        <span class="recent-age">${esc(p.date)}</span>
      </button>`;
    };
    list.innerHTML =
      `<p class="recent-empty">Weekly ingestion tracking begins with the next update. Showing the latest acquisitions for now.</p>`
      + latest.map(rowLatest).join('');
    return;
  }

  const weekCount = recent.filter(f => f.properties.first_seen >= weekAgo).length;
  meta.textContent = weekCount ? `+${weekCount} THIS WEEK` : `${recent.length} THIS MONTH`;

  const row = f => {
    const p = f.properties;
    return `<button class="recent-row" data-recent-id="${esc(p.id)}">
      <span class="recent-dot" style="background:${PROVIDER_COLORS[p.provider]}"></span>
      <span class="recent-body">
        <span class="recent-id">${esc(p.id || '—')}</span>
        <span class="recent-sub">${esc(p.provider_label)} · ${esc(p.date || 'undated')} · ${esc(p.sensor_mode || '—')}</span>
      </span>
      <span class="recent-age">${relDays(p.first_seen, nowMs)}</span>
    </button>`;
  };

  const week   = recent.filter(f => f.properties.first_seen >= weekAgo);
  const earlier = recent.filter(f => f.properties.first_seen < weekAgo);
  let html = '';
  if (week.length)    html += `<div class="recent-group">THIS WEEK</div>` + week.map(row).join('');
  if (earlier.length) html += `<div class="recent-group">EARLIER THIS MONTH</div>` + earlier.map(row).join('');
  list.innerHTML = html;
}

document.getElementById('recentList').addEventListener('click', e => {
  const btn = e.target.closest('.recent-row');
  if (btn) focusFeature(btn.dataset.recentId);
});

// ── Load data ──────────────────────────────────────────────
fetch('data/scenes.geojson')
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(geojson => {
    allFeatures = geojson.features || [];
    buildGeomCache(allFeatures);
    populateModes(allFeatures);
    restoreState();
    initTimeline(allFeatures);
    renderRecent();
    document.getElementById('loading').classList.add('hidden');
    dataLoaded = true;
    render();
  })
  .catch(() => {
    document.getElementById('loading').innerHTML =
      `<p style="color:var(--capella)">No scene data found.<br>Run <code>scripts/fetch_catalog.py</code> to generate it.</p>`;
  });

// ── Image lightbox ────────────────────────────────────────────
(function () {
  const lb       = document.getElementById('img-lightbox');
  const lbImg    = document.getElementById('lb-img');
  const viewport = document.getElementById('lb-viewport');
  const zoomLabel = document.getElementById('lb-zoom-label');

  let scale = 1, ox = 0, oy = 0;
  let dragging = false, startX = 0, startY = 0, startOx = 0, startOy = 0;
  let naturalW = 0, naturalH = 0;

  function applyTransform() {
    lbImg.style.transform = `translate(${ox}px,${oy}px) scale(${scale})`;
    zoomLabel.textContent = Math.round(scale * 100) + '%';
  }

  // transform-origin is 0 0, so the image spans screen [ox, ox+iw] × [oy, oy+ih].
  // When the image is smaller than the viewport on an axis, lock it centered;
  // when larger, clamp so it can't be dragged past its own edges.
  function clampOffset() {
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    const iw = naturalW * scale, ih = naturalH * scale;
    ox = iw <= vw ? (vw - iw) / 2 : Math.min(0, Math.max(vw - iw, ox));
    oy = ih <= vh ? (vh - ih) / 2 : Math.min(0, Math.max(vh - ih, oy));
  }

  function resetView() {
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    scale = Math.min(vw / naturalW, vh / naturalH, 1);
    ox = (vw - naturalW * scale) / 2;
    oy = (vh - naturalH * scale) / 2;
    applyTransform();
  }

  function zoom(factor, cx, cy) {
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    const px = (cx ?? vw / 2), py = (cy ?? vh / 2);
    const newScale = Math.min(8, Math.max(0.1, scale * factor));
    ox = px - (px - ox) * (newScale / scale);
    oy = py - (py - oy) * (newScale / scale);
    scale = newScale;
    clampOffset();
    applyTransform();
  }

  function open(src) {
    lb.classList.add('open');
    document.body.style.overflow = 'hidden';
    const ready = () => {
      naturalW = lbImg.naturalWidth;
      naturalH = lbImg.naturalHeight;
      resetView();
    };
    lbImg.onload = ready;
    lbImg.src = src;
    // Cached images may not fire onload — fit immediately if already decoded.
    if (lbImg.complete && lbImg.naturalWidth) ready();
  }

  function close() {
    lb.classList.remove('open');
    document.body.style.overflow = '';
    lbImg.src = '';
  }

  // Let the on-map drape control open the fullscreen viewer with its image.
  window.__openLightbox = open;

  document.getElementById('lb-close').addEventListener('click', close);
  document.getElementById('lb-zoom-in').addEventListener('click', () => zoom(1.25));
  document.getElementById('lb-zoom-out').addEventListener('click', () => zoom(0.8));
  document.getElementById('lb-reset').addEventListener('click', resetView);

  // Close on backdrop click — but not when the click is the tail end of a pan.
  let moved = false;
  lb.addEventListener('click', e => {
    if (moved) { moved = false; return; }
    if (e.target === lb || e.target === viewport) close();
  });

  // Keyboard
  document.addEventListener('keydown', e => {
    if (!lb.classList.contains('open')) return;
    if (e.key === 'Escape') close();
    if (e.key === '+' || e.key === '=') zoom(1.25);
    if (e.key === '-') zoom(0.8);
    if (e.key === '0') resetView();
  });

  // Mouse wheel zoom
  viewport.addEventListener('wheel', e => {
    e.preventDefault();
    const rect = viewport.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.12 : 0.88, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });

  // Drag to pan
  viewport.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    dragging = true; moved = false; startX = e.clientX; startY = e.clientY;
    startOx = ox; startOy = oy;
    viewport.classList.add('dragging');
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    if (Math.abs(e.clientX - startX) > 3 || Math.abs(e.clientY - startY) > 3) moved = true;
    ox = startOx + (e.clientX - startX);
    oy = startOy + (e.clientY - startY);
    clampOffset();
    applyTransform();
  });
  window.addEventListener('mouseup', () => {
    dragging = false;
    viewport.classList.remove('dragging');
  });

  // Touch pan + pinch zoom
  let lastTouchDist = null;
  viewport.addEventListener('touchstart', e => {
    if (e.touches.length === 1) {
      dragging = true; startX = e.touches[0].clientX; startY = e.touches[0].clientY;
      startOx = ox; startOy = oy;
    }
    if (e.touches.length === 2) {
      dragging = false;
      lastTouchDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  }, { passive: true });
  viewport.addEventListener('touchmove', e => {
    e.preventDefault();
    if (e.touches.length === 1 && dragging) {
      ox = startOx + (e.touches[0].clientX - startX);
      oy = startOy + (e.touches[0].clientY - startY);
      clampOffset(); applyTransform();
    }
    if (e.touches.length === 2 && lastTouchDist) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
      const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      const rect = viewport.getBoundingClientRect();
      zoom(dist / lastTouchDist, cx - rect.left, cy - rect.top);
      lastTouchDist = dist;
    }
  }, { passive: false });
  viewport.addEventListener('touchend', () => { dragging = false; lastTouchDist = null; });
})();
