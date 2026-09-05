const CACHE_NAME = 'open-sar-triad-v46';
const APP_SHELL = [
  './',
  './index.html',
  './manifest.json',
  './css/style.css?v=recent-1',
  './css/tour.css?v=tour-logo-1',
  './js/app.js?v=recent-1',
  './js/tour.js?v=tour-v12',
  './data/scenes.geojson',
  './assets/logo.svg',
  './assets/pwa-icon.svg',
  './assets/pwa-icon-192.png',
  './assets/pwa-icon-512.png',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
  'https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
  'https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js',
  'https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js',
  'https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json'
];
const APP_SHELL_URLS = new Set(APP_SHELL.map(url => new URL(url, self.location.href).href));

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await Promise.all(APP_SHELL.map(async url => {
      try {
        await cache.add(url);
      } catch (err) {
        console.warn('[open-sar-triad] skipped cache item', url, err);
      }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, './index.html'));
    return;
  }

  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    // The scene catalog is refreshed weekly by CI without bumping CACHE_NAME, so
    // serving it cache-first froze the map at a stale scene count. Revalidate it
    // in the background instead: the cached copy loads instantly and the next
    // visit reflects the newest data, with no manual version bump needed.
    if (url.pathname.endsWith('/data/scenes.geojson')) {
      event.respondWith(staleWhileRevalidate(request));
      return;
    }
    event.respondWith(cacheFirst(request));
    return;
  }

  if (APP_SHELL_URLS.has(request.url)) {
    event.respondWith(staleWhileRevalidate(request));
  }
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) {
    const cache = await caches.open(CACHE_NAME);
    cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request, fallbackUrl) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    return cached || caches.match(fallbackUrl);
  }
}

async function staleWhileRevalidate(request) {
  const cached = await caches.match(request);
  const fetchPromise = fetch(request).then(response => {
    if (response && (response.ok || response.type === 'opaque')) {
      caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone()));
    }
    return response;
  }).catch(() => cached);
  return cached || fetchPromise;
}
