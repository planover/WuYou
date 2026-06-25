const CACHE = "wuyou-v1";
const ASSETS = ["/", "/static/js/app.js", "/static/css/app.css", "/static/index.html"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;

  if (e.request.url.includes("/api/mail/inbox") || e.request.url.includes("/api/mail/threads")) {
    e.respondWith(
      fetch(e.request).then(response => {
        const clone = response.clone();
        caches.open(CACHE + "-api").then(c => c.put(e.request, clone));
        return response;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached && !navigator.onLine) return cached;
      return fetch(e.request).then(response => {
        if (response.ok && e.request.url.includes("/static/")) {
          const clone = response.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return response;
      }).catch(() => cached || new Response("Offline", { status: 503 }));
    })
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE && k !== CACHE + "-api").map(k => caches.delete(k)))));
});
