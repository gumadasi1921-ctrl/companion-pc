// Service worker: permite instalar como app e receber "Compartilhar" de outros
// apps do Android (fotos, arquivos, links). Sem cache offline: o app só faz
// sentido conectado ao PC.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "POST" || url.pathname !== "/share") return;
  e.respondWith((async () => {
    const form = await e.request.formData();
    const cache = await caches.open("share");
    await Promise.all((await cache.keys()).map((k) => cache.delete(k)));
    const meta = { title: form.get("title") || "", text: form.get("text") || "", url: form.get("url") || "", files: [] };
    let i = 0;
    for (const f of form.getAll("files")) {
      if (!(f instanceof File)) continue;
      const key = `/share-file/${i++}`;
      await cache.put(key, new Response(f, { headers: { "Content-Type": f.type || "application/octet-stream" } }));
      meta.files.push({ key, name: f.name, size: f.size });
    }
    await cache.put("/share-meta", new Response(JSON.stringify(meta), { headers: { "Content-Type": "application/json" } }));
    return Response.redirect("/?shared=1", 303);
  })());
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => (cs[0] ? cs[0].focus() : self.clients.openWindow("/"))));
});
