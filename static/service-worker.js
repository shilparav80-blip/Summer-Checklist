const CACHE_NAME = 'star-quest-v2';
const APP_SHELL = [
  '/',
  '/login',
  '/manifest.webmanifest',
  '/app-icon.svg'
];

function shouldBypassCache(request) {
  const url = new URL(request.url);
  return url.pathname.startsWith('/api/')
    || url.pathname.startsWith('/admin')
    || url.pathname.startsWith('/checklist');
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  if (shouldBypassCache(event.request)) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
