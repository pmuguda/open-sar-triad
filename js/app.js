/* open-sar-triad — main app */

const PROVIDER_COLORS = { iceye: '#00FF87', umbra: '#00C9FF', capella: '#FF6B35' };
const PROVIDER_LABELS = { iceye: 'ICEYE', umbra: 'Umbra', capella: 'Capella' };

let allFeatures = [];
let activeLayers = {};
let drawnBbox = null;
let drawnItems = null;
let drawControl = null;
let isDrawing = false;
let countryLayer = null;
let countryMode = false;
let selectedCountryBbox = null;
let countriesLoaded = false;
let dateSlider = null;
let dateMin = 0, dateMax = 0;

// ── Map ──────────────────────────────────────────────────────
const map = L.map('map', { center: [20, 0], zoom: 2, zoomControl: true });

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://openstreetmap.org">OSM</a>',
  subdomains: 'abcd', maxZoom: 19,
}).addTo(map);

drawnItems = new L.FeatureGroup().addTo(map);
drawControl = new L.Control.Draw({
  draw: {
    rectangle: { shapeOptions: { color: '#00C9FF', weight: 1.5, fillOpacity: 0.05 } },
    polyline: false, polygon: false, circle: false, marker: false, circlemarker: false,
  },
  edit: { featureGroup: drawnItems, remove: false, edit: false },
});

// ── Provider state ───────────────────────────────────────────
const providerActive = { iceye: true, umbra: true, capella: true };

['iceye', 'umbra', 'capella'].forEach(pid => {
  document.getElementById(`pill-${pid}`).addEventListener('click', () => {
    providerActive[pid] = !providerActive[pid];
    const el = document.getElementById(`pill-${pid}`);
    el.classList.toggle('active', providerActive[pid]);
    render();
  });
});

// ── Filters ──────────────────────────────────────────────────
function getFilters() {
  let dateFrom = null, dateTo = null;
  if (dateSlider) {
    const vals = dateSlider.get();
    dateFrom = sliderValToDate(+vals[0]);
    dateTo   = sliderValToDate(+vals[1]);
  }
  return {
    iceye:    providerActive.iceye,
    umbra:    providerActive.umbra,
    capella:  providerActive.capella,
    dateFrom,
    dateTo,
    mode:     document.getElementById('mode-filter').value,
    bbox:     selectedCountryBbox || drawnBbox,
  };
}

function sliderValToDate(val) {
  const d = new Date(val);
  return d.toISOString().slice(0, 10);
}

// ── Render ────────────────────────────────────────────────────
function render() {
  const f = getFilters();
  Object.values(activeLayers).forEach(l => map.removeLayer(l));
  activeLayers = {};

  const counts = { iceye: 0, umbra: 0, capella: 0 };

  allFeatures.forEach(feat => {
    const p = feat.properties;
    if (!f[p.provider]) return;
    if (f.dateFrom && p.date && p.date < f.dateFrom) return;
    if (f.dateTo   && p.date && p.date > f.dateTo)   return;
    if (f.mode && p.sensor_mode && p.sensor_mode !== f.mode) return;
    if (f.bbox) {
      const c = centroid(feat.geometry);
      if (!c) return;
      const [w, s, e, n] = f.bbox;
      if (c[0] < w || c[0] > e || c[1] < s || c[1] > n) return;
    }

    const color = PROVIDER_COLORS[p.provider];
    const layer = L.geoJSON(feat, {
      style: { color, weight: 1, opacity: 0.8, fillColor: color, fillOpacity: 0.08 },
    });
    layer.on('click', () => showDetail(p));
    layer.on('mouseover', function () { this.setStyle({ fillOpacity: 0.28, weight: 1.5 }); });
    layer.on('mouseout',  function () { this.setStyle({ fillOpacity: 0.08, weight: 1 }); });
    layer.bindPopup(makePopup(p), { maxWidth: 280 });
    layer.addTo(map);
    activeLayers[p.id] = layer;
    counts[p.provider]++;
  });

  const total = counts.iceye + counts.umbra + counts.capella;
  document.getElementById('total-vis').textContent  = total;
  document.getElementById('tb-iceye').textContent   = counts.iceye;
  document.getElementById('tb-umbra').textContent   = counts.umbra;
  document.getElementById('tb-capella').textContent = counts.capella;

  drawHistogram(counts);
}

