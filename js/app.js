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
let selectedCountry = null;   // { layer, bbox, name, geometry }
let dateSlider      = null;
let dateMin = 0, dateMax = 0;
const providerActive = { iceye: true, umbra: true, capella: true };
let orbitFilter = '';   // '' | 'ascending' | 'descending'
let lookFilter  = '';   // '' | 'left' | 'right'
let dataLoaded  = false;
let pendingCountryRestore = null;

// ── Map ────────────────────────────────────────────────────
const map = L.map('map', { center: [20, 0], zoom: 2, zoomControl: false });
L.control.zoom({ position: 'bottomleft' }).addTo(map);
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
  const bbox            = (selectedCountry && selectedCountry.bbox) || aoiBbox;
  const countryGeometry = selectedCountry ? selectedCountry.geometry : null;
  return {
    iceye: providerActive.iceye, umbra: providerActive.umbra, capella: providerActive.capella,
    dateFrom, dateTo,
    mode:  document.getElementById('mode-filter').value,
    bbox, countryGeometry,
    orbit: orbitFilter,
    look:  lookFilter,
  };
}
const tsToDate = ts => new Date(ts).toISOString().slice(0, 10);

// ── Render ─────────────────────────────────────────────────
function render() {
  Object.values(activeLayers).forEach(l => map.removeLayer(l));
  activeLayers = {};
  const counts = { iceye: 0, umbra: 0, capella: 0 };
  const visible = getVisibleFeatures();
  updateDownloadCount(visible);

  visible.forEach(feat => {
    const p = feat.properties;
    const color = PROVIDER_COLORS[p.provider];
    const layer = L.geoJSON(feat, {
      style: { color, weight: 1, opacity: .8, fillColor: color, fillOpacity: .08 },
      interactive: !countryMode,  // pass-through clicks when country mode active
    });
    if (!countryMode) {
      layer.on('click',     () => showDetail(p));
      layer.on('mouseover', function () { this.setStyle({ fillOpacity: .28, weight: 1.5 }); });
      layer.on('mouseout',  function () { this.setStyle({ fillOpacity: .08, weight: 1 }); });
      layer.bindPopup(makePopup(p), { maxWidth: 280 });
    }
    layer.addTo(map);
    activeLayers[p.id] = layer;
    counts[p.provider]++;
  });

  const total = counts.iceye + counts.umbra + counts.capella;
  document.getElementById('total-vis').textContent = total;
  drawHistogram(counts, selectedCountry ? selectedCountry.name : null);
  drawModeBreakdown();
  if (dataLoaded) history.replaceState(null, '', '#' + encodeState());
}

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
  if (geom.type === 'Polygon')      return inRing(geom.coordinates[0]);
  if (geom.type === 'MultiPolygon') return geom.coordinates.some(p => inRing(p[0]));
  return false;
}

