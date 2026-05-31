/* open-sar-triad */

const PROVIDER_COLORS = { iceye: '#00FF87', umbra: '#00C9FF', capella: '#FF6B35' };
const PROVIDER_LABELS = { iceye: 'ICEYE', umbra: 'Umbra', capella: 'Capella' };

let allFeatures     = [];
let activeLayers    = {};
let aoiBbox         = null;
let countryLayer    = null;
let countriesLoaded = false;
let countryMode     = false;
let hoveredCountry  = null;
let selectedCountry = null;   // { layer, bbox, name }
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

// ── Antimeridian helpers (from bboxer) ─────────────────────
function unwrapAntimeridian(geom) {
  if (!geom) return;
  const fixRing = ring => {
    for (let i = 1; i < ring.length; i++) {
      const d = ring[i][0] - ring[i-1][0];
      if (d > 180)  ring[i][0] -= 360;
      else if (d < -180) ring[i][0] += 360;
    }
  };
  if (geom.type === 'Polygon')      geom.coordinates.forEach(fixRing);
  else if (geom.type === 'MultiPolygon') geom.coordinates.forEach(p => p.forEach(fixRing));
}

function flatCoords(geom) {
  if (!geom) return [];
  if (geom.type === 'Polygon')      return geom.coordinates.flat();
  if (geom.type === 'MultiPolygon') return geom.coordinates.flat(2);
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
  if (dateSlider) {
    const v = dateSlider.get();
    dateFrom = tsToDate(+v[0]);
    dateTo   = tsToDate(+v[1]);
  }
  const bbox = (selectedCountry && selectedCountry.bbox) || aoiBbox;
  return {
    iceye: providerActive.iceye, umbra: providerActive.umbra, capella: providerActive.capella,
    dateFrom, dateTo,
    mode: document.getElementById('mode-filter').value,
    bbox,
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
  document.getElementById('total-vis').textContent = total;
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
function drawHistogram(counts, label) {
  const canvas = document.getElementById('histogram');
  const W = canvas.offsetWidth || 240; canvas.width = W; const H = 90;
  const ctx = canvas.getContext('2d'); ctx.clearRect(0, 0, W, H);
  const providers = ['iceye','umbra','capella'];
  const vals = providers.map(p => counts[p]);
  const max  = Math.max(...vals, 1);
  const barW = Math.floor((W - 32) / 3);
  const gap  = (W - barW * 3) / 4;

  // Update section title
  const titleEl = document.getElementById('hist-title');
  titleEl.textContent = label ? `Coverage — ${label}` : 'Scene Coverage';

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
const esc = s => String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ── Detail panel ───────────────────────────────────────────
window.showDetailById = id => {
  const f = allFeatures.find(f => f.properties.id === id);
  if (f) showDetail(f.properties);
};

function proxyThumb(url, provider) {
  if (!url) return null;
  // ICEYE S3 has no CORS headers — route through weserv.nl (free CDN proxy)
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
  document.body.classList.remove('detail-collapsed');
  document.getElementById('detail-toggle').classList.remove('hidden');
}

// ── Sidebar / detail panel toggles ─────────────────────────
document.getElementById('sidebar-toggle').addEventListener('click', () => {
  document.body.classList.toggle('sidebar-collapsed');
  setTimeout(() => map.invalidateSize(), 300);
});

document.getElementById('detail-toggle').addEventListener('click', () => {
  document.body.classList.toggle('detail-collapsed');
  setTimeout(() => map.invalidateSize(), 220);
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

  // Default start: 2 years back from max, or dateMin if data is shorter
  const twoYearsBack = dateMax - 2 * 365.25 * 24 * 3600 * 1000;
  const defaultStart = Math.max(dateMin, twoYearsBack);

  dateSlider = noUiSlider.create(document.getElementById('date-slider'), {
    start: [defaultStart, dateMax], connect: true,
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
  update([defaultStart, dateMax]);
}

// ── Country picker ──────────────────────────────────────────
const tooltip   = document.getElementById('country-tooltip');
const hintBanner = document.getElementById('hint-banner');

function showHint(msg) { hintBanner.textContent = msg; hintBanner.classList.add('visible'); }
function hideHint()    { hintBanner.classList.remove('visible'); }

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

document.getElementById('tb-country').addEventListener('click', () => setCountryMode(!countryMode));

// ISO numeric → name (subset of commonly needed countries)
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
    const res   = await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json');
    const topo  = await res.json();
    const geojson = topojson.feature(topo, topo.objects.countries);

    // Assign names + unwrap antimeridian for every feature
    geojson.features.forEach(f => {
      f.properties = f.properties || {};
      if (!f.properties.name) f.properties.name = ISO_NAMES[+f.id] || `Country ${f.id}`;
      unwrapAntimeridian(f.geometry);
      f._bbox = bboxFromGeometry(f.geometry); // cache bbox
    });
    // Drop Antarctica
    geojson.features = geojson.features.filter(f => +f.id !== 10);

    countryLayer = L.geoJSON(geojson, {
      style: () => ({ color: 'transparent', weight: 0, fillColor: 'transparent', fillOpacity: 0 }),
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
            layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
          }
          hoveredCountry = null;
        });
        layer.on('click', () => {
          if (!countryMode) return;
          if (selectedCountry) {
            selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
          }
          const bbox = feat._bbox;
          layer.setStyle({ fillColor: '#d29922', fillOpacity: 0.1, color: '#d29922', weight: 1.5 });
          selectedCountry = { layer, bbox, name: feat.properties.name };

          // Fit bounds using safe leaflet bounds (clamped to ±180)
          const safeBounds = [
            [Math.max(-85, bbox[1]), Math.max(-180, bbox[0])],
            [Math.min(85,  bbox[3]), Math.min(180,  bbox[2])],
          ];
          map.fitBounds(safeBounds, { padding: [40,40], maxZoom: 8, duration: 700 });
          setCountryMode(false);
          render();
          // Update histogram with country label
          updateCountryHistogram(feat.properties.name, bbox);
        });
      },
    }).addTo(map);
    countriesLoaded = true;
  } catch(e) { console.error('Countries failed:', e); }
}

