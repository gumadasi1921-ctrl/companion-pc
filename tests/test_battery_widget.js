// Widget Bateria (bloco BTW em web/index.html): nome curto, cor por %, ícone, estado, ordem. Rodar: node tests/test_battery_widget.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* BTW:begin \*\/([\s\S]*?)\/\* BTW:end \*\//);
if (!m) { console.log("FALHA bloco BTW não encontrado"); process.exit(1); }
const sb = { String, Array, Number }; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.B = BTW;", sb);
const B = sb.B;
let fail = 0;
const t = (n, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", n, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };

t("cor: verde, amarelo < 30, vermelho < 15", [100, 30, 29, 15, 14, 0].map((p) => B.tone(p, false)), ["ok", "ok", "mid", "mid", "lo", "lo"]);
t("carregando é sempre verde", [B.tone(5, true), B.tone(20, true)], ["ok", "ok"]);
t("nome curto: mouse do receptor", B.short("Mouse (receptor)", "mouse"), "Mouse");
t("nome curto: tira parênteses e fica com 2 palavras", B.short("JBL Tune 510BT", ""), "JBL Tune");
t("nome curto: 1 palavra", B.short("JBL", ""), "JBL");
t("nome curto: 2 palavras longas = 1ª", B.short("Fone Headphones Pro", ""), "Fone Headphones".length > 12 ? "Fone" : "Fone Headphones");
t("nome curto: palavra enorme corta com …", B.short("SuperLongDeviceName123", ""), "SuperLongDe…");
t("nome curto: vazio", [B.short("", ""), B.short(null, ""), B.short("(x)", "")], ["Aparelho", "Aparelho", "Aparelho"]);
t("ícones", [B.icon({ kind: "mouse", name: "x" }), B.icon({ name: "MX Master 3" }), B.icon({ name: "Bluetooth Mouse" }), B.icon({ name: "Teclado BT" }), B.icon({ name: "JBL Tune" })], ["mouse", "mouse", "mouse", "battery", "headphones"]);
t("estado", [B.state({ pct: 80 }), B.state({ pct: 14 }), B.state({ pct: 14, charging: true }), B.state({ pct: 15 })], ["", "Bateria baixa", "Carregando", ""]);
t("limpar: só 0..100 inteiro, do menor para o maior", B.clean([{ name: "B", pct: 50 }, { name: "A", pct: 10 }, { name: "X", pct: 101 }, { name: "Y", pct: "5" }, { name: "Z", pct: 50.5 }, null, { name: "C", pct: 0 }]).map((i) => i.pct), [0, 10, 50]);
t("limpar: entrada inválida = lista vazia", [B.clean(null), B.clean({}), B.clean(undefined)], [[], [], []]);
t("barra de terminal", [B.bar(0), B.bar(82), B.bar(100)], ["░░░░░░░░░░", "████████░░", "██████████"]);
process.exit(fail ? 1 : 0);