// ── Histogram ──────────────────────────────────────────────
function drawHistogram(counts, label) {
  const canvas = document.getElementById('histogram');
  const W = canvas.offsetWidth || 240;
  const H = 110;
  canvas.width = W;
  canvas.height = H;
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
    const barH = Math.max(2, Math.round((vals[i] / max) * (H - 42)));
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

// ── Mode breakdown (per-mode stacked bar) ──────────────────
function drawModeBreakdown() {
  const modes = {};
  getVisibleFeatures().forEach(feat => {
    const p = feat.properties;
    const m = (p.sensor_mode || 'unknown').toLowerCase();
    if (!modes[m]) modes[m] = { iceye: 0, umbra: 0, capella: 0, total: 0 };
    modes[m][p.provider]++;
    modes[m].total++;
  });

  const container = document.getElementById('mode-stats');
  const sorted = Object.entries(modes).sort((a, b) => b[1].total - a[1].total);

  if (!sorted.length) {
    container.innerHTML = '<div style="font-size:11px;color:var(--muted)">No scenes match filters</div>';
    return;
  }

  const maxTotal = Math.max(...sorted.map(([, v]) => v.total));

  container.innerHTML = sorted.map(([name, v]) => {
    const widthPct = (v.total / maxTotal) * 100;
    const segs = ['iceye', 'umbra', 'capella'].map(pid => {
      if (!v[pid]) return '';
      const pct = (v[pid] / v.total) * 100;
      const label = pct > 14 ? v[pid] : '';
      return `<div class="mode-bar-seg ${pid}" style="flex:${v[pid]}" title="${PROVIDER_LABELS[pid]}: ${v[pid]}">${label}</div>`;
    }).join('');

    return `<div class="mode-row">
      <div class="mode-row-label">
        <span class="mode-name">${esc(name.replace(/_/g, ' '))}</span>
        <span class="mode-total">${v.total.toLocaleString()}</span>
      </div>
      <div class="mode-bar" style="width:${widthPct}%; min-width: 40px">${segs}</div>
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
  features.forEach(f => {
    const m = f.properties.sensor_mode;
    if (m && m.toLowerCase() !== 'n/a') modes.add(m.toLowerCase());
  });
  const sel = document.getElementById('mode-filter');
  [...modes].sort().forEach(m => {
    const o = document.createElement('option');
    o.value = m;
    o.textContent = m.replace(/_/g, ' ');
    sel.appendChild(o);
  });
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

  // Apply date range from URL hash if present
  const dr = window._pendingDateRestore;
  if (dr && dr.from && dr.to) {
    const ts0 = new Date(dr.from).getTime();
    const ts1 = new Date(dr.to).getTime();
    if (!isNaN(ts0) && !isNaN(ts1) && ts0 >= dateMin && ts1 <= dateMax) {
      dateSlider.set([ts0, ts1]);
    }
    window._pendingDateRestore = null;
  }

  update(dateSlider.get());
}

// ── Country picker ──────────────────────────────────────────
const tooltip   = document.getElementById('country-tooltip');
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

    // Restore country filter from URL hash if pending
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
  document.getElementById('tb-country').classList.remove('country-active');
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
  orbitFilter = '';
  lookFilter  = '';
  ['orbit-pills', 'look-pills'].forEach(id => {
    document.querySelectorAll(`#${id} .geo-pill`).forEach((p, i) => p.classList.toggle('active', i === 0));
  });
  clearAll();
});

// ── STAC export ─────────────────────────────────────────────
document.getElementById('export-stac-btn').addEventListener('click', () => {
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
  a.href=url; a.download=`open-sar-triad-${new Date().toISOString().slice(0,10)}.json`;
  a.click(); URL.revokeObjectURL(url);
});

// ── Download script ──────────────────────────────────────────
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

function fileNameFromUrl(url, fallbackId) {
  try {
    const name = new URL(url).pathname.split('/').pop();
    return name || fallbackId;
  } catch { return fallbackId; }
}

document.getElementById('download-script-btn').addEventListener('click', () => {
  const visible = getVisibleFeatures();
  if (!visible.length) {
    alert('No scenes match the current filters.');
    return;
  }

  const byProvider = { iceye: [], umbra: [], capella: [] };
  visible.forEach(feat => {
    const p = feat.properties;
    if (p.download && byProvider[p.provider]) byProvider[p.provider].push(p);
  });

  const counts = Object.fromEntries(
    Object.entries(byProvider).map(([k, v]) => [k, v.length])
  );
  const total = counts.iceye + counts.umbra + counts.capella;
  const date  = new Date().toISOString().slice(0, 10);

  const lines = [
    '#!/usr/bin/env bash',
    `# open-sar-triad download script — generated ${new Date().toISOString()}`,
    `# Total: ${total} scenes  (ICEYE: ${counts.iceye}, Umbra: ${counts.umbra}, Capella: ${counts.capella})`,
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
});

// Update download count label whenever render() runs
function updateDownloadCount(visible) {
  const el = document.getElementById('download-count');
  if (!el) return;
  const withUrl = visible.filter(f => f.properties.download).length;
  el.textContent = withUrl ? `${withUrl} scene${withUrl !== 1 ? 's' : ''} with download links` : '';
}

// ── Draggable AOI toolbar ─────────────────────────────────────
(function () {
  const toolbar = document.getElementById('aoi-toolbar');
  let dragging = false, ox = 0, oy = 0;

  // Restore saved position
  const saved = JSON.parse(localStorage.getItem('aoi-toolbar-pos') || 'null');
  if (saved && typeof saved.top === 'number' && typeof saved.left === 'number') {
    toolbar.style.right  = 'auto';
    toolbar.style.top    = saved.top  + 'px';
    toolbar.style.left   = saved.left + 'px';
  }

  toolbar.addEventListener('mousedown', e => {
    // Only drag on the toolbar background, not button clicks
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
    const mapArea = document.getElementById('map-area').getBoundingClientRect();
    let left = e.clientX - mapArea.left - ox;
    let top  = e.clientY - mapArea.top  - oy;
    // Clamp within map-area
    left = Math.max(0, Math.min(left, mapArea.width  - toolbar.offsetWidth));
    top  = Math.max(0, Math.min(top,  mapArea.height - toolbar.offsetHeight));
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

  // Double-click to reset position
  toolbar.addEventListener('dblclick', () => {
    toolbar.style.left  = '';
    toolbar.style.top   = '12px';
    toolbar.style.right = '14px';
    localStorage.removeItem('aoi-toolbar-pos');
  });
})();

// ── Geometry filters (orbit state / look direction) ───────────
function initGeoPills(groupId, onChange) {
  document.querySelectorAll(`#${groupId} .geo-pill`).forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll(`#${groupId} .geo-pill`).forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      onChange(pill.dataset.val);
      render();
    });
  });
}
initGeoPills('orbit-pills', val => { orbitFilter = val; });
initGeoPills('look-pills',  val => { lookFilter  = val; });

