/* open-sar-triad */

const PROVIDER_COLORS = { iceye: '#00FF87', umbra: '#00C9FF', capella: '#FF6B35' };
const PROVIDER_LABELS = { iceye: 'ICEYE', umbra: 'Umbra', capella: 'Capella' };

let allFeatures     = [];
let activeLayers    = {};
let aoiLayer        = null;     // current drawn / uploaded AOI
let aoiBbox         = null;     // [w,s,e,n] from AOI
let countryLayer    = null;
let countriesLoaded = false;
let countryMode     = false;
let hoveredCountry  = null;
let selectedCountry = null;     // { layer, bbox }
let dateSlider      = null;
let dateMin = 0, dateMax = 0;
const providerActive = { iceye: true, umbra: true, capella: true };

// ── Map ────────────────────────────────────────────────────
const map = L.map('map', { center: [20, 0], zoom: 2, zoomControl: true });

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://openstreetmap.org">OSM</a>',
  subdomains: 'abcd', maxZoom: 19,
}).addTo(map);

const drawnItems = new L.FeatureGroup().addTo(map);
const drawControl = new L.Control.Draw({
  draw: {
    rectangle: { shapeOptions: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05 } },
    polygon:   { shapeOptions: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05 } },
    polyline: false, circle: false, marker: false, circlemarker: false,
  },
  edit: { featureGroup: drawnItems, remove: false, edit: false },
});

// ── Filters ───────────────────────────────────────────────
function getFilters() {
  let dateFrom = null, dateTo = null;
  if (dateSlider) {
    const v = dateSlider.get();
    dateFrom = tsToDate(+v[0]);
    dateTo   = tsToDate(+v[1]);
  }
  const activeBbox = (selectedCountry && selectedCountry.bbox) || aoiBbox;
  return {
    iceye: providerActive.iceye, umbra: providerActive.umbra, capella: providerActive.capella,
    dateFrom, dateTo,
    mode: document.getElementById('mode-filter').value,
    bbox: activeBbox,
  };
}
const tsToDate = ts => new Date(ts).toISOString().slice(0, 10);

// ── Render ─────────────────────────────────────────────────
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
      style: { color, weight: 1, opacity: .8, fillColor: color, fillOpacity: .08 },
    });
    layer.on('click',     () => showDetail(p));
    layer.on('mouseover', function () { this.setStyle({ fillOpacity: .28, weight: 1.5 }); });
    layer.on('mouseout',  function () { this.setStyle({ fillOpacity: .08, weight: 1 }); });
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
  const ring = geom.type === 'Polygon' ? geom.coordinates[0]
             : geom.type === 'MultiPolygon' ? geom.coordinates[0][0] : null;
  if (!ring) return null;
  return [ring.reduce((s,c)=>s+c[0],0)/ring.length, ring.reduce((s,c)=>s+c[1],0)/ring.length];
}

// ── Histogram ──────────────────────────────────────────────
function drawHistogram(counts) {
  const canvas = document.getElementById('histogram');
  const W = canvas.offsetWidth || 240;
  canvas.width = W; const H = 90;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  const providers = ['iceye','umbra','capella'];
  const vals = providers.map(p => counts[p]);
  const max  = Math.max(...vals, 1);
  const barW = Math.floor((W - 32) / 3);
  const gap  = (W - barW * 3) / 4;
  providers.forEach((pid, i) => {
    const x = gap + i * (barW + gap);
    const barH = Math.max(2, Math.round((vals[i] / max) * (H - 28)));
    const y = H - 18 - barH;
    const color = PROVIDER_COLORS[pid];
    ctx.fillStyle = color + '33'; ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.roundRect(x, y, barW, barH, 3); ctx.fill(); ctx.stroke();
    ctx.fillStyle = color; ctx.font = '600 11px system-ui,sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(vals[i], x + barW / 2, y - 4);
    ctx.fillStyle = '#8b949e'; ctx.font = '10px system-ui,sans-serif';
    ctx.fillText(PROVIDER_LABELS[pid], x + barW / 2, H - 4);
  });
}

