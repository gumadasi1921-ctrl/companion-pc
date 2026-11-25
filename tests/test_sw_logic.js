// Testa a decisão do service worker (sw.js: route) sem navegador. Rodar: node tests/test_sw_logic.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "sw.js"), "utf8");
const sandbox = { self: { addEventListener() {}, location: { origin: "https://casa.x.ts.net" } }, module: { exports: {} }, URL, Request: class {}, Response: class {}, caches: {}, fetch() {} };
vm.createContext(sandbox); vm.runInContext(src, sandbox);
const { route } = sandbox.module.exports, O = "https://casa.x.ts.net";
let fail = 0;
const t = (name, got, want) => { const ok = got === want; if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", name, ok ? "" : `(${got} != ${want})`); };
t("página inicial = navigate", route(O + "/", "GET", "navigate", O), "navigate");
t("página com ?shared=1 = navigate", route(O + "/?shared=1", "GET", "navigate", O), "navigate");
t("legacy.html não entra", route(O + "/legacy.html", "GET", "navigate", O), "skip");
t("ícone png = asset", route(O + "/icon-192.png", "GET", "no-cors", O), "asset");
t("fonte = asset", route(O + "/fonts/vt323.woff2", "GET", "cors", O), "asset");
t("sprite = asset", route(O + "/icons/lucide.svg", "GET", "cors", O), "asset");
t("/icon/<id> (ícone de app do PC) nunca", route(O + "/icon/abc123?k=zz", "GET", "no-cors", O), "skip");
t("/dota/img nunca", route(O + "/dota/img/hero/1.png", "GET", "no-cors", O), "skip");
t("/dl nunca", route(O + "/dl/1/a.png", "GET", "navigate", O), "skip");
t("/ws nunca", route(O + "/ws", "GET", "websocket", O), "skip");
t("com k= nunca", route(O + "/icon-192.png?k=abc", "GET", "no-cors", O), "skip");
t("com token nunca", route(O + "/icon-192.png?token=abc", "GET", "no-cors", O), "skip");
t("outro PC (outra origem) nunca", route("https://trabalho.x.ts.net/icon-192.png", "GET", "no-cors", O), "skip");
t("POST /share", route(O + "/share", "POST", "navigate", O), "share");
t("POST em outra rota nunca", route(O + "/outra", "POST", "navigate", O), "skip");
t("mp4 pesado não entra", route(O + "/keepawake.mp4", "GET", "no-cors", O), "skip");
t("URL inválida", route("::::", "GET", "navigate", O), "skip");

// ---- navigate / asset com cache e rede simulados
(async () => {
  const store = new Map();
  class Resp { constructor(body, o = {}) { this.body = body; this.ok = o.ok !== false; this.type = o.type || "basic"; } clone() { return new Resp(this.body, { ok: this.ok, type: this.type }); } static error() { const r = new Resp("erro", { ok: false }); r.isError = true; return r; } }
  let net = "ok";
  const cache = { match: async (k) => store.get(typeof k === "string" ? k : k.url), put: async (k, v) => { store.set(typeof k === "string" ? k : k.url, v); }, add: async () => {} };
  const sb = { self: { addEventListener() {}, location: { origin: O } }, module: { exports: {} }, URL, Request: class {}, Response: Resp, AbortController, setTimeout, clearTimeout,
    caches: { open: async () => cache, keys: async () => [] }, fetch: async () => { if (net === "off") throw new Error("offline"); return new Resp("pagina nova"); } };
  vm.createContext(sb); vm.runInContext(src, sb);
  const { navigate, asset } = sb.module.exports;
  let r = await navigate({ url: O + "/" }); t("online: devolve a página da rede", r.body, "pagina nova");
  t("online: guardou a casca em /", store.get("/") && store.get("/").body, "pagina nova");
  net = "off"; r = await navigate({ url: O + "/" }); t("offline: abre a casca do cache", r.body, "pagina nova");
  store.clear(); r = await navigate({ url: O + "/" }); t("offline sem cache: erro (nada inventado)", r.isError === true, true);
  net = "ok"; r = await asset({ url: O + "/icon-192.png" }); t("asset sem cache: busca na rede", r.body, "pagina nova");
  net = "off"; r = await asset({ url: O + "/icon-192.png" }); t("asset offline: usa o cache", r.body, "pagina nova");
  process.exit(fail ? 1 : 0);
})();
