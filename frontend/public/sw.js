// PanWatch Service Worker. Each deployment owns only its own scope and caches.
const BASE_URL = new URL(self.registration.scope);
const BASE_PATH = BASE_URL.pathname;
const CACHE_PREFIX = `panwatch:${BASE_PATH}:`;
const CACHE_NAME = `${CACHE_PREFIX}v4`;
const STATIC_ASSETS = ['', 'manifest.json', 'manifest.zh-CN.json', 'icon-192.png', 'icon-512.png']
  .map(path => new URL(path, BASE_URL).href);

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(names => Promise.all(
      names.filter(name => (name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
        || (BASE_PATH === '/' && name === 'panwatch-v2'))
        .map(name => caches.delete(name))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== BASE_URL.origin || !url.pathname.startsWith(BASE_PATH)) return;
  const localPath = url.pathname.slice(BASE_PATH.length);
  // Never cache API, realtime, authentication, or protocol responses.
  if (localPath.split('/').some(segment => ['api', 'mcp', '.well-known', 'docs', 'redoc', 'openapi.json'].includes(segment))) return;
  // A root-scoped worker may temporarily control a nested installation. Cache
  // only this installation's known public files and bundled assets, never an
  // arbitrary navigation or another app's authenticated response.
  const isBundledAsset = /^assets\/[^/]+$/.test(localPath);
  if (!STATIC_ASSETS.includes(url.origin + url.pathname) && !isBundledAsset) return;

  event.respondWith(
    fetch(event.request).then(response => {
      const isHtml = (response.headers.get('content-type') || '').toLowerCase().includes('text/html');
      // The known root shell is public HTML; an asset URL returning HTML may
      // belong to a nested application, even when its prefix looks like a file.
      if (response.ok && !response.redirected && !(isBundledAsset && isHtml)) {
        const copy = response.clone();
        event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)));
      }
      return response;
    }).catch(async () => {
      const cache = await caches.open(CACHE_NAME);
      return await cache.match(event.request) || Response.error();
    })
  );
});
