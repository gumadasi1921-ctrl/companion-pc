// PC que recusa/não responde (bloco PCF em web/index.html): texto por motivo, destino do "Voltar", prazo do "Reconectando…". Rodar: node tests/test_pcfail.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* PCF:begin \*\/([\s\S]*?)\/\* PCF:end \*\//);
if (!m) { console.log("FALHA bloco PCF não encontrado"); process.exit(1); }
const sb = { String }; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.i = pcFailInfo; this.b = pcBackTarget; this.s = pcStuck; this.MS = PCF_STUCK_MS;", sb);
let fail = 0;
const t = (n, ok) => { if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", n); };
const L = [{ id: "a", name: "Casa" }, { id: "b", name: "Trabalho" }, { id: "c", name: "Sala" }];

t("origem: cita o PC e allowed_origins", /Trabalho/.test(sb.i("origin", "Trabalho").msg) && /allowed_origins/.test(sb.i("origin", "Trabalho").msg) && /não aceita este endereço/.test(sb.i("origin", "x").title));
t("token: manda parear de novo", /Pareie de novo/.test(sb.i("token", "X").msg));
t("cada motivo tem título e texto diferentes", new Set(["origin", "token", "old", "other", "https", "?"].map((k) => sb.i(k, "X").title)).size === 6);
t("sem nome não quebra", sb.i("origin", "").msg.includes("este PC") && sb.i("origin", null).msg.length > 10);

t("volta para o anterior", JSON.stringify(sb.b(L, "b", "a")) === JSON.stringify({ id: "a", name: "Casa", prev: true }));
t("anterior = o atual: vai para o primeiro outro", JSON.stringify(sb.b(L, "b", "b")) === JSON.stringify({ id: "a", name: "Casa", prev: false }));
t("anterior removido: primeiro outro", sb.b(L, "a", "zzz").id === "b" && sb.b(L, "a", "zzz").prev === false);
t("sem anterior guardado: primeiro outro", sb.b(L, "a", null).id === "b");
t("um PC só: nada", sb.b([L[0]], "a", "a") === null && sb.b([], "a", null) === null && sb.b(null, "a", null) === null);

t("15 s sem conexão = preso", sb.s(1000, 1000 + sb.MS) === true && sb.s(1000, 1000 + sb.MS - 1) === false);
t("conectado (downAt 0) nunca preso", sb.s(0, 999999) === false);
process.exit(fail ? 1 : 0);
