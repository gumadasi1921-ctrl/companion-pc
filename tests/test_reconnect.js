// Reconexão do app (bloco RC em web/index.html): socket morto/preso, backoff, gatilhos. Rodar: node tests/test_reconnect.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* RC:begin \*\/([\s\S]*?)\/\* RC:end \*\//);
if (!m) { console.log("FALHA bloco RC não encontrado"); process.exit(1); }
const sb = { Math, Date }; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.makeRc = makeRc; this.rcDelay = rcDelay;", sb);
let fail = 0;
const t = (n, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", n, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };

t("backoff 1-2-4-8-10 e teto", [0, 1, 2, 3, 4, 5, 50].map(sb.rcDelay), [1000, 2000, 4000, 8000, 10000, 10000, 10000]);

function rig() {
  const r = { clock: 0, timers: [], socks: [], st: [], fatal: [], msgs: 0 };
  const setT = (f, ms) => { const h = { f, at: r.clock + ms, on: true }; r.timers.push(h); return h; };
  r.advance = (ms) => { const end = r.clock + ms; for (;;) { const n = r.timers.filter((h) => h.on && h.at <= end).sort((a, b) => a.at - b.at)[0]; if (!n) break; r.clock = n.at; n.on = false; n.f(); } r.clock = end; };
  r.rc = sb.makeRc({
    create: () => { const s = { readyState: 0, closed: false, close() { this.closed = true; } }; r.socks.push(s); return s; },
    now: () => r.clock, setT, clearT: (h) => { h.on = false; }, pairCode: () => null,
    onSock: () => {}, onOpen: () => {}, onMessage: () => { r.msgs++; },
    onStatus: (k) => r.st.push(k), onFatal: (c) => r.fatal.push(c),
  });
  r.open = () => { const s = r.rc.sock; s.readyState = 1; s.onopen(); };
  r.live = () => { r.open(); r.rc.welcome(); };
  return r;
}
let r;
// 1) queda normal: tenta de novo em 1 s, depois 2 s, 4 s... para sempre
r = rig(); r.rc.connect(); r.live();
r.rc.sock.onclose({ code: 1006 });
t("caiu: sem socket e 1 tentativa agendada", [r.rc.sock, r.st.slice(-1)[0]], [null, "down"]);
r.advance(999); t("antes de 1 s nada", r.socks.length, 1); r.advance(2); t("com 1 s reconecta", r.socks.length, 2);
r.rc.sock.onclose({ code: 1006 }); r.advance(1500); t("2ª falha: ainda esperando 2 s", r.socks.length, 2); r.advance(600); t("2ª falha: reconecta", r.socks.length, 3);
for (let i = 0; i < 12; i++) { r.rc.sock.onclose({ code: 1006 }); r.advance(10001); }
t("depois de muitas falhas continua tentando (teto 10 s)", r.socks.length, 15);
// 2) socket preso em CONNECTING
r = rig(); r.rc.connect(); r.advance(500); r.rc.tick(); t("5,5 s preso ainda espera", r.socks.length, 1);
r.advance(6000); r.rc.tick(); t("preso > 6 s: descarta e agenda", [r.socks[0].closed, r.rc.sock], [true, null]); r.advance(1100); t("reconecta depois do preso", r.socks.length, 2);
// 3) socket aberto mas calado (zumbi do iOS)
r = rig(); r.rc.connect(); r.live();
r.advance(10000); r.rc.tick(); t("10 s calado ainda vale", r.rc.sock !== null, true);
r.rc.welcome(); r.advance(16000); r.rc.tick(); t("calado > 15 s: derruba", [r.socks[0].closed, r.rc.sock], [true, null]); r.advance(1100); t("e reconecta", r.socks.length, 2);
// mensagem renova o relógio
r = rig(); r.rc.connect(); r.live(); r.advance(14000); r.rc.sock.onmessage({ data: "{}" }); r.advance(14000); r.rc.tick(); t("mensagem renova o vigia", r.rc.sock !== null, true);
// 4) voltar ao app
r = rig(); r.rc.connect(); r.live(); r.advance(2000); r.rc.resume("visible"); t("voltou com socket fresco: não mexe", r.socks.length, 1);
r.advance(8000); r.rc.resume("visible"); t("voltou com socket calado > 5 s: reconecta na hora", [r.socks.length, r.socks[0].closed], [2, true]);
r = rig(); r.rc.connect(); r.live(); r.rc.resume("online"); t("rede voltou: sempre reconecta", r.socks.length, 2);
r = rig(); r.rc.connect(); r.live(); r.rc.sock.onclose({ code: 1006 }); r.rc.resume("pageshow"); t("voltou durante a espera: sem esperar o backoff", [r.socks.length, r.rc.fails], [2, 0]);
r = rig(); r.rc.connect(); r.rc.resume("focus"); t("voltou com handshake pendente: recomeça", r.socks.length, 2);
// 5) tentar agora zera o backoff
r = rig(); r.rc.connect(); for (let i = 0; i < 6; i++) { r.rc.sock.onclose({ code: 1006 }); r.advance(10001); }
r.rc.retryNow(); t("Tentar agora zera as falhas", r.rc.fails, 0); r.rc.sock.onclose({ code: 1006 }); r.advance(1001); t("e volta a 1 s", r.socks.length, 9);
// 6) erros de login param; fechado de propósito não reconecta
for (const c of [4001, 4002, 4003, 4004]) { r = rig(); r.rc.connect(); r.rc.sock.onclose({ code: c }); r.advance(60000); t("código " + c + " não tenta de novo", [r.socks.length, r.fatal], [1, [c]]); }
r = rig(); r.rc.connect(); r.live(); r.rc.stop(); r.advance(60000); t("stop() não reconecta", [r.socks.length, r.rc.sock], [1, null]);
// 7) onclose atrasado de socket descartado é ignorado
r = rig(); r.rc.connect(); r.live(); const old = r.rc.sock; r.rc.retryNow(); old.onclose && old.onclose({ code: 1006 }); t("close do socket velho é ignorado", [r.socks.length, r.rc.sock === r.socks[1]], [2, true]);
console.log(fail ? `\n${fail} FALHA(S)` : "\ntudo certo"); process.exit(fail ? 1 : 0);
