const CACHE = "geoclaw-v1";
const SHELL = ["/dashboard", "/terminal", "/ask", "/live", "/theses"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL))
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.url.includes("/api/")) {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match(event.request).then((response) => response || new Response(
          "{\"status\":\"offline\"}",
          { headers: { "Content-Type": "application/json" } }
        ))
      )
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((response) => response || fetch(event.request))
  );
});
