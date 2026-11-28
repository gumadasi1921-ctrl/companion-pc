// Capturador de erros de boot (bloco BOOTERR em web/index.html). Rodar: node tests/test_booterr.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* BOOTERR:begin \*\/([\s\S]*?)\/\* BOOTERR:end \*\//);
if (!m) { console.log("FALHA bloco BOOTERR não encontrado"); process.exit(1); }
let fail = 0;
const t = (n, ok, extra) => { if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", n, ok ? "" : `(${extra})`); };

function rig({ body = true, build = "1795885808", ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X)" } = {}) {
  const el = (tag) => ({ tag, style: {}, children: [], attrs: {}, listeners: {}, textContent: "", id: "", type: "",
    setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); return c; }, addEventListener(e, f) { this.listeners[e] = f; }, select() {}, nodeType: 1 });
  const root = el("html"), bodyEl = el("body");
  const logs = [], listeners = {}, win = { addEventListener: (e, f) => { listeners[e] = f; } };
  const doc = { body: body ? bodyEl : null, documentElement: root, createElement: el, querySelector: () => (build ? { content: build } : null) };
  const timers = [];
  const sb = { window: win, document: doc, navigator: { userAgent: ua, clipboard: { writeText: (s) => { sb.copied = s; return { then: (ok) => ok() }; } } },
    location: { reload: () => { sb.reloaded = true; } }, console: { warn: (s) => logs.push(s) }, setTimeout: (f, ms) => timers.push([f, ms]), String, RegExp };
  vm.createContext(sb);
  vm.runInContext(m[1], sb);
  const host = body ? bodyEl : root;
  return { sb, win, listeners, logs, timers, host, err: (message, extra = {}) => listeners.error({ message, ...extra }), rej: (reason) => listeners.unhandledrejection({ reason }),
    box: () => host.children.find((c) => c.id === "boot-err") };
}
const spanText = (r) => r.box().children[0].textContent;

let r = rig();
r.err("TypeError: undefined is not an object (evaluating 'x.y')", { lineno: 120, colno: 7 });
t("erro de boot mostra a faixa com mensagem e linha", !!r.box() && /Erro ao iniciar/.test(spanText(r)) && /evaluating 'x\.y'/.test(spanText(r)) && /linha 120:7/.test(spanText(r)), r.box() && spanText(r));
t("faixa traz a versão (build)", /build 1795885808/.test(spanText(r)), spanText(r));
t("faixa é alerta fixo no topo", r.box().attrs.role === "alert" && r.box().style.cssText.includes("position:fixed") && r.box().style.cssText.includes("top:0"));
const btns = r.box().children.filter((c) => c.tag === "button");
t("botões Copiar e Recarregar com alvo >= 44 px", btns.map((b) => b.textContent).join() === "Copiar,Recarregar" && btns.every((b) => /min-height:44px/.test(b.style.cssText) && /min-width:44px/.test(b.style.cssText)));
btns[0].listeners.click();
t("Copiar leva versão, iOS e mensagem", /build 1795885808/.test(r.sb.copied) && /iOS 18\.7/.test(r.sb.copied) && /evaluating 'x\.y'/.test(r.sb.copied) && btns[0].textContent === "Copiado", r.sb.copied);
btns[1].listeners.click();
t("Recarregar recarrega a página", r.sb.reloaded === true);

r = rig();
r.err("Falhou em https://casa.exemplo.ts.net/ws?k=SEGREDO123 e C:\\Users\\fulano\\companion-pc\\server\\x.py token=ABCDEFGHIJKLMNOPQRSTUVWXYZ012345");
const sp = spanText(r);
t("sem endereço, caminho nem token no texto", !/casa.exemplo|SEGREDO|fulano|companion-pc|ABCDEFGHIJ/.test(sp) && /…/.test(sp), sp);
r.btnCopy = r.box().children[1]; r.btnCopy.listeners.click();
t("texto copiado também sem segredos", !/casa.exemplo|SEGREDO|fulano|ABCDEFGHIJ/.test(r.sb.copied), r.sb.copied);

r = rig();
r.err("x".repeat(500));
t("mensagem longa é cortada", spanText(r).length < 220, spanText(r).length);

r = rig();
r.err("ResizeObserver loop completed with undelivered notifications.");
t("ResizeObserver benigno é ignorado", !r.box());

r = rig();
r.rej(new Error("fetch falhou"));
t("promessa rejeitada no boot aparece", !!r.box() && /promessa rejeitada: fetch falhou/.test(spanText(r)), r.box() && spanText(r));
r.rej("texto puro");
t("segundo erro soma no contador, mantém o primeiro", /\(\+1\)/.test(spanText(r)) && /fetch falhou/.test(spanText(r)), spanText(r));
t("uma só faixa", r.host.children.filter((c) => c.id === "boot-err").length === 1);

r = rig();
r.sb.window.__bootEnd();
r.err("TypeError: depois do boot");
t("depois do boot: sem faixa, só console", !r.box() && r.logs.length === 1 && /depois do boot/.test(r.logs[0]), JSON.stringify(r.logs));

r = rig();
t("fim do boot automático em 30 s", r.timers.some(([f, ms]) => ms === 30000 && (f(), true)));
r.err("TypeError: tarde");
t("depois dos 30 s: sem faixa", !r.box());

r = rig();
r.listeners.error({ message: "Falha de imagem", target: { nodeType: 1 } });
t("falha de carregar recurso (img/script) não conta", !r.box());

r = rig({ body: false, build: "" , ua: "Mozilla/5.0 (X11)" });
r.err("SyntaxError: Unexpected token");
t("sem <body> ainda: anexa no documentElement; sem meta de build não quebra", !!r.box() && /Erro ao iniciar/.test(spanText(r)) && !/build/.test(spanText(r)), r.box() && spanText(r));

// o app avisa que o boot acabou nos 3 caminhos (boas-vindas, login, PC recusado)
t("app chama __bootEnd na boas-vindas, no login e na tela de PC recusado", (html.match(/window\.__bootEnd\)\s*window\.__bootEnd\(\)/g) || []).length === 3, (html.match(/window\.__bootEnd\)\s*window\.__bootEnd\(\)/g) || []).length);
t("o bloco é o 1º script depois do teste de compatibilidade", html.indexOf("BOOTERR:begin") < html.indexOf("window.pccTheme") && html.indexOf("BOOTERR:begin") > html.indexOf("legacy.html"));
process.exit(fail ? 1 : 0);
