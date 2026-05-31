/* open-sar-triad — main app */

const PROVIDER_COLORS = {
  iceye:   '#00FF87',
  umbra:   '#00C9FF',
  capella: '#FF6B35',
};

let allFeatures = [];
let activeLayers = {};       // id → leaflet layer
let drawnBbox = null;
let drawControl = null;
let drawnItems = null;
let isDrawing = false;

// ── Map init ────────────────────────────────────────────────
const map = L.map('map', {
  center: [20, 0],
  zoom: 2,
  zoomControl: true,
  attributionControl: true,
});

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://openstreetmap.org">OpenStreetMap</a>',
  subdomains: 'abcd',
  maxZoom: 19,
}).addTo(map);

// Drawn items layer
drawnItems = new L.FeatureGroup().addTo(map);

drawControl = new L.Control.Draw({
  draw: {
    rectangle: {
      shapeOptions: { color: '#00C9FF', weight: 1.5, fillOpacity: 0.05 },
    },
    polyline: false, polygon: false, circle: false, marker: false, circlemarker: false,
  },
  edit: { featureGroup: drawnItems, remove: false, edit: false },
});

// ── Filters state ───────────────────────────────────────────
function getFilters() {
  return {
    iceye:    document.getElementById('tog-iceye').checked,
    umbra:    document.getElementById('tog-umbra').checked,
    capella:  document.getElementById('tog-capella').checked,
    dateFrom: document.getElementById('date-from').value,
    dateTo:   document.getElementById('date-to').value,
    mode:     document.getElementById('mode-filter').value.trim().toLowerCase(),
    bbox:     drawnBbox,
  };
}

// ── Render ──────────────────────────────────────────────────
function render() {
  const f = getFilters();

  // Remove existing layers
  Object.values(activeLayers).forEach(l => map.removeLayer(l));
  activeLayers = {};

  const counts = { iceye: 0, umbra: 0, capella: 0 };

  allFeatures.forEach(feature => {
    const p = feature.properties;
    if (!f[p.provider]) return;
    if (f.dateFrom && p.date && p.date < f.dateFrom) return;
    if (f.dateTo   && p.date && p.date > f.dateTo)   return;
    if (f.mode && p.sensor_mode && !p.sensor_mode.toLowerCase().includes(f.mode)) return;
    if (f.bbox) {
      const [minLng, minLat, maxLng, maxLat] = f.bbox;
      const center = getCentroid(feature.geometry);
      if (!center) return;
      if (center[0] < minLng || center[0] > maxLng || center[1] < minLat || center[1] > maxLat) return;
    }

    const color = PROVIDER_COLORS[p.provider];
    const layer = L.geoJSON(feature, {
      style: {
        color,
        weight: 1,
        opacity: 0.8,
        fillColor: color,
        fillOpacity: 0.08,
        className: 'scene-polygon',
      },
    });

    layer.on('click', () => showDetail(p));
    layer.on('mouseover', function () {
      this.setStyle({ fillOpacity: 0.25, weight: 1.5 });
    });
    layer.on('mouseout', function () {
      this.setStyle({ fillOpacity: 0.08, weight: 1 });
    });

    layer.bindPopup(makePopup(p), { maxWidth: 280 });
    layer.addTo(map);
    activeLayers[p.id] = layer;
    counts[p.provider]++;
  });

  document.getElementById('vis-iceye').textContent   = counts.iceye;
  document.getElementById('vis-umbra').textContent   = counts.umbra;
  document.getElementById('vis-capella').textContent = counts.capella;
}

function getCentroid(geometry) {
  if (!geometry) return null;
  if (geometry.type === 'Point') return geometry.coordinates;
  if (geometry.type === 'Polygon') {
    const coords = geometry.coordinates[0];
    const lng = coords.reduce((s, c) => s + c[0], 0) / coords.length;
    const lat = coords.reduce((s, c) => s + c[1], 0) / coords.length;
    return [lng, lat];
  }
  if (geometry.type === 'MultiPolygon') {
    const first = geometry.coordinates[0][0];
    const lng = first.reduce((s, c) => s + c[0], 0) / first.length;
    const lat = first.reduce((s, c) => s + c[1], 0) / first.length;
    return [lng, lat];
  }
  return null;
}

// ── Popup ────────────────────────────────────────────────────
function makePopup(p) {
  const detailBtn = `<button class="popup-btn details-btn" onclick="showDetailById('${p.id}')">Details</button>`;
  const dlBtn = p.download
    ? `<a class="popup-btn" href="${p.download}" target="_blank">Download</a>`
    : '';
  const provBtn = p.provider_url
    ? `<a class="popup-btn" href="${p.provider_url}" target="_blank">${p.provider_label}</a>`
    : '';

  return `
    <div class="popup-provider ${p.provider}">${p.provider_label}</div>
    <div class="popup-id">${p.id || '—'}</div>
    <div class="popup-date">📅 ${p.date || 'Unknown date'}</div>
    <div class="popup-mode">⚡ ${p.sensor_mode || '—'}</div>
    <div class="popup-actions">${detailBtn}${dlBtn}${provBtn}</div>
  `;
}

// ── Detail panel ─────────────────────────────────────────────
function showDetailById(id) {
  const feature = allFeatures.find(f => f.properties.id === id);
  if (feature) showDetail(feature.properties);
}
window.showDetailById = showDetailById;

