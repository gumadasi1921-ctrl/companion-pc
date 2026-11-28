// Layout do Início por PC (bloco PCH em web/index.html). Rodar: node tests/test_pchome.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* PCH:begin \*\/([\s\S]*?)\/\* PCH:end \*\//);
if (!m) { console.log("FALHA bloco PCH não encontrado"); process.exit(1); }
const sb = { Object, Array, Number }; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.makePcHome = makePcHome;", sb);
let fail = 0;
const t = (n, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", n, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };
const mk = () => { const d = {}; const store = { get: (k, def) => (k in d ? JSON.parse(JSON.stringify(d[k])) : def), set: (k, v) => { d[k] = JSON.parse(JSON.stringify(v)); }, del: (k) => { delete d[k]; } };
  return { d, store, pch: sb.makePcHome(store, (v) => (v && typeof v === "object" && [1, 2, 3].includes(v.v) ? v : null)) }; };
const H = (n) => ({ v: 3, tablet: [{ id: "w" + n, size: "P", page: 0, c: 1, r: 1 }] });

let r = mk(); r.store.set("home", H(1)); r.store.set("homepg", { land: 1 });
t("1ª vez: PC novo copia o layout atual", [r.pch.activate("A", "B"), r.d.home.tablet[0].id, r.d.homepg], [false, "w1", { land: 1 }]);
t("os dois PCs passam a ter layout", Object.keys(r.d.pchome).sort(), ["A", "B"]);
r.store.set("home", H(2)); r.store.set("homepg", { land: 0 });   // edita no B
t("volta ao A: layout do A (não o editado no B)", [r.pch.activate("B", "A"), r.d.home.tablet[0].id, r.d.homepg], [true, "w1", { land: 1 }]);
t("o B guardou a própria edição", [r.pch.activate("A", "B"), r.d.home.tablet[0].id, r.d.homepg], [true, "w2", { land: 0 }]);
r.pch.activate("B", "B"); t("trocar para o mesmo PC não mexe", r.d.home.tablet[0].id, "w2");
// PC ativo sem layout personalizado (home ausente)
r = mk(); r.pch.activate("A", "B"); t("sem home: guarda null e o novo também fica sem", [r.d.pchome.A.home, r.d.pchome.B.home, "home" in r.d], [null, null, false]);
r.store.set("home", H(3)); r.pch.activate("B", "A"); t("A sem layout volta ao padrão (home apagado)", "home" in r.d, false);
// copiar
r = mk(); r.store.set("home", H(1)); r.pch.activate("A", "B"); r.store.set("home", H(2)); r.pch.activate("B", "C");   // C copia o do B (w2)
t("copiar origem sem layout próprio falha", r.pch.copy("Z", "C", "C"), false);
t("copiar do A para o ativo C", [r.pch.copy("A", "C", "C"), r.d.home.tablet[0].id, r.d.pchome.C.home.tablet[0].id], [true, "w1", "w1"]);
t("copiar do ativo para outro PC grava só nele", [r.pch.copy("C", "B", "C"), r.d.pchome.B.home.tablet[0].id, r.d.home.tablet[0].id], [true, "w1", "w1"]);
t("copiar para si mesmo é recusado", r.pch.copy("C", "C", "C"), false);
r.pch.remove("B"); t("remover apaga a entrada", Object.keys(r.d.pchome).sort(), ["A", "C"]);
// dados ruins
r = mk(); r.d.pchome = { A: { home: { v: 99 } }, B: "x", C: { home: H(5), homepg: { land: 99, port: 2, evil: 1 } } };
t("entradas inválidas descartadas e páginas limpas", [Object.keys(r.pch.all()), r.pch.all().C.homepg], [["C"], { port: 2 }]);
r.d.pchome = [1, 2]; t("pchome que não é objeto = vazio", r.pch.all(), {});
console.log(fail ? `\n${fail} FALHA(S)` : "\ntudo certo"); process.exit(fail ? 1 : 0);