// ── Collapsible sidebar trays ─────────────────────────────────
document.querySelectorAll('.tray-header').forEach(hdr => {
  hdr.addEventListener('click', () => {
    hdr.closest('.sidebar-tray').classList.toggle('tray-collapsed');
  });
});

// ── Shareable URL state ───────────────────────────────────────
function encodeState() {
  const p = new URLSearchParams();
  const hidden = ['iceye','umbra','capella'].filter(id => !providerActive[id]);
  if (hidden.length) p.set('hide', hidden.join(','));
  if (dateSlider) {
    const v = dateSlider.get();
    p.set('from', tsToDate(+v[0]));
    p.set('to',   tsToDate(+v[1]));
  }
  const mode = document.getElementById('mode-filter').value;
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
      document.getElementById(`pill-${id}`)?.classList.remove('active');
    }
  });

  // Geo-pills helper
  const setGeopill = (groupId, val) => {
    document.querySelectorAll(`#${groupId} .geo-pill`).forEach(btn => {
      btn.classList.toggle('active', btn.dataset.val === val);
    });
  };

  const orbit = p.get('orbit');
  if (orbit) { orbitFilter = orbit; setGeopill('orbit-pills', orbit); }

  const look = p.get('look');
  if (look)  { lookFilter  = look;  setGeopill('look-pills',  look);  }

  const mode = p.get('mode');
  if (mode) document.getElementById('mode-filter').value = mode;

  // AOI bbox (drawn)
  const bbox = p.get('bbox');
  if (bbox) {
    const parts = bbox.split(',').map(Number);
    if (parts.length === 4 && parts.every(n => !isNaN(n))) aoiBbox = parts;
  }

  // Country — deferred until loadCountries() finishes
  const country = p.get('country');
  if (country) pendingCountryRestore = country;

  // Map view
  const lat = parseFloat(p.get('lat'));
  const lng = parseFloat(p.get('lng'));
  const z   = parseInt(p.get('z'), 10);
  if (!isNaN(lat) && !isNaN(lng) && !isNaN(z)) map.setView([lat, lng], z);

  // Date — deferred until initDateSlider() runs
  window._pendingDateRestore = { from: p.get('from'), to: p.get('to') };
}

document.getElementById('copy-link-btn').addEventListener('click', () => {
  history.replaceState(null, '', '#' + encodeState());
  navigator.clipboard.writeText(window.location.href).then(() => {
    const btn = document.getElementById('copy-link-btn');
    const orig = btn.innerHTML;
    btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> Copied!';
    btn.style.color = 'var(--accent)';
    setTimeout(() => { btn.innerHTML = orig; btn.style.color = ''; }, 2000);
  });
});

// ── Load data ────────────────────────────────────────────────
fetch('data/scenes.geojson')
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(geojson => {
    allFeatures = geojson.features || [];
    populateModes(allFeatures);
    restoreState();          // parse hash before slider init so date gets applied
    initDateSlider(allFeatures);
    document.getElementById('loading').classList.add('hidden');
    dataLoaded = true;
    render();
  })
  .catch(() => {
    document.getElementById('loading').innerHTML =
      `<p style="color:#FF6B35">No scene data found.<br>Run <code>scripts/fetch_catalog.py</code> to generate it.</p>`;
  });
