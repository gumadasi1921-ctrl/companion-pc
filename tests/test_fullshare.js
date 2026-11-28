// Exportar/importar layout completo (PCL2, makeFull em web/index.html). Rodar: node tests/test_fullshare.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const blk = (n) => { const m = html.match(new RegExp(`/\\* ${n}:begin \\*/([\\s\\S]*?)/\\* ${n}:end \\*/`)); if (!m) { console.log("FALHA bloco " + n); process.exit(1); } return m[1]; };
let fail = 0;
const t = (name, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", name, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };
const throws = (name, fn, re) => { try { fn(); fail++; console.log("FALHA", name, "(não lançou)"); } catch (e) { const ok = re.test(e.message); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", name, ok ? "" : e.message); } };
const DEF = { theme: "modern", variant: "green", mode: "auto", accent: "blue", crt: true };
function dev(initial = {}) {
  const mem = new Map(Object.entries(initial).map(([k, v]) => [k, JSON.stringify(v)]));
  const store = { get: (k, d) => (mem.has(k) ? JSON.parse(mem.get(k)) : d), set: (k, v) => { mem.set(k, JSON.stringify(v)); }, del: (k) => { mem.delete(k); } };
  let n = 0, clock = 5000; const sb = { TextEncoder, TextDecoder, btoa, atob, Uint8Array, Set, Array, Object, JSON, Error, Number, String, Math, setTimeout, clearTimeout, Date };
  vm.createContext(sb); vm.runInContext(blk("KEYS") + blk("LAY") + blk("PCX") + "\nthis.mk = (s, r, nw, d) => { const L = makeLayouts(s, r, nw, d); return { L, F: makeFull(s, DEVKEYS, L, d, nw), K: DEVKEYS }; };", sb);
  const x = sb.mk(store, () => "id" + (++n), () => ++clock, DEF);
  return { ...x, store, mem };
}
const HOME = { v: 3, land: [{ id: "now", size: "A", page: 0, c: 1, r: 1 }, { id: "cpu", size: "P", page: 0, c: 2, r: 1 }], port: [{ id: "tasks", size: "L" }], tablet: [{ id: "dota", size: "G", page: 1, c: 1, r: 1 }] };
const FULLSTATE = {
  theme: "fallout", variant: "amber", mode: "dark", accent: "pink", crt: false, home: HOME, homepg: { land: 1 }, tab: "games", views: { ctrl: "mouse" },
  nav: { v: 1, order: ["games", "inicio", "ctrl", "deck", "tasks", "dash", "media", "dota", "work", "weather", "send", "power", "widgets", "sys", "mascot", "reminders", "calendar", "recap"], hidden: ["sys", "power", "calendar", "recap"] },
  mascot: { v: 1, skin: "lume", cfg: { lunch: { on: true, at: "12:30", days: "all" } }, water_at: 99, snack: 7, born: 1 },
  alerts: { net: false, gpu: true, gpuV: 70 }, snd: false, vib: false, wake: false, dock: 5, idleMode: "clock", land: true, sens: 2.5, scrFast: false,
  micFab: false, micfab: { p: { x: 0.2, y: 0.5 }, l: { x: 0.9, y: 0.1 } }, lumepos: { p: { x: 1, y: 0.4 } }, dotaOpen: false, deckAuto: false, pcnotif: false, wmode: "month", tkmode: "foco",
};
const SECRETS = { token: "SEGREDO", pcs: { list: [{ base: "https://x", token: "T" }] }, devid: "a".repeat(32), devname: "iPad do Gui", pend: "123456", bklast: { at: 1 } };

// ---- fonte única: DEVKEYS == devbackup.KEYS == BK.KEYS
{
  const py = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "devbackup.py"), "utf8");
  const body = py.match(/^KEYS = \(([\s\S]*?)\)/m)[1], pyKeys = [...body.matchAll(/"([A-Za-z]+)"/g)].map((m) => m[1]);
  const { K } = dev();
  t("DEVKEYS igual à lista do servidor (devbackup.KEYS)", [...K], pyKeys);
  t("BK usa a mesma lista (sem 2ª cópia)", /const KEYS = DEVKEYS;/.test(html) && !/const KEYS = \[/.test(blk("BKP")), true);
  const { F } = dev();
  t("todo grupo cobre as chaves (menos layouts e pchome)", F.GROUPS.flatMap((g) => g.keys).sort(), [...K].filter((k) => k !== "layouts" && k !== "pchome").sort());
  t("grupos", F.GROUPS.map((g) => g.label), ["Início", "Menu", "Aparência", "Tela de espera", "Lume e avisos", "Outros"]);
}
// ---- exportar -> importar em aparelho limpo dá o mesmo estado
{
  const a = dev({ ...FULLSTATE, ...SECRETS }); a.L.init();
  const code = a.F.code();
  t("código PCL2", code.startsWith("PCL2."), true);
  const dec = JSON.parse(Buffer.from(code.slice(5), "base64url").toString());
  t("sem segredo no código", Object.keys(dec.d).filter((k) => ["token", "pcs", "devid", "devname", "pend", "bklast", "layouts", "impundo"].includes(k)), []);
  t("mascote sem medidores/histórico", dec.d.mascot, { v: 1, skin: "lume", cfg: FULLSTATE.mascot.cfg });
  const b = dev(); b.L.init();
  const p = b.F.parse(code), prev = b.F.preview(p);
  t("prévia tem os 6 grupos", prev.map((g) => g.label), ["Início", "Menu", "Aparência", "Tela de espera", "Lume e avisos", "Outros"]);
  t("prévia conta mudanças (aparência)", prev.find((g) => g.id === "look").changed, 5);
  b.F.apply(p, prev.map((g) => g.id));
  const exp = { ...FULLSTATE, nav: { ...FULLSTATE.nav, hidden: ["power", "sys", "calendar", "recap"] } }; delete exp.mascot;   // nav é normalizado (hidden na ordem do menu)
  for (const k of Object.keys(exp)) t("mesmo valor: " + k, b.store.get(k, null), exp[k]);
  t("mascote importado só skin+cfg", b.store.get("mascot", null), { v: 1, skin: "lume", cfg: FULLSTATE.mascot.cfg });
  t("não trouxe token/pcs/devid", ["token", "pcs", "devid", "devname", "pend"].filter((k) => b.store.get(k, null) !== null), []);
  const d2 = JSON.parse(Buffer.from(b.F.code().slice(5), "base64url").toString()).d;
  t("exportar de novo = mesmos dados", d2, dec.d);
  // arquivo .json
  const c = dev(); c.F.apply(c.F.parse(a.F.file()), c.F.GROUPS.map((g) => g.id));
  t("arquivo importa igual", c.store.get("home", null), HOME);
}
// ---- mascote existente: medidores preservados, ajustes trocados
{
  const c = dev({ mascot: { v: 1, skin: "lume", cfg: { lunch: { on: false } }, water_at: 42, snack: 3, born: 9 } });
  const a = dev(FULLSTATE); const p = c.F.parse(a.F.code()); c.F.apply(p, ["lume"]);
  const m = c.store.get("mascot", null); t("medidores mantidos", [m.water_at, m.snack, m.born], [42, 3, 9]); t("cfg trocado", m.cfg.lunch.at, "12:30");
}
// ---- grupos desmarcados não mudam nada; desfazer volta ao estado anterior
{
  const b = dev({ theme: "modern", accent: "blue", home: { v: 2, phone: [{ id: "now", size: "A" }] }, token: "SEGREDO" }); b.L.init();
  const a = dev(FULLSTATE); const p = b.F.parse(a.F.code());
  const strip = () => { const x = b.F.snapshot(); delete x.layouts; return JSON.stringify(x); }, before = strip();
  b.F.apply(p, ["home", "menu"]);
  t("só os marcados mudam", [b.store.get("theme", null), b.store.get("accent", null), b.store.get("home", null).v], ["modern", "blue", 3]);
  t("há o que desfazer", !!b.F.undoInfo(), true);
  b.F.undo();
  t("desfazer restaura o estado", strip() === before, true);
  t("desfazer limpa a cópia", b.F.undoInfo(), null);
  throws("nenhum grupo marcado", () => b.F.apply(p, []), /pelo menos um grupo/);
  throws("desfazer sem cópia", () => b.F.undo(), /Não há importação/);
}
// ---- itens inválidos/ausentes: ignorados e listados, o resto vale
{
  const b = dev(); const bad = { companion_layout: 2, name: "<b>x</b>", data: { theme: "hack", accent: "pink", token: "T", pcs: [1], layouts: { v: 1 }, dock: 9999, snd: "sim", alerts: { gpu: true, evil: 1, gpuV: 9999 }, micfab: { p: { x: 5, y: 0 } }, nav: "lixo", mode: "dark" } };
  const p = b.F.parse(JSON.stringify(bad));
  t("válidos ficam", p.data, { accent: "pink", alerts: { gpu: true }, mode: "dark" });
  t("ignorados listados", p.ignored.map((x) => x.k).sort(), ["dock", "layouts", "micfab", "nav", "pcs", "snd", "theme", "token"]);
  t("motivos", p.ignored.find((x) => x.k === "token").why, "não faz parte do layout");
  throws("tudo inválido", () => b.F.parse(JSON.stringify({ companion_layout: 2, name: "a", data: { theme: "hack" } })), /vazio ou inválido/);
}
// ---- código corrompido / formatos
{
  const b = dev();
  throws("lixo", () => b.F.parse("PCL2.@@@@"), /não parece/);
  throws("truncado", () => b.F.parse(dev(FULLSTATE).F.code().slice(0, 40)), /não parece/);
  throws("json sem marca", () => b.F.parse('{"a":1}'), /não parece/);
  throws("vazio", () => b.F.parse("  "), /Cole o código/);
  throws("grande demais", () => b.F.parse("PCL2." + "A".repeat(33000)), /grande demais/);
  throws("PCL2 com versão errada", () => b.F.parse("PCL2." + Buffer.from(JSON.stringify({ v: 1, n: "a", d: { theme: "fallout" } })).toString("base64url")), /não parece/);
  t("isFull reconhece só PCL2", [b.F.isFull("PCL2.x"), b.F.isFull("PCL1.x"), b.F.isFull('{"companion_layout":1}'), b.F.isFull('{"companion_layout":2}'), b.F.isFull("lixo")], [true, false, false, true, false]);
}
// ---- PCL1 antigo continua valendo (só Início/tema; cria perfil novo)
{
  const a = dev({ theme: "fallout", home: HOME }); a.L.init();
  const old = a.L.code("default"); t("PCL1 não é PCL2", a.F.isFull(old), false);
  const b = dev(); b.L.init(); const e = b.L.import(old); t("PCL1 importa como perfil", [e.name, b.L.list().length], ["Padrão (2)", 2]);
  const f = b.L.import(JSON.stringify({ companion_layout: 1, name: "v1", data: { theme: "fallout", home: { v: 2, phone: [{ id: "now", size: "A" }] } } })); t("arquivo v1 importa", f.name, "v1");
}
console.log(fail ? `${fail} FALHA(S)` : "tudo certo"); process.exit(fail ? 1 : 0);
