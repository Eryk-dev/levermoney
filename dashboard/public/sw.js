const CACHE_NAME = 'faturamento-dashboard-v2';
const CORE_ASSETS = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/favicon.svg',
  '/apple-touch-icon.png',
  '/pwa-192.png',
  '/pwa-192-maskable.png',
  '/pwa-512.png',
  '/pwa-512-maskable.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => (key === CACHE_NAME ? null : caches.delete(key))))
    ).then(() => self.clients.claim())
  );
});

function isHtmlRequest(request) {
  return request.headers.get('accept')?.includes('text/html');
}

function isApiPath(pathname) {
  const apiPrefixes = [
    '/admin',
    '/dashboard',
    '/auth',
    '/health',
    '/webhooks',
    '/backfill',
    '/baixas',
    '/queue',
    '/expenses',
    '/docs',
    '/openapi.json',
    '/redoc',
    '/install',
  ];
  return apiPrefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    // Never cache API responses; always hit network for fresh data.
    if (isApiPath(url.pathname)) {
      event.respondWith(fetch(request, { cache: 'no-store' }));
      return;
    }

    if (isHtmlRequest(request)) {
      // Network-first for HTML, fallback to cached shell
      event.respondWith(
        fetch(request)
          .then((response) => {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
            return response;
          })
          .catch(() => caches.match(request).then((cached) => cached || caches.match('/index.html')))
      );
      return;
    }

    // Cache-first for static assets
    event.respondWith(
      caches.match(request).then((cached) =>
        cached ||
        fetch(request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        })
      )
    );
    return;
  }

  // Runtime cache for fonts (Google)
  if (url.origin.includes('fonts.googleapis.com') || url.origin.includes('fonts.gstatic.com')) {
    event.respondWith(
      caches.match(request).then((cached) =>
        cached ||
        fetch(request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        })
      )
    );
  }
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
