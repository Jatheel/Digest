// Minimal service worker: cache the app shell but make sure it refreshes when the
// frontend changes. News API responses must stay live and never be cached.
const CACHE = "digest-shell-v3";
const SHELL = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Never cache news API requests.
  if (url.pathname.startsWith("/api/")) return;

  // Always fetch the latest app shell and manifest so UI updates are not hidden by stale cache.
  if (url.pathname === "/" || url.pathname === "/index.html" || url.pathname === "/manifest.json") {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
