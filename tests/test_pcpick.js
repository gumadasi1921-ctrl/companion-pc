// Vários PCs atrás do mesmo endereço (bloco PCP em web/index.html): escolha da entrada pelo pc_id, plano B, onde guardar o token pareado.
// Rodar: node tests/test_pcpick.js   (só ids e endereços de exemplo)
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* PCP:begin \*\/([\s\S]*?)\/\* PCP:end \*\//);
if (!m) { console.log("FALHA bloco PCP não encontrado"); process.exit(1); }
const sb = {}; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.f = { needWho: pcpNeedWho, choose: pcpChoose, order: pcpOrder, resolve: pcpResolvePaired, sharers: pcpSharers };", sb);
const F = sb.f;
let fail = 0;
const t = (n, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", n, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };
const NEUTRO = "https://neutro.exemplo.ts.net", PA = "aaaaaaaaaaaaaaaa", PB = "bbbbbbbbbbbbbbbb", PC = "cccccccccccccccc";
const L = (...e) => e.map(([id, base, pcid, hasToken]) => ({ id, base, pcid, hasToken: !!hasToken, name: id }));

t("sem endereço compartilhado não pergunta quem é", F.needWho(L(["1", "https://a.ts.net", PA, 1], ["2", "https://b.ts.net", PB, 1]), { id: "1", base: "https://a.ts.net" }), false);
t("2 entradas no mesmo endereço = pergunta", F.needWho(L(["1", NEUTRO, PA, 1], ["2", NEUTRO, PB, 1]), { id: "1", base: NEUTRO }), true);
t("sem ativa não quebra", F.needWho(L(["1", NEUTRO, PA, 1]), null), false);

let l = L(["1", NEUTRO, PA, 1], ["2", NEUTRO, PB, 1]);
t("respondeu a ativa: usa", F.choose(l, "1", PA), { action: "use", id: "1" });
t("respondeu o outro PC já pareado: troca para a entrada dele", F.choose(l, "1", PB), { action: "switch", id: "2" });
t("não confunde endereços diferentes", F.choose(L(["1", NEUTRO, PA, 1], ["2", "https://x.ts.net", PB, 1]), "1", PB), { action: "pair" });
t("PC desconhecido e sem entrada livre: pareia", F.choose(l, "1", PC), { action: "pair" });
l = L(["1", NEUTRO, PA, 1], ["2", NEUTRO, null, 0]);
t("PC desconhecido com entrada sem pc_id: adota essa", F.choose(l, "1", PC), { action: "switch", id: "2", adopt: true });
l = L(["1", NEUTRO, null, 1], ["2", NEUTRO, PB, 1]);
t("ativa sem pc_id adota o que respondeu", F.choose(l, "1", PA), { action: "use", id: "1", adopt: true });
t("ativa sem pc_id mas o que respondeu já tem entrada: troca", F.choose(l, "1", PB), { action: "switch", id: "2" });
t("sem pc_id no who: segue com a ativa", F.choose(l, "1", ""), { action: "use", id: "1" });

l = L(["1", NEUTRO, PA, 1], ["2", NEUTRO, PB, 1], ["3", NEUTRO, PC, 0], ["4", "https://x.ts.net", PA, 1]);
t("plano B: ativa primeiro, depois as do mesmo endereço com token", F.order(l, "2"), ["2", "1"]);
t("plano B: ignora entrada sem token e de outro endereço", F.order(l, "1"), ["1", "2"]);
t("plano B: sem ativa = nada", F.order(l, "zzz"), []);

l = L(["1", NEUTRO, PA, 1], ["2", NEUTRO, PB, 1]);
t("pareou e respondeu o PC da ativa: token na ativa", F.resolve(l, "1", PA), { id: "1" });
t("pareou e respondeu OUTRO PC já listado: token na entrada dele (não na ativa)", F.resolve(l, "1", PB), { id: "2" });
t("pareou PC novo sem entrada livre: cria entrada", F.resolve(l, "1", PC), { create: true });
l = L(["1", NEUTRO, null, 0]);
t("entrada nova esperando pareamento recebe o token", F.resolve(l, "1", PA), { id: "1" });
l = L(["1", NEUTRO, null, 0], ["2", NEUTRO, PB, 1]);
t("espera pareamento mas respondeu PC já listado: token vai para ele", F.resolve(l, "1", PB), { id: "2" });
l = L(["1", NEUTRO, PA, 1], ["2", NEUTRO, null, 0]);
t("respondeu PC novo e há entrada livre: usa a livre", F.resolve(l, "1", PC), { id: "2" });
t("servidor antigo sem pc_id: token na ativa", F.resolve(l, "1", undefined), { id: "1" });

// integração com o app (texto): token por entrada, 4001 do plano B não apaga token, who antes do hello
const src = html;
t("o hello usa o token da entrada escolhida (sendHello(RCON.sock, tok))", /sendHello\(RCON\.sock, tok\)/.test(src), true);
t("plano B não apaga token (o ramo fb sai antes do setToken)", src.indexOf("if (code === 4001 && fb)") < src.indexOf('PCL.setToken("", rconPcId)'), true);
t("paired só guarda o token (a entrada é decidida no welcome)", /case "paired": pairTok = m\.token;/.test(src), true);
process.exit(fail ? 1 : 0);
