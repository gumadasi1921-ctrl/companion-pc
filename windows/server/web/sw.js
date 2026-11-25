// Service worker do Companion PC.
// 1) "Compartilhar" de outros apps (POST /share) -> guarda no cache "share" e abre o app.
// 2) App que abre offline (PWA): guarda só a CASCA (a página e os arquivos estáticos do próprio app). A página tenta a rede
//    primeiro (3 s) e cai no cache; ícones e fontes usam o cache e se atualizam em segundo plano.
// NUNCA guarda: o WebSocket, /icon/, /dota/img/, /dl/, nada com chave (k=) ou token, nem requisições a outro endereço (os PCs).
// Dados de PC não ficam aqui: sem rede o app mostra "PC indisponível", nunca dados velhos.
const SHELL = "shell-1";
const PRECACHE = ["/", "manifest.webmanifest", "icon-192.png", "icon-512.png", "icon-maskable.png",
  "fonts/vt323.woff2", "fonts/share-tech-mono.woff2", "icons/lucide.svg"];
const NET_TIMEOUT_MS = 3000;
const NEVER = ["/ws", "/icon/", "/dota/img/", "/dl/", "/share"];

// Decide o que fazer com um pedido (função pura: testada em tests/test_sw_logic.js)
function route(urlStr, method, mode, ownOrigin) {
  let u;
  try { u = new URL(urlStr); } catch { return "skip"; }
  if (u.origin !== ownOrigin) return "skip";                                   // outro PC / outro site: não é com a casca
  if (method === "POST" && u.pathname === "/share") return "share";
  if (method !== "GET") return "skip";
  if (NEVER.some((p) => u.pathname === p || u.pathname.startsWith(p.endsWith("/") ? p : p + "/"))) return "skip";
  if (u.searchParams.has("k") || u.searchParams.has("token") || u.searchParams.has("t")) return "skip";
  if (mode === "navigate") return u.pathname === "/" || u.pathname === "/index.html" ? "navigate" : "skip";
  if (/\.(png|svg|woff2|webmanifest)$/.test(u.pathname)) return "asset";
  return "skip";
}

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    for (const p of PRECACHE) { try { await cache.add(new Request(p, { cache: "reload" })); } catch {} }   // um arquivo faltando não impede a instalação
    await self.skipWaiting();
  })());
});
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== SHELL && k !== "share") await caches.delete(k);
  await self.clients.claim();
})()));

async function navigate(req) {
  const cache = await caches.open(SHELL), ctl = new AbortController(), t = setTimeout(() => ctl.abort(), NET_TIMEOUT_MS);
  try {
    const res = await fetch(req, { signal: ctl.signal });
    clearTimeout(t);
    if (res.ok && res.type === "basic") await cache.put("/", res.clone());
    return res;
  } catch {
    clearTimeout(t);
    return (await cache.match("/")) || Response.error();
  }
}
async function asset(req) {
  const cache = await caches.open(SHELL), hit = await cache.match(req);
  const refresh = fetch(req).then((res) => { if (res.ok && res.type === "basic") cache.put(req, res.clone()); return res; }).catch(() => null);
  return hit || (await refresh) || Response.error();
}
async function share(req) {
  const form = await req.formData();
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
}

self.addEventListener("fetch", (e) => {
  const r = route(e.request.url, e.request.method, e.request.mode, self.location.origin);
  if (r === "navigate") e.respondWith(navigate(e.request));
  else if (r === "asset") e.respondWith(asset(e.request));
  else if (r === "share") e.respondWith(share(e.request));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => (cs[0] ? cs[0].focus() : self.clients.openWindow("/"))));
});

if (typeof module !== "undefined") module.exports = { route, navigate, asset, NEVER, SHELL };