function updateCountryHistogram(name, bbox) {
  const counts = { iceye: 0, umbra: 0, capella: 0 };
  const f = getFilters();
  allFeatures.forEach(feat => {
    const p = feat.properties;
    if (!providerActive[p.provider]) return;
    if (f.dateFrom && p.date && p.date < f.dateFrom) return;
    if (f.dateTo   && p.date && p.date > f.dateTo)   return;
    if (f.mode && p.sensor_mode && p.sensor_mode !== f.mode) return;
    const c = centroid(feat.geometry);
    if (!c) return;
    const [w,s,e,n] = bbox;
    if (c[0] >= w && c[0] <= e && c[1] >= s && c[1] <= n) counts[p.provider]++;
  });
  drawHistogram(counts, name);
}

// ── Draw tools ──────────────────────────────────────────────
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
    selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
    selectedCountry = null;
    drawHistogram({ iceye:0, umbra:0, capella:0 });
  }
  map.removeControl(drawControl); activeDrawTool = null;
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  document.body.classList.remove('mode-draw'); hideHint(); render();
});

map.on(L.Draw.Event.DRAWSTOP, () => {
  document.querySelectorAll('.tb-btn').forEach(b => b.classList.remove('active'));
  document.body.classList.remove('mode-draw'); hideHint();
});

// ── Upload GeoJSON ──────────────────────────────────────────
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
        selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
        selectedCountry = null;
      }
      map.fitBounds(b, { padding: [40,40] }); render();
    } catch { alert('Invalid GeoJSON file.'); }
  };
  reader.readAsText(file); e.target.value = '';
});

// ── Clear all ───────────────────────────────────────────────
document.getElementById('tb-clear').addEventListener('click', clearAll);
function clearAll() {
  drawnItems.clearLayers(); aoiBbox = null;
  if (selectedCountry) {
    selectedCountry.layer.setStyle({ fillColor: 'transparent', fillOpacity: 0, color: 'transparent', weight: 0 });
    selectedCountry = null;
  }
  setCountryMode(false);
  drawHistogram({ iceye:0, umbra:0, capella:0 });
  render();
}

// ── Reset ───────────────────────────────────────────────────
document.getElementById('reset-btn').addEventListener('click', () => {
  ['iceye','umbra','capella'].forEach(pid => {
    providerActive[pid] = true;
    document.getElementById(`pill-${pid}`).classList.add('active');
  });
  if (dateSlider) {
    const twoYearsBack = Math.max(dateMin, dateMax - 2 * 365.25 * 24 * 3600 * 1000);
    dateSlider.set([twoYearsBack, dateMax]);
  }
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
  a.href=url; a.download=`open-sar-triad-${new Date().toISOString().slice(0,10)}.json`;
  a.click(); URL.revokeObjectURL(url);
});

// ── Load data ────────────────────────────────────────────────
fetch('data/scenes.geojson')
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(geojson => {
    allFeatures = geojson.features || [];
    populateModes(allFeatures);
    initDateSlider(allFeatures);
    document.getElementById('loading').classList.add('hidden');
    render();
  })
  .catch(() => {
    document.getElementById('loading').innerHTML =
      `<p style="color:#FF6B35">No scene data found.<br>Run <code>scripts/fetch_catalog.py</code> to generate it.</p>`;
  });