function centroid(geom) {
  if (!geom) return null;
  if (geom.type === 'Point') return geom.coordinates;
  const coords = geom.type === 'Polygon' ? geom.coordinates[0]
               : geom.type === 'MultiPolygon' ? geom.coordinates[0][0] : null;
  if (!coords) return null;
  return [
    coords.reduce((s, c) => s + c[0], 0) / coords.length,
    coords.reduce((s, c) => s + c[1], 0) / coords.length,
  ];
}

// ── Histogram ─────────────────────────────────────────────────
function drawHistogram(counts) {
  const canvas = document.getElementById('histogram');
  const W = canvas.offsetWidth || 256;
  canvas.width = W;
  const H = 90;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  const providers = ['iceye', 'umbra', 'capella'];
  const values = providers.map(p => counts[p]);
  const max = Math.max(...values, 1);

  const barW = Math.floor((W - 32) / 3);
  const gap  = (W - barW * 3) / 4;

  providers.forEach((pid, i) => {
    const x = gap + i * (barW + gap);
    const barH = Math.max(2, Math.round((values[i] / max) * (H - 28)));
    const y = H - 18 - barH;
    const color = PROVIDER_COLORS[pid];

    // bar
    ctx.fillStyle = color + '33';
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x, y, barW, barH, 3);
    ctx.fill();
    ctx.stroke();

    // value label
    ctx.fillStyle = color;
    ctx.font = `600 11px system-ui, -apple-system, sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText(values[i], x + barW / 2, y - 4);

    // name label
    ctx.fillStyle = '#8b949e';
    ctx.font = `10px system-ui, -apple-system, sans-serif`;
    ctx.fillText(PROVIDER_LABELS[pid], x + barW / 2, H - 4);
  });
}

// ── Popup ─────────────────────────────────────────────────────
function makePopup(p) {
  const dl  = p.download    ? `<a class="popup-btn" href="${p.download}" target="_blank">Download</a>` : '';
  const pv  = p.provider_url? `<a class="popup-btn" href="${p.provider_url}" target="_blank">${p.provider_label}</a>` : '';
  const det = `<button class="popup-btn details-btn" onclick="showDetailById('${escHtml(p.id)}')">Details</button>`;
  return `<div class="popup-provider ${p.provider}">${p.provider_label}</div>
<div class="popup-id">${escHtml(p.id||'—')}</div>
<div class="popup-date">📅 ${p.date||'Unknown'}</div>
<div class="popup-mode">⚡ ${p.sensor_mode||'—'}</div>
<div class="popup-actions">${det}${dl}${pv}</div>`;
}

function escHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// ── Detail panel ───────────────────────────────────────────────
window.showDetailById = id => {
  const f = allFeatures.find(f => f.properties.id === id);
  if (f) showDetail(f.properties);
};

function showDetail(p) {
  const thumb = p.thumbnail
    ? `<img class="detail-thumbnail" src="${p.thumbnail}" alt="SAR thumbnail" onerror="this.style.display='none'" />`
    : `<div class="detail-thumb-placeholder">No preview available</div>`;
  const rows = [
    ['Date',         p.date          || '—'],
    ['Provider',     p.provider_label|| '—'],
    ['Mode',         p.sensor_mode   || '—'],
    ['Polarization', p.polarization  || '—'],
    ['Resolution',   p.resolution != null ? p.resolution + ' m' : '—'],
    ['Collection',   p.collection    || '—'],
  ].map(([k,v]) => `<tr><td>${k}</td><td>${escHtml(v)}</td></tr>`).join('');

  const dl = p.download
    ? `<a class="detail-action-btn primary" href="${p.download}" target="_blank">Download Asset</a>` : '';
  const pv = p.provider_url
    ? `<a class="detail-action-btn" href="${p.provider_url}" target="_blank">View on ${p.provider_label}</a>` : '';

  document.getElementById('detail-content').innerHTML =
    `${thumb}<div class="detail-provider ${p.provider}">${p.provider_label}</div>
<div class="detail-id">${escHtml(p.id||'—')}</div>
<table class="detail-table"><tbody>${rows}</tbody></table>
<div class="detail-actions">${dl}${pv}</div>`;
  document.getElementById('detail-panel').classList.remove('hidden');
}

document.getElementById('detail-close').addEventListener('click', () => {
  document.getElementById('detail-panel').classList.add('hidden');
});

// ── Country picker ─────────────────────────────────────────────
const tooltip = document.getElementById('country-tooltip');

document.getElementById('country-btn').addEventListener('click', () => {
  countryMode = !countryMode;
  document.getElementById('country-btn').classList.toggle('active', countryMode);
  if (countryMode) {
    loadCountries();
    map.getContainer().style.cursor = 'crosshair';
  } else {
    map.getContainer().style.cursor = '';
    tooltip.style.display = 'none';
  }
});

document.getElementById('country-clear').addEventListener('click', () => {
  selectedCountryBbox = null;
  document.getElementById('country-selected').classList.add('hidden');
  render();
});

async function loadCountries() {
  if (countriesLoaded) return;
  try {
    const res  = await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json');
    const topo = await res.json();
    const geojson = topojson.feature(topo, topo.objects.countries);

    countryLayer = L.geoJSON(geojson, {
      style: { color: 'transparent', weight: 0, fillColor: 'transparent', fillOpacity: 0 },
      onEachFeature(feat, layer) {
        layer.on('mousemove', e => {
          if (!countryMode) return;
          const name = feat.properties.name || 'Unknown';
          tooltip.textContent = name;
          tooltip.style.display = 'block';
          const rect = document.getElementById('map-area').getBoundingClientRect();
          tooltip.style.left = (e.originalEvent.clientX - rect.left + 12) + 'px';
          tooltip.style.top  = (e.originalEvent.clientY - rect.top  - 28) + 'px';
          layer.setStyle({ fillColor: '#d29922', fillOpacity: 0.15, color: '#d29922', weight: 1 });
        });
        layer.on('mouseout', () => {
          tooltip.style.display = 'none';
          layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
        });
        layer.on('click', () => {
          if (!countryMode) return;
          const b = layer.getBounds();
          selectedCountryBbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
          const name = feat.properties.name || 'Unknown';
          document.getElementById('country-name').textContent = name;
          document.getElementById('country-selected').classList.remove('hidden');
          map.fitBounds(b, { padding: [40, 40] });
          countryMode = false;
          document.getElementById('country-btn').classList.remove('active');
          map.getContainer().style.cursor = '';
          tooltip.style.display = 'none';
          render();
        });
      },
    }).addTo(map);

    countriesLoaded = true;
  } catch (e) {
    console.error('Failed to load countries', e);
  }
}

// ── BBox draw ──────────────────────────────────────────────────
document.getElementById('tool-bbox').addEventListener('click', () => {
  if (isDrawing) return;
  isDrawing = true;
  document.getElementById('tool-bbox').classList.add('active');
  map.addControl(drawControl);
  new L.Draw.Rectangle(map, drawControl.options.draw.rectangle).enable();
});

map.on(L.Draw.Event.CREATED, e => {
  drawnItems.clearLayers();
  drawnItems.addLayer(e.layer);
  const b = e.layer.getBounds();
  drawnBbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  map.removeControl(drawControl);
  isDrawing = false;
  document.getElementById('tool-bbox').classList.remove('active');
  render();
});

document.getElementById('tool-clear-bbox').addEventListener('click', () => {
  drawnItems.clearLayers();
  drawnBbox = null;
  render();
});

// ── Sensor mode dropdown ────────────────────────────────────────
document.getElementById('mode-filter').addEventListener('change', render);

function populateModes(features) {
  const modes = new Set();
  features.forEach(f => {
    const m = f.properties.sensor_mode;
    if (m && m !== 'N/A' && m !== 'null') modes.add(m);
  });
  const sel = document.getElementById('mode-filter');
  [...modes].sort().forEach(m => {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m;
    sel.appendChild(opt);
  });
}

// ── Date slider ────────────────────────────────────────────────
function initDateSlider(features) {
  const dates = features
    .map(f => f.properties.date)
    .filter(Boolean)
    .map(d => new Date(d).getTime())
    .filter(t => !isNaN(t));

  if (!dates.length) return;

  dateMin = Math.min(...dates);
  dateMax = Math.max(...dates);

  const sliderEl = document.getElementById('date-slider');
  dateSlider = noUiSlider.create(sliderEl, {
    start: [dateMin, dateMax],
    connect: true,
    range: { min: dateMin, max: dateMax },
    tooltips: [
      { to: v => new Date(+v).toISOString().slice(0,7) },
      { to: v => new Date(+v).toISOString().slice(0,7) },
    ],
    step: 30 * 24 * 3600 * 1000, // 1 month steps
  });

  function updateLabels(vals) {
    document.getElementById('date-label-from').textContent = new Date(+vals[0]).toISOString().slice(0,7);
    document.getElementById('date-label-to').textContent   = new Date(+vals[1]).toISOString().slice(0,7);
  }

  dateSlider.on('update', updateLabels);
  dateSlider.on('change', () => render());
  updateLabels([dateMin, dateMax]);
}

// ── STAC export ────────────────────────────────────────────────
document.getElementById('export-stac-btn').addEventListener('click', () => {
  const f = getFilters();
  const visible = allFeatures.filter(feat => {
    const p = feat.properties;
    if (!f[p.provider]) return false;
    if (f.dateFrom && p.date && p.date < f.dateFrom) return false;
    if (f.dateTo   && p.date && p.date > f.dateTo)   return false;
    if (f.mode && p.sensor_mode && p.sensor_mode !== f.mode) return false;
    if (f.bbox) {
      const c = centroid(feat.geometry);
      if (!c) return false;
      const [w, s, e, n] = f.bbox;
      if (c[0] < w || c[0] > e || c[1] < s || c[1] > n) return false;
    }
    return true;
  });

  const collection = {
    type: 'FeatureCollection',
    stac_version: '1.0.0',
    id: 'open-sar-triad-export',
    description: 'Exported SAR scenes from open-sar-triad',
    exported_at: new Date().toISOString(),
    providers: [
      { name: 'ICEYE',   url: 'https://www.iceye.com/open-data-initiative' },
      { name: 'Umbra',   url: 'https://umbra.space/open-data/' },
      { name: 'Capella', url: 'https://www.capellaspace.com/community/capella-open-data-program/' },
    ],
    features: visible,
  };

  const blob = new Blob([JSON.stringify(collection, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url;
  a.download = `open-sar-triad-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

// ── Reset ──────────────────────────────────────────────────────
document.getElementById('reset-btn').addEventListener('click', () => {
  ['iceye','umbra','capella'].forEach(pid => {
    providerActive[pid] = true;
    document.getElementById(`pill-${pid}`).classList.add('active');
  });
  if (dateSlider) dateSlider.set([dateMin, dateMax]);
  document.getElementById('mode-filter').value = '';
  drawnItems.clearLayers();
  drawnBbox = null;
  selectedCountryBbox = null;
  document.getElementById('country-selected').classList.add('hidden');
  countryMode = false;
  document.getElementById('country-btn').classList.remove('active');
  map.getContainer().style.cursor = '';
  render();
});

// ── Load data ──────────────────────────────────────────────────
fetch('data/scenes.geojson')
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(geojson => {
    allFeatures = geojson.features || [];

    const totals = { iceye: 0, umbra: 0, capella: 0 };
    allFeatures.forEach(f => { const p = f.properties.provider; if (p in totals) totals[p]++; });

    document.getElementById('total-all').textContent   = allFeatures.length;
    document.getElementById('pill-cnt-iceye').textContent   = totals.iceye;
    document.getElementById('pill-cnt-umbra').textContent   = totals.umbra;
    document.getElementById('pill-cnt-capella').textContent = totals.capella;

    populateModes(allFeatures);
    initDateSlider(allFeatures);

    document.getElementById('loading').classList.add('hidden');
    render();
  })
  .catch(err => {
    console.warn('scenes.geojson:', err);
    document.getElementById('loading').innerHTML =
      `<p style="color:#FF6B35">No scene data found.<br>Run <code>scripts/fetch_catalog.py</code> to generate it.</p>`;
  });
