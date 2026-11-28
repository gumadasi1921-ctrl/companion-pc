// Entrar sozinho no PC ligado (bloco APC em web/index.html). Rodar: node tests/test_autopc.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* APC:begin \*\/([\s\S]*?)\/\* APC:end \*\//);
if (!m) { console.log("FALHA bloco APC não encontrado"); process.exit(1); }
const sb = { Promise, Array, Object }; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.makeAutoPc = makeAutoPc;", sb);
let fail = 0;
const t = (n, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", n, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };
const E = (id, hasToken = true) => ({ id, name: id, hasToken });
// relógio falso: setT guarda; `fire` dispara em ordem de prazo
function rig(o = {}) {
  const r = { timers: [], switched: [], probed: [], connected: false, fatal: false, active: "A", ...o };
  r.apc = sb.makeAutoPc({
    enabled: () => (o.enabled !== false), activeId: () => r.active, list: () => o.list || [E("A"), E("B"), E("C")], recent: () => !!o.recent,
    setT: (f, ms) => { r.timers.push({ f, ms }); }, connected: () => r.connected, fatal: () => r.fatal,
    probe: (e, ms) => { r.probed.push(e.id); return new Promise((res) => { r.pending = r.pending || {}; r.pending[e.id] = res; }); },
    onSwitch: (e) => r.switched.push(e.id),
  });
  r.tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
  r.fire = async (ms) => { const x = r.timers.filter((q) => q.ms === ms && !q.done); x.forEach((q) => { q.done = true; q.f(); }); await r.tick(); };
  return r;
}
(async () => {
  let r = rig({ list: [E("A")] }); t("só 1 PC: nada", await r.apc.run(), "alone");
  r = rig({ enabled: false }); t("interruptor desligado: nada", await r.apc.run(), "off");
  r = rig({ recent: true }); t("acabou de trocar sozinho: não troca de novo", await r.apc.run(), "recent");
  r = rig({ list: [E("A"), E("B", false)] }); t("PC sem token não é testado", await r.apc.run(), "alone");
  // atual conecta dentro dos 3 s: nunca testa nem troca
  r = rig(); let p = r.apc.run(); await r.tick(); r.connected = true; await r.fire(3000); t("atual conectou em 3 s", [await p, r.probed, r.switched], ["connected", [], []]);
  // erro de login do atual
  r = rig(); p = r.apc.run(); await r.tick(); r.fatal = true; await r.fire(3000); t("erro de login do atual: não troca", [await p, r.switched], ["fatal", []]);
  // atual calado, B responde
  r = rig(); p = r.apc.run(); await r.tick(); await r.fire(3000);
  t("testa os outros em paralelo (nunca o atual)", r.probed, ["B", "C"]);
  r.pending.C(false); r.pending.B(true); await r.tick(); t("B respondeu: troca para B", [await p, r.switched], ["switched", ["B"]]);
  // o atual conecta durante o teste
  r = rig(); p = r.apc.run(); await r.tick(); await r.fire(3000); r.connected = true; r.pending.B(true); await r.tick();
  t("atual conectou durante o teste: não troca", [await p, r.switched], ["connected", []]);
  // ninguém responde
  r = rig(); p = r.apc.run(); await r.tick(); await r.fire(3000); r.pending.B(false); r.pending.C(false); await r.tick(); t("ninguém responde: fica", [await p, r.switched], ["none", []]);
  // teste que nunca responde: o prazo geral encerra
  r = rig(); p = r.apc.run(); await r.tick(); await r.fire(3000); await r.fire(4500); t("teste pendurado: prazo geral", [await p, r.switched], ["none", []]);
  // teste que lança erro conta como não respondeu
  r = rig(); r.apc = sb.makeAutoPc({ enabled: () => true, activeId: () => "A", list: () => [E("A"), E("B")], recent: () => false, setT: (f, ms) => r.timers.push({ f, ms }), connected: () => false, fatal: () => false, probe: () => { throw new Error("x"); }, onSwitch: (e) => r.switched.push(e.id) });
  p = r.apc.run(); await r.tick(); await r.fire(3000); t("probe com erro: não troca", [await p, r.switched], ["none", []]);
  // 1× por página
  r = rig({ list: [E("A")] }); await r.apc.run(); t("segunda chamada ignorada", await r.apc.run(), "again");
  // o código usa o bloco certo: prazos de 3 s e 4 s
  t("prazos", [rig().apc.WAIT, rig().apc.PROBE], [3000, 4000]);
  console.log(fail ? `\n${fail} FALHA(S)` : "\ntudo certo"); process.exit(fail ? 1 : 0);
})();