// ── Popup ──────────────────────────────────────────────────
function makePopup(p) {
  const det = `<button class="popup-btn details-btn" onclick="showDetailById('${esc(p.id)}')">Details</button>`;
  const dl  = p.download    ? `<a class="popup-btn" href="${p.download}" target="_blank">Download</a>` : '';
  const pv  = p.provider_url? `<a class="popup-btn" href="${p.provider_url}" target="_blank">${p.provider_label}</a>` : '';
  return `<div class="popup-provider ${p.provider}">${p.provider_label}</div>
<div class="popup-id">${esc(p.id||'—')}</div>
<div class="popup-date">📅 ${p.date||'Unknown'}</div>
<div class="popup-mode">⚡ ${p.sensor_mode||'—'}</div>
<div class="popup-actions">${det}${dl}${pv}</div>`;
}
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ── Detail panel ───────────────────────────────────────────
window.showDetailById = id => {
  const f = allFeatures.find(f => f.properties.id === id);
  if (f) showDetail(f.properties);
};

function showDetail(p) {
  let thumbHtml = '';
  if (p.thumbnail) {
    thumbHtml = `<img class="detail-thumbnail" src="${p.thumbnail}" referrerpolicy="no-referrer" crossorigin="anonymous" alt="SAR thumbnail" onerror="this.outerHTML='<div class=detail-thumb-placeholder>Preview unavailable</div>'" />`;
  } else {
    thumbHtml = `<div class="detail-thumb-placeholder">No preview available</div>`;
  }

  const rows = [
    ['Date',        p.date           || '—'],
    ['Provider',    p.provider_label || '—'],
    ['Mode',        p.sensor_mode    || '—'],
    ['Polarization',p.polarization   || '—'],
    ['Resolution',  p.resolution != null ? p.resolution + ' m' : '—'],
    ['Collection',  p.collection     || '—'],
  ].map(([k,v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('');

  const dl = p.download
    ? `<a class="detail-action-btn primary" href="${p.download}" target="_blank">Download Asset</a>` : '';
  const pv = p.provider_url
    ? `<a class="detail-action-btn" href="${p.provider_url}" target="_blank">View on ${p.provider_label}</a>` : '';

  document.getElementById('detail-content').innerHTML =
    `${thumbHtml}<div class="detail-provider ${p.provider}">${p.provider_label}</div>
<div class="detail-id">${esc(p.id||'—')}</div>
<table class="detail-table"><tbody>${rows}</tbody></table>
<div class="detail-actions">${dl}${pv}</div>`;
  document.getElementById('detail-panel').classList.remove('hidden');
}

document.getElementById('detail-close').addEventListener('click', () => {
  document.getElementById('detail-panel').classList.add('hidden');
});

// ── Sidebar toggle ─────────────────────────────────────────
document.getElementById('sidebar-toggle').addEventListener('click', () => {
  document.body.classList.toggle('sidebar-collapsed');
  setTimeout(() => map.invalidateSize(), 300);
});

// ── Provider pills ─────────────────────────────────────────
['iceye','umbra','capella'].forEach(pid => {
  document.getElementById(`pill-${pid}`).addEventListener('click', () => {
    providerActive[pid] = !providerActive[pid];
    document.getElementById(`pill-${pid}`).classList.toggle('active', providerActive[pid]);
    render();
  });
});

// ── Sensor mode ─────────────────────────────────────────────
document.getElementById('mode-filter').addEventListener('change', render);

function populateModes(features) {
  const modes = new Set();
  features.forEach(f => { const m = f.properties.sensor_mode; if (m && m !== 'N/A') modes.add(m); });
  const sel = document.getElementById('mode-filter');
  [...modes].sort().forEach(m => { const o = document.createElement('option'); o.value=m; o.textContent=m; sel.appendChild(o); });
}

// ── Date slider ─────────────────────────────────────────────
function initDateSlider(features) {
  const ts = features.map(f => f.properties.date).filter(Boolean)
    .map(d => new Date(d).getTime()).filter(t => !isNaN(t));
  if (!ts.length) return;
  dateMin = Math.min(...ts); dateMax = Math.max(...ts);

  dateSlider = noUiSlider.create(document.getElementById('date-slider'), {
    start: [dateMin, dateMax], connect: true,
    range: { min: dateMin, max: dateMax },
    tooltips: [
      { to: v => new Date(+v).toISOString().slice(0,7) },
      { to: v => new Date(+v).toISOString().slice(0,7) },
    ],
    step: 30 * 24 * 3600 * 1000,
  });

  const update = vals => {
    document.getElementById('date-label-from').textContent = new Date(+vals[0]).toISOString().slice(0,7);
    document.getElementById('date-label-to').textContent   = new Date(+vals[1]).toISOString().slice(0,7);
  };
  dateSlider.on('update', update);
  dateSlider.on('change', () => render());
  update([dateMin, dateMax]);
}

// ── Country picker ──────────────────────────────────────────
const tooltip = document.getElementById('country-tooltip');
const hintBanner = document.getElementById('hint-banner');

function showHint(msg) {
  hintBanner.textContent = msg;
  hintBanner.classList.add('visible');
}
function hideHint() { hintBanner.classList.remove('visible'); }

function setCountryMode(on) {
  countryMode = on;
  document.getElementById('tb-country').classList.toggle('country-on', on);
  if (on) {
    document.body.classList.add('mode-country');
    showHint('Hover and click a country to filter scenes');
    loadCountries();
  } else {
    document.body.classList.remove('mode-country');
    hideHint();
    tooltip.style.display = 'none';
  }
}

document.getElementById('tb-country').addEventListener('click', () => {
  setCountryMode(!countryMode);
});

async function loadCountries() {
  if (countriesLoaded) return;
  try {
    const res  = await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json');
    const topo = await res.json();
    const geojson = topojson.feature(topo, topo.objects.countries);

    countryLayer = L.geoJSON(geojson, {
      style: () => ({ color: 'transparent', weight: 0, fillColor: 'transparent', fillOpacity: 0 }),
      onEachFeature(feat, layer) {
        layer.on('mousemove', e => {
          if (!countryMode) return;
          const name = feat.properties.name || 'Unknown';
          tooltip.textContent = name;
          tooltip.style.display = 'block';
          tooltip.style.left = (e.originalEvent.clientX + 14) + 'px';
          tooltip.style.top  = (e.originalEvent.clientY - 32) + 'px';
          if (hoveredCountry && hoveredCountry !== layer && hoveredCountry !== (selectedCountry && selectedCountry.layer)) {
            hoveredCountry.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
          }
          if (layer !== (selectedCountry && selectedCountry.layer)) {
            layer.setStyle({ fillColor: '#d29922', fillOpacity: 0.15, color: '#d29922', weight: 1 });
          }
          hoveredCountry = layer;
        });
        layer.on('mouseout', () => {
          tooltip.style.display = 'none';
          if (layer !== (selectedCountry && selectedCountry.layer)) {
            layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
          }
          hoveredCountry = null;
        });
        layer.on('click', () => {
          if (!countryMode) return;
          // Deselect old
          if (selectedCountry) {
            selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
          }
          const b = layer.getBounds();
          const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
          // Highlight selected
          layer.setStyle({ fillColor: '#d29922', fillOpacity: 0.12, color: '#d29922', weight: 1.5 });
          selectedCountry = { layer, bbox };
          map.fitBounds(b, { padding: [40,40], maxZoom: 8, duration: 700 });
          setCountryMode(false);
          render();
        });
      },
    }).addTo(map);
    countriesLoaded = true;
  } catch(e) { console.error('Countries failed:', e); }
}

// ── BBox / Polygon draw ─────────────────────────────────────
let activeDrawTool = null;

function startDraw(ToolClass, options, btnId) {
  if (activeDrawTool) { activeDrawTool.disable(); activeDrawTool = null; }
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  map.addControl(drawControl);
  document.body.classList.add('mode-draw');
  showHint('Click to start drawing — double-click to finish');
  const tool = new ToolClass(map, options);
  tool.enable();
  activeDrawTool = tool;
  document.getElementById(btnId).classList.add('active');
}

document.getElementById('tb-bbox').addEventListener('click', () => {
  startDraw(L.Draw.Rectangle, drawControl.options.draw.rectangle, 'tb-bbox');
});

document.getElementById('tb-poly').addEventListener('click', () => {
  startDraw(L.Draw.Polygon, drawControl.options.draw.polygon, 'tb-poly');
});

map.on(L.Draw.Event.CREATED, e => {
  drawnItems.clearLayers();
  drawnItems.addLayer(e.layer);
  const b = e.layer.getBounds();
  aoiBbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  aoiLayer = e.layer;
  // Clear country selection when drawing
  if (selectedCountry) {
    selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
    selectedCountry = null;
  }
  map.removeControl(drawControl);
  activeDrawTool = null;
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  document.body.classList.remove('mode-draw');
  hideHint();
  render();
});

map.on(L.Draw.Event.DRAWSTOP, () => {
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  document.body.classList.remove('mode-draw');
  hideHint();
});

// ── Upload GeoJSON ──────────────────────────────────────────
document.getElementById('tb-upload').addEventListener('change', e => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      const geojson = JSON.parse(ev.target.result);
      drawnItems.clearLayers();
      const layer = L.geoJSON(geojson, {
        style: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05 },
      });
      layer.addTo(drawnItems);
      const b = layer.getBounds();
      aoiBbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
      if (selectedCountry) {
        selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
        selectedCountry = null;
      }
      map.fitBounds(b, { padding: [40,40] });
      render();
    } catch { alert('Invalid GeoJSON file.'); }
  };
  reader.readAsText(file);
  e.target.value = '';
});