function showDetail(p) {
  const thumb = p.thumbnail
    ? `<img class="detail-thumbnail" src="${p.thumbnail}" alt="SAR thumbnail" onerror="this.style.display='none'" />`
    : `<div class="detail-thumb-placeholder">No preview available</div>`;

  const rows = [
    ['Date',        p.date          || '—'],
    ['Provider',    p.provider_label || '—'],
    ['Mode',        p.sensor_mode   || '—'],
    ['Polarization',p.polarization  || '—'],
    ['Resolution',  p.resolution != null ? p.resolution + ' m' : '—'],
    ['Collection',  p.collection    || '—'],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('');

  const dlBtn = p.download
    ? `<a class="detail-action-btn primary" href="${p.download}" target="_blank">Download Asset</a>`
    : '';
  const provBtn = p.provider_url
    ? `<a class="detail-action-btn" href="${p.provider_url}" target="_blank">View on ${p.provider_label}</a>`
    : '';

  document.getElementById('detail-content').innerHTML = `
    ${thumb}
    <div class="detail-provider ${p.provider}">${p.provider_label}</div>
    <div class="detail-id">${p.id || '—'}</div>
    <table class="detail-table"><tbody>${rows}</tbody></table>
    <div class="detail-actions">${dlBtn}${provBtn}</div>
  `;

  document.getElementById('detail-panel').classList.remove('hidden');
}

document.getElementById('detail-close').addEventListener('click', () => {
  document.getElementById('detail-panel').classList.add('hidden');
});

// ── Draw bbox ────────────────────────────────────────────────
document.getElementById('draw-btn').addEventListener('click', () => {
  if (isDrawing) return;
  isDrawing = true;
  document.getElementById('draw-btn').classList.add('active');
  map.addControl(drawControl);
  new L.Draw.Rectangle(map, drawControl.options.draw.rectangle).enable();
});

map.on(L.Draw.Event.CREATED, (e) => {
  drawnItems.clearLayers();
  drawnItems.addLayer(e.layer);
  const b = e.layer.getBounds();
  drawnBbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  map.removeControl(drawControl);
  isDrawing = false;
  document.getElementById('draw-btn').classList.remove('active');
  document.getElementById('clear-bbox-btn').style.display = 'block';
  render();
});

document.getElementById('clear-bbox-btn').addEventListener('click', () => {
  drawnItems.clearLayers();
  drawnBbox = null;
  document.getElementById('clear-bbox-btn').style.display = 'none';
  render();
});

// ── Location search ──────────────────────────────────────────
document.getElementById('location-btn').addEventListener('click', searchLocation);
document.getElementById('location-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') searchLocation();
});

function searchLocation() {
  const q = document.getElementById('location-input').value.trim();
  if (!q) return;
  const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(q)}&limit=1`;
  fetch(url, { headers: { 'Accept-Language': 'en' } })
    .then(r => r.json())
    .then(results => {
      if (results && results.length > 0) {
        const r = results[0];
        map.setView([+r.lat, +r.lon], 8);
      } else {
        alert('Location not found.');
      }
    })
    .catch(() => alert('Location search failed.'));
}

// ── Provider toggles ─────────────────────────────────────────
['iceye', 'umbra', 'capella'].forEach(pid => {
  const el = document.getElementById(`tog-${pid}`);
  const chip = el.closest('.provider-chip');
  el.addEventListener('change', () => {
    chip.classList.toggle('disabled', !el.checked);
    render();
  });
});

// ── Date / mode filters ───────────────────────────────────────
['date-from', 'date-to', 'mode-filter'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => render());
});

// ── Reset ─────────────────────────────────────────────────────
document.getElementById('reset-btn').addEventListener('click', () => {
  ['tog-iceye', 'tog-umbra', 'tog-capella'].forEach(id => {
    document.getElementById(id).checked = true;
    document.getElementById(id).closest('.provider-chip').classList.remove('disabled');
  });
  document.getElementById('date-from').value = '';
  document.getElementById('date-to').value = '';
  document.getElementById('mode-filter').value = '';
  document.getElementById('location-input').value = '';
  drawnItems.clearLayers();
  drawnBbox = null;
  document.getElementById('clear-bbox-btn').style.display = 'none';
  render();
});

// ── Load data ─────────────────────────────────────────────────
fetch('data/scenes.geojson')
  .then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then(geojson => {
    allFeatures = geojson.features || [];

    // Set total counts per provider
    const totals = { iceye: 0, umbra: 0, capella: 0 };
    allFeatures.forEach(f => {
      const pid = f.properties.provider;
      if (pid in totals) totals[pid]++;
    });

    document.getElementById('scene-count').textContent = allFeatures.length;
    ['iceye', 'umbra', 'capella'].forEach(pid => {
      document.getElementById(`cnt-${pid}`).textContent = totals[pid];
    });

    document.getElementById('loading').classList.add('hidden');
    render();
  })
  .catch(err => {
    console.warn('Could not load scenes.geojson:', err);
    document.getElementById('loading').innerHTML =
      `<p style="color:#FF6B35">No scene data found.<br>Run <code>scripts/fetch_catalog.py</code> to generate it.</p>`;
  });
