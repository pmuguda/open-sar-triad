/* open-sar-triad — app.js */

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
const map = L.map('map', { center: [20, 0], zoom: 2, zoomControl: false });
L.control.zoom({ position: 'bottomleft' }).addTo(map);
L.control.scale({ position: 'bottomleft', imperial: false, maxWidth: 140 }).addTo(map);

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

// ── Drawn items (Leaflet-Draw) ─────────────────────────────
const drawnItems = new L.FeatureGroup().addTo(map);
const drawControl = new L.Control.Draw({
  draw: {
    rectangle: { shapeOptions: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05 } },
    polygon:   { shapeOptions: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05 } },
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

// ── Render ─────────────────────────────────────────────────
function render() {
  Object.values(activeLayers).forEach(l => map.removeLayer(l));
  activeLayers = {};
  const counts = { iceye: 0, umbra: 0, capella: 0 };
  const visible = getVisibleFeatures();

  visible.forEach(feat => {
    const p = feat.properties;
    const color = PROVIDER_COLORS[p.provider];
    const geomCopy = JSON.parse(JSON.stringify(feat.geometry));
    unwrapAntimeridian(geomCopy);

    const layer = L.geoJSON({ type: 'Feature', geometry: geomCopy, properties: p }, {
      style: { color, weight: 1, opacity: 0.85, fillColor: color, fillOpacity: 0.15 },
      interactive: !countryMode,
      pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 5, color, weight: 1, fillColor: color, fillOpacity: 0.6 }),
    });
    if (!countryMode) {
      layer.on('click',     () => showDetail(p));
      layer.on('mouseover', function () { this.setStyle({ fillOpacity: 0.45, weight: 2 }); });
      layer.on('mouseout',  function () { this.setStyle({ fillOpacity: 0.15, weight: 1 }); });
      layer.bindPopup(makePopup(p), { maxWidth: 280 });
    }
    layer.addTo(map);
    activeLayers[p.id] = layer;
    counts[p.provider]++;
  });

  const total = counts.iceye + counts.umbra + counts.capella;
  const visEl = document.getElementById('visCount');
  if (visEl) visEl.textContent = total.toLocaleString('en-US');
  updateCoverage(counts, total);
  updateModes();
  updateDownloadCount(visible);
  updateTimelineHistogram();
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
function updateModes() {
  const modes = {};
  getVisibleFeatures().forEach(feat => {
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
  const det = `<button class="popup-btn details-btn" onclick="showDetailById('${esc(p.id)}')">Details</button>`;
  const dl  = dlUrl ? `<a class="popup-btn" href="${esc(dlUrl)}" target="_blank" rel="noopener noreferrer">Download</a>` : '';
  const pv  = pvUrl ? `<a class="popup-btn" href="${esc(pvUrl)}" target="_blank" rel="noopener noreferrer">${esc(p.provider_label)}</a>` : '';
  return `<div class="popup-provider ${esc(p.provider)}">${esc(p.provider_label)}</div>
<div class="popup-id">${esc(p.id||'—')}</div>
<div class="popup-date">📅 ${esc(p.date||'Unknown')}</div>
<div class="popup-mode">⚡ ${esc(p.sensor_mode||'—')}</div>
<div class="popup-actions">${det}${dl}${pv}</div>`;
}

const esc = s => String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

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

function proxyThumb(url, provider) {
  if (!safeUrl(url)) return null;
  if (provider === 'iceye') {
    return `https://images.weserv.nl/?url=${encodeURIComponent(url)}&w=600&output=jpg&q=80`;
  }
  return url;
}

function showDetail(p) {
  const thumbSrc = proxyThumb(p.thumbnail, p.provider);
  const noPreviewMsg = p.provider === 'umbra'
    ? 'Umbra open data does not include preview images'
    : 'Preview unavailable';
  const thumbHtml = thumbSrc
    ? `<img class="detail-thumbnail" src="${thumbSrc}" alt="SAR thumbnail" onerror="this.outerHTML='<div class=detail-thumb-placeholder>${noPreviewMsg}</div>'" />`
    : `<div class="detail-thumb-placeholder">${noPreviewMsg}</div>`;

  const rows = [
    ['Date',            p.date             || '—'],
    ['Provider',        p.provider_label   || '—'],
    ['Mode',            p.sensor_mode      || '—'],
    ['Polarization',    p.polarization     || '—'],
    ['Resolution',      p.resolution != null ? p.resolution + ' m' : '—'],
    ['Incidence angle', p.incidence_angle != null ? p.incidence_angle + '°' : '—'],
    ['Off-nadir',       p.off_nadir != null ? p.off_nadir + '°' : '—'],
    ['Collection',      p.collection       || '—'],
  ].filter(([,v]) => v !== '—')
   .map(([k,v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('');

  const dlUrl = safeUrl(p.download);
  const pvUrl = safeUrl(p.provider_url);
  const dl = dlUrl
    ? `<a class="detail-action-btn primary" href="${esc(dlUrl)}" target="_blank" rel="noopener noreferrer">Download Asset</a>` : '';
  const pv = pvUrl
    ? `<a class="detail-action-btn" href="${esc(pvUrl)}" target="_blank" rel="noopener noreferrer">View on ${esc(p.provider_label)}</a>` : '';

  document.getElementById('detail-content').innerHTML =
    `${thumbHtml}<div class="detail-provider ${esc(p.provider)}">${esc(p.provider_label)}</div>
<div class="detail-id">${esc(p.id||'—')}</div>
<table class="detail-table"><tbody>${rows}</tbody></table>
<div class="detail-actions">${dl}${pv}</div>`;

  document.getElementById('detail-panel').classList.remove('hidden');
  document.body.classList.remove('detail-collapsed');
  document.getElementById('detail-toggle').classList.remove('hidden');
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

async function loadCountries() {
  if (countriesLoaded) return;
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
      style: () => ({ color: 'transparent', weight: 0, fillColor: '#ffffff', fillOpacity: 0.001 }),
      onEachFeature(feat, layer) {
        layer.on('mousemove', e => {
          if (!countryMode) return;
          tooltip.textContent = feat.properties.name;
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

    if (pendingCountryRestore) {
      const name = pendingCountryRestore;
      pendingCountryRestore = null;
      countryLayer.eachLayer(l => {
        if (l.feature && l.feature.properties.name === name) {
          l.setStyle({ fillColor: '#d29922', fillOpacity: 0.1, color: '#d29922', weight: 1.5 });
          selectedCountry = { layer: l, bbox: l.feature._bbox, name, geometry: l.feature.geometry };
          document.getElementById('tb-country').classList.add('country-active');
          showHint(`Filtered by ${name} · click 🌐 to switch country · × to clear`);
          render();
        }
      });
    }
  } catch(e) { console.error('Countries failed:', e); }
}

// ── Draw tools ─────────────────────────────────────────────
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
      const layer = L.geoJSON(geojson, { style: { color: '#3fb950', weight: 1.5, fillOpacity: 0.05 } });
      layer.addTo(drawnItems);
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
  drawnItems.clearLayers(); aoiBbox = null;
  if (selectedCountry) {
    selectedCountry.layer.setStyle({ fillColor: '#ffffff', fillOpacity: 0.001, color: 'transparent', weight: 0 });
    selectedCountry = null;
  }
  document.getElementById('tb-country').classList.remove('country-active');
  setCountryMode(false);
  render();
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
    return name || fallbackId;
  } catch { return fallbackId; }
}

document.querySelector('[data-export="script"]').addEventListener('click', () => {
  const visible = getVisibleFeatures();
  if (!visible.length) { showToast('No scenes match current filters'); return; }

  const byProvider = { iceye: [], umbra: [], capella: [] };
  visible.forEach(feat => {
    const p = feat.properties;
    if (p.download && byProvider[p.provider]) byProvider[p.provider].push(p);
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
      lines.push(`dl "${p.download}" "${pid}/${fname}"`);
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

// ── Draggable AOI toolbar ──────────────────────────────────
(function () {
  const toolbar = document.getElementById('aoi-toolbar');
  let dragging = false, ox = 0, oy = 0;

  const saved = JSON.parse(localStorage.getItem('aoi-toolbar-pos') || 'null');
  if (saved && typeof saved.top === 'number' && typeof saved.left === 'number') {
    toolbar.style.right = 'auto';
    toolbar.style.top   = saved.top  + 'px';
    toolbar.style.left  = saved.left + 'px';
  }

  toolbar.addEventListener('mousedown', e => {
    if (e.target.closest('.tb-btn') || e.target.closest('.tb-divider')) return;
    dragging = true;
    const rect = toolbar.getBoundingClientRect();
    ox = e.clientX - rect.left;
    oy = e.clientY - rect.top;
    toolbar.style.cursor = 'grabbing';
    toolbar.style.right  = 'auto';
    e.preventDefault();
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const vp = document.getElementById('viewport').getBoundingClientRect();
    let left = e.clientX - vp.left - ox;
    let top  = e.clientY - vp.top  - oy;
    left = Math.max(0, Math.min(left, vp.width  - toolbar.offsetWidth));
    top  = Math.max(0, Math.min(top,  vp.height - toolbar.offsetHeight));
    toolbar.style.left = left + 'px';
    toolbar.style.top  = top  + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    toolbar.style.cursor = '';
    localStorage.setItem('aoi-toolbar-pos', JSON.stringify({
      left: parseInt(toolbar.style.left),
      top:  parseInt(toolbar.style.top),
    }));
  });

  toolbar.addEventListener('dblclick', () => {
    toolbar.style.left  = '';
    toolbar.style.top   = '56px';
    toolbar.style.right = '60px';
    localStorage.removeItem('aoi-toolbar-pos');
  });
})();

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
    if (parts.length === 4 && parts.every(n => !isNaN(n))) aoiBbox = parts;
  }

  const country = p.get('country');
  if (country) pendingCountryRestore = country;

  const lat = parseFloat(p.get('lat'));
  const lng = parseFloat(p.get('lng'));
  const z   = parseInt(p.get('z'), 10);
  if (!isNaN(lat) && !isNaN(lng) && !isNaN(z)) map.setView([lat, lng], z);

  // Date — deferred until initTimeline() builds MONTHS
  window._pendingDateRestore = { from: p.get('from'), to: p.get('to') };
}

// ── Load data ──────────────────────────────────────────────
fetch('data/scenes.geojson')
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(geojson => {
    allFeatures = geojson.features || [];
    populateModes(allFeatures);
    restoreState();
    initTimeline(allFeatures);
    document.getElementById('loading').classList.add('hidden');
    dataLoaded = true;
    render();
  })
  .catch(() => {
    document.getElementById('loading').innerHTML =
      `<p style="color:var(--capella)">No scene data found.<br>Run <code>scripts/fetch_catalog.py</code> to generate it.</p>`;
  });