// ── Clear all ───────────────────────────────────────────────
document.getElementById('tb-clear').addEventListener('click', clearAll);

function clearAll() {
  drawnItems.clearLayers();
  aoiBbox = null; aoiLayer = null;
  if (selectedCountry) {
    selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
    selectedCountry = null;
  }
  setCountryMode(false);
  render();
}

// ── Reset all filters ───────────────────────────────────────
document.getElementById('reset-btn').addEventListener('click', () => {
  ['iceye','umbra','capella'].forEach(pid => {
    providerActive[pid] = true;
    document.getElementById(`pill-${pid}`).classList.add('active');
  });
  if (dateSlider) dateSlider.set([dateMin, dateMax]);
  document.getElementById('mode-filter').value = '';
  clearAll();
});

// ── STAC export ─────────────────────────────────────────────
document.getElementById('export-stac-btn').addEventListener('click', () => {
  const f = getFilters();
  const visible = allFeatures.filter(feat => {
    const p = feat.properties;
    if (!f[p.provider]) return false;
    if (f.dateFrom && p.date && p.date < f.dateFrom) return false;
    if (f.dateTo   && p.date && p.date > f.dateTo)   return false;
    if (f.mode && p.sensor_mode && p.sensor_mode !== f.mode) return false;
    if (f.bbox) { const c = centroid(feat.geometry); if (!c) return false; const [w,s,e,n]=f.bbox; if(c[0]<w||c[0]>e||c[1]<s||c[1]>n) return false; }
    return true;
  });
  const collection = {
    type: 'FeatureCollection', stac_version: '1.0.0',
    id: 'open-sar-triad-export',
    description: 'Exported SAR scenes from open-sar-triad',
    exported_at: new Date().toISOString(),
    source: 'https://github.com/Jack-Hayes/commerical-sar-stac',
    features: visible,
  };
  const blob = new Blob([JSON.stringify(collection, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `open-sar-triad-${new Date().toISOString().slice(0,10)}.json`;
  a.click(); URL.revokeObjectURL(url);
});

// ── Load data ────────────────────────────────────────────────
fetch('data/scenes.geojson')
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(geojson => {
    allFeatures = geojson.features || [];
    const totals = { iceye: 0, umbra: 0, capella: 0 };
    allFeatures.forEach(f => { const p = f.properties.provider; if (p in totals) totals[p]++; });

    document.getElementById('pill-cnt-iceye').textContent   = totals.iceye;
    document.getElementById('pill-cnt-umbra').textContent   = totals.umbra;
    document.getElementById('pill-cnt-capella').textContent = totals.capella;

    populateModes(allFeatures);
    initDateSlider(allFeatures);
    document.getElementById('loading').classList.add('hidden');
    render();
  })
  .catch(err => {
    document.getElementById('loading').innerHTML =
      `<p style="color:#FF6B35">No scene data found.<br>Run <code>scripts/fetch_catalog.py</code> to generate it.</p>`;
  });
