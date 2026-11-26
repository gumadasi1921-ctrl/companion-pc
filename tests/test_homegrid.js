// Grade do Início (HG em web/index.html): posição por widget, troca de lugar, gravidade vertical, páginas, migração. Rodar: node tests/test_homegrid.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* HG:begin \*\/([\s\S]*?)\/\* HG:end \*\//);
if (!m) { console.log("FALHA bloco HG não encontrado"); process.exit(1); }
const sb = { Array, Object, Set, Math, Number, Error }; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.HG = HG;", sb);
const HG = sb.HG;
let fail = 0;
const t = (name, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", name, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };
const at = (l, id) => { const x = l.find((y) => y.id === id); return x ? [x.page, x.c, x.r, x.size] : null; };
const overlap = (l, mode) => { const d = HG.dims(mode), seen = new Set(); for (const x of l) { const [w, h] = HG.SZ[x.size]; for (let y = x.r; y < x.r + h; y++) for (let c = x.c; c < x.c + w; c++) { const k = x.page + ":" + c + ":" + y; if (seen.has(k) || c > d.C || y > d.R) return true; seen.add(k); } } return false; };
const P = (id, size, page, c, r) => ({ id, size, page, c, r });

// ---- o caso do print (844x390 deitado, 3×2): Tocando A c1; PC P c2r1; Tarefas P c3r1
{
  const L0 = [P("now", "A", 0, 1, 1), P("pcquick", "P", 0, 2, 1), P("tasks", "P", 0, 3, 1)];
  const s = HG.settle(L0, "land"); t("posições válidas ficam como estão", s.list.map((x) => [x.id, x.page, x.c, x.r]), [["now", 0, 1, 1], ["pcquick", 0, 2, 1], ["tasks", 0, 3, 1]]);
  const mv = HG.move(L0, "tasks", { page: 0, c: 2, r: 2 }, "land");
  t("Tarefas vai para c2r2 (embaixo do PC)", at(mv, "tasks"), [0, 2, 2, "P"]);
  t("PC e Tocando não se mexem", [at(mv, "pcquick"), at(mv, "now")], [[0, 2, 1, "P"], [0, 1, 1, "A"]]);
  const ad = HG.add(mv, "cpu", "A", "land");
  t("a coluna 3 ficou livre: outro widget A entra nela, na mesma página", at(ad, "cpu"), [0, 3, 1, "A"]);
  t("sem página nova", HG.pagesUsed(ad), 1);
  t("sem sobreposição", overlap(ad, "land"), false);
  t("o que sobrou não preenche a coluna 3 sozinho (gravidade só vertical)", HG.move(L0, "tasks", { page: 0, c: 2, r: 2 }, "land").some((x) => x.id === "tasks" && x.c === 3), false);
}
// ---- soltar em cima de outro: troca de lugar
{
  const L = [P("a", "L", 0, 1, 1), P("b", "P", 0, 3, 1), P("c", "L", 0, 1, 2), P("d", "P", 0, 3, 2)];
  const mv = HG.move(L, "a", { page: 0, c: 1, r: 2 }, "land");
  t("L sobre L troca de lugar", [at(mv, "a"), at(mv, "c")], [[0, 1, 2, "L"], [0, 1, 1, "L"]]);
  const mv2 = HG.move(L, "b", { page: 0, c: 3, r: 2 }, "land");
  t("P sobre P troca de lugar", [at(mv2, "b"), at(mv2, "d")], [[0, 3, 2, "P"], [0, 3, 1, "P"]]);
}
// ---- gravidade: sobe na própria coluna, nunca para o lado
{
  const mv = HG.move([P("a", "P", 0, 1, 1)], "a", { page: 0, c: 3, r: 2 }, "land");
  t("soltar embaixo de espaço livre: sobe até o topo da coluna", at(mv, "a"), [0, 3, 1, "P"]);
  t("remover sobe o que estava embaixo", at(HG.remove([P("a", "P", 0, 1, 1), P("b", "P", 0, 1, 2)], "a", "land"), "b"), [0, 1, 1, "P"]);
}
// ---- não cabe = null (nada muda); página cheia não cria buraco falso
{
  const full = ["a", "b", "c", "d", "e", "f"].map((id, i) => P(id, "P", 0, (i % 3) + 1, Math.floor(i / 3) + 1));
  t("página cheia: soltar sobre outro troca de lugar, sem página nova", [at(HG.move(full, "a", { page: 0, c: 2, r: 1 }, "land"), "b"), HG.pagesUsed(HG.move(full, "a", { page: 0, c: 2, r: 1 }, "land"))], [[0, 1, 1, "P"], 1]);
  const g = []; for (let i = 0; i < 18; i++) g.push(P("w" + i, "P", Math.floor(i / 6), (i % 3) + 1, Math.floor((i % 6) / 3) + 1));
  t("3 páginas cheias: mover G para dentro não cabe", HG.resize(g, "w0", "G", "land"), null);
  t("3 páginas cheias: adicionar não cabe", HG.add(g, "x", "P", "land"), null);
  const half = [P("a", "G", 0, 1, 1)];
  t("G + P: o P entra ao lado, ainda na página 1", at(HG.add(half, "b", "P", "land"), "b"), [0, 3, 1, "P"]);
  t("A não cabe em buraco de 1 linha: vai para a coluna livre ou página nova, nunca sobrepõe", overlap(HG.add([P("a", "G", 0, 1, 1), P("p", "P", 0, 3, 1)], "z", "A", "land"), "land"), false);
}
// ---- páginas
{
  const L = [P("a", "G", 0, 1, 1), P("b", "P", 0, 3, 1), P("c", "P", 0, 3, 2), P("d", "G", 1, 1, 1)];
  const mv = HG.move(L, "d", { page: 0, c: 3, r: 1 }, "land");
  t("G arrastado para a borda direita é recolocado dentro da grade (c ≤ 2)", at(mv, "d")[1] <= 2, true);
  t("página vazia some (renumera)", HG.pagesUsed(HG.move([P("a", "P", 0, 1, 1), P("b", "P", 2, 1, 1)], "b", { page: 0, c: 2, r: 1 }, "land")), 1);
  const pg = HG.move([P("a", "P", 0, 1, 1)], "a", { page: 1, c: 1, r: 1 }, "land");
  t("movido para a página 2 sozinho: vira a página 1 (nada de página vazia no meio)", at(pg, "a"), [0, 1, 1, "P"]);
  const two = HG.move([P("a", "P", 0, 1, 1), P("b", "P", 0, 2, 1)], "b", { page: 1, c: 1, r: 1 }, "land");
  t("duas páginas usadas", [HG.pagesUsed(two), at(two, "b")], [2, [1, 1, 1, "P"]]);
}
// ---- tamanho
{
  const L = [P("a", "P", 0, 1, 1), P("b", "P", 0, 2, 1), P("c", "P", 0, 3, 1)];
  const r = HG.resize(L, "a", "A", "land"); t("P→A cresce para baixo", at(r, "a"), [0, 1, 1, "A"]);
  const r2 = HG.resize(L, "a", "L", "land"); t("P→L empurra o vizinho, sem sobrepor", [at(r2, "a"), overlap(r2, "land")], [[0, 1, 1, "L"], false]);
  const r3 = HG.resize([P("a", "P", 0, 3, 1)], "a", "G", "land"); t("G na borda direita é puxado para dentro", at(r3, "a"), [0, 2, 1, "G"]);
}
// ---- em pé: 2 colunas, linhas que rolam
{
  const L = HG.fromOrder([{ id: "a", size: "P" }, { id: "b", size: "P" }, { id: "c", size: "L" }, { id: "d", size: "A" }, { id: "e", size: "G" }], "port");
  t("em pé: P P / L / A + vazio ao lado", L.list.map((x) => [x.id, x.c, x.r]), [["a", 1, 1], ["b", 2, 1], ["c", 1, 2], ["d", 1, 3], ["e", 1, 5]]);
  t("em pé: só 1 página", HG.pagesUsed(L.list), 1);
  const big = []; for (let i = 0; i < 30; i++) big.push({ id: "w" + i, size: "G" });
  t("em pé: 30 G cabem (60 linhas)", HG.fromOrder(big, "port").over.length, 0);
  const mv = HG.move(L.list, "e", { page: 0, c: 2, r: 3 }, "port"); t("em pé: G em c2 é puxado para c1 (largura 2)", at(mv, "e")[1], 1);
}
// ---- lista estragada
{
  const s = HG.settle([P("a", "P", 0, 1, 1), P("b", "P", 0, 1, 1), { id: "c", size: "ZZ" }, { id: "a", size: "P" }, null, { id: 5 }, P("d", "P", 9, 99, 99)], "land");
  t("sobreposto, repetido, tamanho inválido e posição absurda: tudo arrumado sem sobrepor", [s.list.map((x) => x.id).sort(), overlap(s.list, "land"), s.over], [["a", "b", "c", "d"], false, []]);
  t("tamanho inválido vira P", at(s.list, "c")[3], "P");
  const o = HG.settle(Array.from({ length: 25 }, (_, i) => ({ id: "w" + i, size: "P" })), "land");
  t("o que não cabe em 3 páginas vai para over", [o.list.length, o.over.length], [18, 7]);
}
// ---- migração: pela ordem, igual à distribuição antiga (padrão do celular e do tablet)
{
  const phone = [["now", "L"], ["weather", "P"], ["tasks", "L"], ["pcquick", "P"]].map(([id, size]) => ({ id, size }));
  t("padrão do celular deitado, igual a hoje", HG.fromOrder(phone, "land").list.map((x) => [x.id, x.page, x.c, x.r]), [["now", 0, 1, 1], ["weather", 0, 3, 1], ["tasks", 0, 1, 2], ["pcquick", 0, 3, 2]]);
  const tab = [["cpu", "L"], ["net", "L"], ["now", "G"], ["tasks", "A"], ["mem", "A"], ["work", "L"], ["games", "L"], ["procs", "L"], ["notif", "L"], ["quick", "L"], ["weather", "L"]].map(([id, size]) => ({ id, size }));
  const T = HG.fromOrder(tab, "tablet"); t("padrão do tablet cabe todo, sem sobrepor", [T.over.length, overlap(T.list, "tablet")], [0, false]);
  t("é idempotente (settle do resultado não muda nada)", HG.settle(T.list, "tablet").list, T.list);
}
process.exit(fail ? 1 : 0);
