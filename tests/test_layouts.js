// Perfis de layout (makeLayouts em web/index.html): migração, gancho, criar/aplicar/renomear/duplicar/apagar, compartilhar. Rodar: node tests/test_layouts.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* LAY:begin \*\/([\s\S]*?)\/\* LAY:end \*\//);
if (!m) { console.log("FALHA bloco LAY não encontrado"); process.exit(1); }
let fail = 0;
const t = (name, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", name, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };
const throws = (name, fn, re) => { try { fn(); fail++; console.log("FALHA", name, "(não lançou)"); } catch (e) { const ok = re.test(e.message); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", name, ok ? "" : e.message); } };
function fresh(initial = {}) {
  const mem = new Map(Object.entries(initial).map(([k, v]) => [k, JSON.stringify(v)]));
  const store = { get: (k, d) => (mem.has(k) ? JSON.parse(mem.get(k)) : d), set: (k, v) => { mem.set(k, JSON.stringify(v)); }, del: (k) => { mem.delete(k); } };
  let n = 0; const sb = { TextEncoder, TextDecoder, btoa, atob, Uint8Array, Set, Array, Object, JSON, Error, Number, String, Math, setTimeout, clearTimeout, Date };
  vm.createContext(sb); vm.runInContext(m[1] + "\nthis.make = makeLayouts;", sb);
  const L = sb.make(store, () => "id" + (++n).toString(16).padStart(2, "0"), () => 1000 + n, { theme: "modern", variant: "green", mode: "auto", accent: "blue", crt: true });
  return { L, store, mem };
}
const HOME = { v: 2, phone: [{ id: "now", size: "A" }, { id: "cpu", size: "P" }], tablet: [{ id: "dota", size: "G" }] };

// ---- migração: o layout de hoje vira o Padrão
{
  const { L, store, mem } = fresh({ theme: "fallout", variant: "amber", home: HOME, tab: "games", token: "SEGREDO", pcs: { list: [] } });
  L.init(); const s = JSON.parse(mem.get("layouts"));
  t("migração cria só o Padrão", s.list.map((p) => p.id), ["default"]);
  t("Padrão tem o tema de hoje", s.list[0].data.theme, "fallout");
  t("Padrão tem os widgets de hoje", s.list[0].data.home, HOME);
  t("Padrão tem a última tela", s.list[0].data.tab, "games");
  t("não copia token nem lista de PCs", Object.keys(s.list[0].data).filter((k) => ["token", "pcs"].includes(k)), []);
  t("as chaves de hoje continuam lá (volta atrás possível)", store.get("theme", null), "fallout");
  L.init(); t("repetir a migração não muda nada", JSON.parse(mem.get("layouts")).list.length, 1);
}
// ---- aparelho novo
{ const { L } = fresh(); t("aparelho novo: Padrão vazio", L.list().map((p) => p.name), ["Padrão"]);
  t("aparelho novo exporta os valores padrão (pacote não fica vazio)", L.pack("default").data, { theme: "modern", variant: "green", mode: "auto", accent: "blue", crt: true });
  const e = L.import(L.code("default")); t("e o código dele importa", e.name, "Padrão (2)"); }
// ---- gancho: mudar uma chave de layout espelha no perfil ativo; chave de outro tipo não
{
  const { L, store, mem } = fresh({ theme: "modern" }); L.init(); L.hook();
  store.set("accent", "pink"); L.sync();
  t("gancho espelha a mudança", JSON.parse(mem.get("layouts")).list[0].data.accent, "pink");
  store.set("snd", false); L.sync(); t("chave de som fica fora do perfil", JSON.parse(mem.get("layouts")).list[0].data.snd, undefined);
}
// ---- criar, aplicar, trocar mantém o outro intacto
{
  const { L, store } = fresh({ theme: "modern", mode: "dark", accent: "blue", home: HOME }); L.init(); L.hook();
  const novo = L.create("Trabalho"); t("criar ativa o novo", L.active().name, "Trabalho");
  store.set("accent", "orange"); L.sync();
  L.apply("default"); t("aplicar Padrão restaura o destaque", store.get("accent", null), "blue");
  t("aplicar Padrão deixa o ativo certo", L.active().isDefault, true);
  L.apply(novo.id); t("aplicar o novo traz o laranja", store.get("accent", null), "orange");
  t("tela e widgets acompanham o perfil", store.get("home", null), HOME);
  throws("nome repetido recusado", () => L.create("trabalho"), /Já existe/);
  throws("nome vazio recusado", () => L.create("   "), /1 a 30/);
  throws("nome grande recusado", () => L.create("x".repeat(31)), /1 a 30/);
}
// ---- renomear, duplicar, apagar, desfazer
{
  const { L, store } = fresh({ theme: "modern" }); L.init(); L.hook();
  const a = L.create("A"); L.rename(a.id, "Casa"); t("renomear", L.list().map((p) => p.name), ["Padrão", "Casa"]);
  throws("Padrão não renomeia", () => L.rename("default", "X"), /Padrão/);
  const d = L.duplicate(a.id); t("duplicar cria nome novo", d.name, "Casa (2)"); const d2 = L.duplicate(a.id); t("duplicar de novo", d2.name, "Casa (3)");
  t("duplicar não ativa", L.active().name, "Casa");
  throws("Padrão não apaga", () => L.remove("default"), /Padrão/);
  const rm = L.remove(a.id); t("apagar o em uso volta ao Padrão", [rm.wasActive, L.active().isDefault], [true, true]);
  t("restaurar traz de volta", [L.restore(rm), L.list().some((p) => p.name === "Casa")], [true, true]);
  for (let i = 0; i < 12; i++) { try { L.duplicate("default"); } catch (e) { t("limite de 10", /No máximo 10/.test(e.message), true); break; } }
}
// ---- compartilhar: só campos permitidos; importar valida tudo
{
  const { L, store } = fresh({ theme: "fallout", variant: "cyan", mode: "light", accent: "green", crt: false, home: HOME, tab: "mix", views: { mais: "mix" }, token: "SEGREDO" }); L.init(); L.hook();
  const code = L.code("default"), file = L.file("default");
  t("código tem o prefixo", code.startsWith("PCL1."), true);
  t("código não leva token, última tela nem views", /SEGREDO|mix/.test(Buffer.from(code.slice(5).replace(/-/g, "+").replace(/_/g, "/"), "base64").toString()), false);
  const e = L.import(code); t("importar cria perfil novo (não ativa)", [e.name, L.active().isDefault], ["Padrão (2)", true]);
  t("dados importados", [e.data.theme, e.data.variant, e.data.crt, e.data.home.phone.length], ["fallout", "cyan", false, 2]);
  t("importado não traz tab/views", [e.data.tab, e.data.views], [undefined, undefined]);
  const f = L.import(file, "Do arquivo"); t("importar arquivo com nome escolhido", f.name, "Do arquivo");
  throws("lixo recusado", () => L.parse("oi"), /não parece/);
  throws("vazio recusado", () => L.parse(""), /Cole o código/);
  throws("grande demais", () => L.parse("PCL1." + "A".repeat(9000)), /grande demais/);
  throws("JSON de outro app recusado", () => L.parse('{"a":1}'), /não parece/);
  const evil = "PCL1." + Buffer.from(JSON.stringify({ v: 1, n: "<img src=x onerror=1>", d: { theme: "hack", accent: "blue", home: { v: 2, phone: [{ id: "x y", size: "A" }] }, token: "T", pcs: [1] } })).toString("base64url");
  const p = L.parse(evil); t("só o válido entra (tema falso, widget torto e token caem)", Object.keys(p.data), ["accent"]);
  t("nome com tag fica como texto (a tela escapa)", p.name, "<img src=x onerror=1>");
  throws("nada válido = vazio", () => L.parse("PCL1." + Buffer.from(JSON.stringify({ v: 1, n: "a", d: { theme: "hack" } })).toString("base64url")), /vazio ou inválido/);
}
// ---- dados estragados no armazenamento não quebram
{
  const { L, mem } = fresh({ layouts: { v: 1, active: "nada", list: [{ id: "default", name: "x", data: { theme: "hack", accent: "pink" } }, { id: "!!", name: "ruim", data: {} }, 5] } });
  t("lista estragada: só o Padrão válido sobrevive", L.list().map((p) => p.name), ["Padrão"]);
  t("ativo inexistente volta ao Padrão", L.active().isDefault, true);
  L.init(); t("valor inválido de tema é descartado ao ler", JSON.parse(mem.get("layouts")).list[0].data, { accent: "pink" });
}
// ---- barra de navegação (chave nav): validação, limite da barra, compartilhar e migração
{
  const { L } = fresh(); const N = L.NAV, def = N.def();
  t("padrão: 4 na barra, o resto em Mais", [def.order.filter((i) => !def.hidden.includes(i)), def.hidden.length], [["inicio", "ctrl", "deck", "tasks"], 12]);
  t("padrão passa pela validação sem mudar", N.norm(def), def);
  t("lixo vira null", [N.norm(null), N.norm("x"), N.norm({ v: 2, order: [], hidden: [] }), N.norm({ v: 1, order: "a", hidden: [] })], [null, null, null, null]);
  const a = N.norm({ v: 1, order: ["sys", "xx", "sys", 5, "games"], hidden: ["games", "zz"] });
  t("id desconhecido/repetido some; faltantes entram no fim, em Mais", [a.order.length, a.order.slice(0, 2), a.hidden.includes("inicio"), a.hidden.includes("games")], [16, ["sys", "games"], true, true]);
  t("só 1 visível (sys) porque os faltantes ficam em Mais", a.order.filter((i) => !a.hidden.includes(i)), ["sys"]);
  const b = N.norm({ v: 1, order: N.IDS, hidden: [] });
  t("passou do limite: só os 4 primeiros ficam na barra", b.order.filter((i) => !b.hidden.includes(i)), ["inicio", "ctrl", "deck", "tasks"]);
  const c = N.norm({ v: 1, order: N.IDS, hidden: N.IDS });
  t("nada visível: o primeiro volta (a barra nunca fica vazia)", c.order.filter((i) => !c.hidden.includes(i)), ["inicio"]);
  const { L: L2, store } = fresh({ theme: "modern" }); L2.init(); L2.hook();
  store.set("nav", { v: 1, order: ["games", "inicio", "ctrl", "deck", "tasks", "dash", "media", "dota", "work", "weather", "send", "power", "widgets", "sys"], hidden: ["tasks", "dash", "media", "dota", "work", "weather", "send", "power", "widgets", "sys"] });
  L2.sync();
  const code = L2.code("default"), e = L2.import(code);
  t("o layout compartilha a barra", L2.pack(e.id).data.nav.order[0], "games");
  t("o espelho no perfil guarda a barra", L2.pack("default").data.nav.hidden.includes("tasks"), true);
  const bad = L2.import(JSON.stringify({ companion_layout: 1, name: "x", data: { theme: "fallout", nav: { v: 1, order: ["<script>", "sys"], hidden: [9] } } }));
  t("importar valida a barra (id estranho cai fora)", L2.pack(bad.id).data.nav.order.includes("<script>"), false);
  const onlyBad = L2.import(JSON.stringify({ companion_layout: 1, name: "y", data: { theme: "fallout", nav: "lixo" } }));
  t("barra inválida é descartada, o resto do pacote vale", [L2.pack(onlyBad.id).data.theme, "nav" in L2.pack(onlyBad.id).data], ["fallout", false]);
  const { L: L3 } = fresh({ theme: "fallout", home: HOME }); L3.init();
  t("perfil antigo (sem nav) continua válido", L3.pack("default").data.nav, undefined);
}
// ---- widgets do Início v3 (posição por widget): validação ao importar/compartilhar
{
  const { L } = fresh({ theme: "modern" });
  const H3 = { v: 3, land: [{ id: "now", size: "A", page: 0, c: 1, r: 1 }, { id: "cpu", size: "P", page: 0, c: 9, r: 1 }, { id: "net", size: "L" }], port: [{ id: "now", size: "P", page: 0, c: 2, r: 4 }], tablet: [] };
  const e = L.import(JSON.stringify({ companion_layout: 1, name: "v3", data: { theme: "fallout", home: H3 } })), h = L.pack(e.id).data.home;
  t("v3 passa e guarda a posição válida", [h.v, h.land[0]], [3, { id: "now", size: "A", page: 0, c: 1, r: 1 }]);
  t("posição torta some (o app reempacota), o widget fica", [h.land[1], h.land[2]], [{ id: "cpu", size: "P" }, { id: "net", size: "L" }]);
  t("em pé também", h.port[0], { id: "now", size: "P", page: 0, c: 2, r: 4 });
  const bad = L.import(JSON.stringify({ companion_layout: 1, name: "x", data: { theme: "fallout", home: { v: 3, land: [{ id: "<x>", size: "A" }] } } }));
  t("id torto derruba só o home (resto do pacote vale)", [L.pack(bad.id).data.theme, "home" in L.pack(bad.id).data], ["fallout", false]);
  const old = L.import(JSON.stringify({ companion_layout: 1, name: "v2", data: { theme: "fallout", home: HOME } }));
  t("v2 antigo ainda importa", L.pack(old.id).data.home, HOME);
}
process.exit(fail ? 1 : 0);
