// Núcleo do Lume (makeMascot em web/index.html): medidores, sono, lembretes, textos, histórico. Rodar: node tests/test_mascot.js
const fs = require("fs"), vm = require("vm"), path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "windows", "server", "web", "index.html"), "utf8");
const m = html.match(/\/\* LUME:begin \*\/([\s\S]*?)\/\* LUME:end \*\//);
if (!m) { console.log("FALHA bloco LUME não encontrado"); process.exit(1); }
let fail = 0;
const t = (name, got, want) => { const ok = JSON.stringify(got) === JSON.stringify(want); if (!ok) fail++; console.log(ok ? "OK   " : "FALHA", name, ok ? "" : `(${JSON.stringify(got)} != ${JSON.stringify(want)})`); };
const T = (h, mi = 0, d = 25) => new Date(2026, 10, d, h, mi).getTime() / 1000;   // 25/nov/2026 no fuso local
let clock = T(9);
function fresh(initial) {
  const mem = new Map(initial ? [["mascot", JSON.stringify(initial)]] : []);
  const store = { get: (k, d) => (mem.has(k) ? JSON.parse(mem.get(k)) : d), set: (k, v) => mem.set(k, JSON.stringify(v)), del: (k) => mem.delete(k) };
  const sb = { Date, Math, JSON, Number, String, Object, Array, Set, Error }; vm.createContext(sb); vm.runInContext(m[1] + "\nthis.make = makeMascot;", sb);
  return { M: sb.make(store, () => clock), mem, store, sb };
}

// ---- estado inicial e medidores
{
  clock = T(9); const { M } = fresh(); const v = M.view(clock);
  t("começa feliz e com tudo em 80", [v.st, v.needs.water, v.needs.sat, v.needs.energy], ["happy", 80, 80, 80]);
  clock = T(10, 30);   // 90 min ativos
  const w = M.view(clock);
  t("sem cuidado a água cai 0,5/min (80 → 35 → piso 30)", w.needs.water, 35);
  t("com sede abaixo de 55", w.st, "thirst");
  clock = T(21, 59); t("nunca abaixo do piso", M.view(clock).needs.water, 30);
  clock = T(23, 0); const a = M.view(clock).needs.sat;
  clock = T(23, 59); t("fora do horário ativo nada desce", M.view(clock).needs.sat, a);
}
// ---- só sobem com ação real
{
  clock = T(9); const { M } = fresh(); clock = T(12);
  t("bebi água sobe", [M.act("water", clock).ok, M.view(clock).needs.water], [true, 75]);
  clock += 60; t("água de novo em 1 min = recusa", M.act("water", clock), { ok: false, why: "Ele ainda está bem hidratado" });
  t("petisco sem estoque = recusa", M.act("feed", clock).ok, false);
  t("tarefa dá petisco", [M.act("task", clock, { id: "a" }).ok, M.snacks()], [true, 1]);
  t("mesma tarefa no mesmo dia não conta de novo", M.act("task", clock, { id: "a" }).ok, false);
  t("foco dá petisco", [M.act("focus", clock).ok, M.snacks()], [true, 2]);
  t("petisco gasta o estoque", [M.act("feed", clock).ok, M.snacks()], [true, 1]);
  t("pausa sobe energia", M.act("pause", clock).ok, true);
  t("ação desconhecida", M.act("xyz", clock).ok, false);
  const c = M.counts(clock); t("contagens do dia", [c.water, c.pause, c.focus, c.task, c.feed], [1, 1, 1, 1, 1]);
}
// ---- estados e prioridade
{
  clock = T(9); const { M } = fresh();
  t("foco ganha de tudo menos sono", [M.view(clock, { focus: true, alert: true }).st, M.view(clock, { alert: true, party: true }).st, M.view(clock, { party: true }).st], ["focus", "alert", "party"]);
  M.sleep(30, clock); t("dormindo ganha de foco", [M.sleeping(clock), M.view(clock, { focus: true }).st], [true, "sleep"]);
  t("acorda sozinho depois do tempo", M.sleeping(clock + 31 * 60), false);
  M.wake(clock); t("wake interrompe", M.sleeping(clock), false);
  M.sleep("day", clock); t("até amanhã = amanhã 08:00", M.sleepUntil(), T(8, 0, 26));
}
// ---- lembretes: horário, limite, ignorados, silêncio, foco
{
  clock = T(9); const { M } = fresh();
  t("9:00 não há lembrete", M.due(clock, {}), null);
  clock = T(9, 31); t("água vence 90 min depois de 08:00 → 09:30", M.due(clock, {}), { id: "water" });
  t("texto da água é fixo, sem número", /água/i.test(M.text("water", {}, clock)), true);
  t("foco calado", M.due(clock, { focus: true }), null);
  M.setCfg({ water: { min: 60 } }); t("ajustável: 60 min", M.cfg().water.min, 60);
  t("app aberto agora: só lembra 60 min depois de abrir", [M.due(clock, { since: clock }), M.due(clock + 61 * 60, { since: clock })], [null, { id: "water" }]);
  M.say("water", clock); t("depois de falar não repete na hora", M.due(clock + 60, {}), null);
  t("e dentro de 15 min nada fala", M.can("pause", clock + 600), false);
  clock = T(11); t("água de novo após o intervalo", M.due(clock, {}), { id: "water" });
  M.ignored("water", clock); M.ignored("water", clock); t("ignorou 2 = para até amanhã", M.can("water", clock + 99999 > T(22) ? clock : clock + 3600), false);
  clock = T(9, 31, 26); t("dia seguinte volta", M.can("water", clock), true);
  M.silence(true, clock); t("silenciar hoje", [M.silenced(clock), M.due(clock, {})], [true, null]);
  t("silêncio expira no dia seguinte", M.silenced(clock + 86400), false);
}
{
  clock = T(7); const { M } = fresh();
  t("fora do horário ativo não fala", [M.inActive(clock), M.can("water", clock)], [false, false]);
  clock = T(10); M.setCfg({ maxDay: 3 });
  for (let i = 0; i < 3; i++) { clock += 20 * 60; M.say("x" + i, clock); }
  t("limite diário de falas", M.can("water", clock + 3600), false);
}
// ---- pausa por trabalho contínuo (dado real do servidor)
{
  clock = T(10); const { M } = fresh(); M.setCfg({ water: { on: false } });
  t("sem dado do servidor = nada", M.due(clock, { work: null }), null);
  t("abaixo do limite = nada", M.due(clock, { work: { minutes: 89, app: "Photoshop" } }), null);
  const d = M.due(clock, { work: { minutes: 100, app: "Photoshop" } });
  t("vence com minutos e app reais", d, { id: "pause", minutes: 100, app: "Photoshop" });
  t("texto usa o número real", M.text("pause", d), "Você está há 1 h 40 seguidos no Photoshop. Que tal uma pausa?");
  M.say("pause", clock, d); clock += 20 * 60;
  t("não repete antes de +30 min", M.due(clock, { work: { minutes: 110, app: "Photoshop" } }), null);
  clock += 20 * 60; t("repete em +30 min, no máximo 3 vezes", M.due(clock, { work: { minutes: 131, app: "Photoshop" } }).id, "pause");
  M.say("pause", clock, { minutes: 131 }); clock += 20 * 60; M.say("pause", clock, { minutes: 165 }); clock += 20 * 60;
  t("3 avisos por sequência", M.due(clock, { work: { minutes: 200, app: "Photoshop" } }), null);
  t("sequência nova reinicia", M.due(clock, { work: { minutes: 95, app: "CapCut" } }).id, "pause");
  t("texto sem minutos reais = null", M.text("pause", {}), null);
}
// ---- fim do dia: só com números reais
{
  clock = T(18, 29); const { M } = fresh(); M.setCfg({ water: { on: false } });
  t("antes do horário nada", M.due(clock, {}), null);
  clock = T(18, 31); t("18:30 pede os dados", M.due(clock, {}), { id: "eod", needData: true });
  t("com horas e tarefas reais", M.text("eod", { work_s: 4 * 3600 + 720, done: 3, pend: 2 }), "Hoje: 4 h 12 de trabalho · 3 tarefas feitas · restam 2");
  t("1 tarefa", M.text("eod", { work_s: 0, done: 1, pend: 1 }), "Hoje: 1 tarefa feita · resta 1");
  t("sem dado nenhum = não fala", M.text("eod", { work_s: 0, done: 0, pend: 0 }), null);
  M.eodDone(clock); t("só uma vez por dia", M.due(clock + 60, {}), null);
  clock = T(18, 31, 26); t("no dia seguinte volta", M.due(clock, {}).id, "eod");
  t("fim do dia desligável", [M.setCfg({ eod: { on: false } }).eod.on, M.due(clock, {})], [false, null]);
}
// ---- validação, persistência, histórico, reset
{
  clock = T(9); const { M, mem } = fresh();
  const c = M.setCfg({ mode: "xx", maxDay: 7, active: ["25:00", "10:00"], water: { min: 7 }, eod: { at: "aa" }, route: { notif: "t", pomo: "z" } });
  t("valores inválidos voltam ao padrão", [c.mode, c.maxDay, c.active, c.water.min, c.eod.at, c.route.notif, c.route.pomo], ["on", 6, ["08:00", "22:00"], 90, "18:30", "t", "b"]);
  t("horário ativo válido é aceito", M.setCfg({ active: ["07:30", "23:00"] }).active, ["07:30", "23:00"]);
  M.act("water", clock);
  const raw = JSON.parse(mem.get("mascot")); t("grava só no localStorage do aparelho (chave mascot)", [raw.v, raw.cfg.active], [1, ["07:30", "23:00"]]);
  t("nada de token/PC no que é gravado", /token|pcid|http/i.test(mem.get("mascot")), false);
  const { M: M2 } = fresh(raw); t("recarrega o mesmo estado", M2.cfg().active, ["07:30", "23:00"]);
  t("histórico do dia", M.history(clock, 1)[0].items.map((i) => i[1]), ["water"]);
  const { M: M3 } = fresh({ v: 1, cfg: "lixo", needs: { water: { v: 5, t: "x" } }, log: 7 }); t("estado corrompido não quebra", M3.view(clock).st, "happy");
  const { M: M4 } = fresh({ v: 9 }); t("versão desconhecida = recomeça", M4.view(clock).needs.water, 80);
  M.reset(clock); t("zerar mantém ajustes e apaga histórico", [M.cfg().active, M.history(clock, 1)[0].items.length, M.snacks()], [["07:30", "23:00"], 0, 0]);
}
{
  clock = T(9); const { M } = fresh();
  for (let d = 1; d <= 20; d++) { clock = T(10, 0, d); M.act("water", clock); }
  const days = M.history(clock, 20).filter((x) => x.items.length).length; t("histórico guarda só 14 dias", days <= 14, true);
}
// ---- feitos do Trello/monday contam como tarefa concluída, uma vez por card e por dia
{
  clock = T(9); const { M } = fresh();
  const d = { today: "2026-11-25", done: { today_ids: ["c1", "c2"] } };
  t("2 cards em Aprovar hoje = 2 tarefas", [M.done("tr", d, clock), M.snacks()], [2, 2]);
  t("ler o estado de novo não conta de novo", M.done("tr", d, clock), 0);
  t("card novo conta", M.done("tr", { today: "2026-11-25", done: { today_ids: ["c1", "c2", "c3"] } }, clock), 1);
  t("monday com o mesmo id é outro item", M.done("md", d, clock), 2);
  t("sem today_ids (anteriores nunca vêm) não conta", [M.done("tr", { today: "2026-11-25", done: {} }, clock), M.done("tr", null, clock), M.done("tr", { done: { today_ids: ["z"] } }, clock)], [0, 0, 0]);
  clock = T(9, 0, 26); t("dia seguinte: reentrada com nova data conta de novo", M.done("tr", { today: "2026-11-26", done: { today_ids: ["c1"] } }, clock), 1);
}

// ---- almoço (12:00, dias úteis, 1 por dia, adiar 15 min, foco, virada do dia, 2ª pergunta com nomes reais)
{
  clock = T(9); const { M } = fresh(); M.setCfg({ water: { on: false } }); const D = (h, mi = 0, d = 25) => M.due(T(h, mi, d), {});
  t("padrão: ligado, 12:00, dias úteis", M.cfg().lunch, { on: true, at: "12:00", days: "wk" });
  t("11:59 ainda não", D(11, 59), null);
  t("12:00 pede o almoço (1º pedido)", D(12), { id: "lunch", n: 1, at: "12:00" });
  t("texto do meio-dia", M.text("lunch", { n: 1, at: "12:00" }), "Deu meio-dia, hora do almoço! Já vai almoçar?");
  t("horário ajustado aparece no texto", M.text("lunch", { n: 1, at: "12:30" }), "Deu 12:30, hora do almoço! Já vai almoçar?");
  clock = T(12); t("pedir conta 1", M.lunchAsk(clock), 1);
  t("não repete sem adiar (sem resposta some sozinho)", [D(12, 5), D(12, 40)], [null, null]);
  M.lunchLater(T(12, 1)); t("adiar: antes dos 15 min nada", D(12, 15), null);
  t("adiar: 12:16 repete", D(12, 16), { id: "lunch", n: 2, at: "12:00" });
  t("2º pedido tem texto próprio", M.text("lunch", { n: 2, at: "12:00" }), "Ainda dá tempo: já vai almoçar?");
  M.lunchAsk(T(12, 16)); M.lunchLater(T(12, 17)); t("só repete 1 vez", D(12, 40), null);
  const w0 = M.counts(T(12, 20)).pause; t("vou agora registra pausa real", [M.lunchGo(T(12, 20)).ok, M.counts(T(12, 20)).pause], [true, w0 + 1]);
  t("depois do vou agora não insiste", [D(12, 21), D(14)], [null, null]);
  t("não conta nas falas do dia", M.lunchState(T(13)).n, 2);
  clock = T(12, 30, 26); t("dia seguinte (quinta) volta a perguntar", M.due(clock, {}), { id: "lunch", n: 1, at: "12:00" });
}
{
  clock = T(9); const { M } = fresh(); M.setCfg({ water: { on: false } });
  t("sábado não pergunta (dias úteis)", M.due(T(12, 0, 28), {}), null); t("domingo também não", M.due(T(12, 0, 29), {}), null);
  M.setCfg({ lunch: { days: "all" } }); t("todos os dias: sábado pergunta", M.due(T(12, 0, 28), {}).id, "lunch");
  M.setCfg({ lunch: { on: false } }); t("desligado não pergunta", M.due(T(12, 0, 28), {}), null);
  M.setCfg({ lunch: { on: true, at: "13:15" } }); t("horário ajustado", [M.due(T(13, 14), {}), M.due(T(13, 15), {}).id], [null, "lunch"]);
  M.setCfg({ lunch: { at: "xx" } }); t("horário inválido volta a 12:00", M.cfg().lunch.at, "12:00");
}
{
  clock = T(9); const { M } = fresh(); M.setCfg({ water: { on: false } });
  t("foco/Pomodoro rodando: nada", M.due(T(12), { focus: true }), null);
  t("passou o foco: ainda pergunta (até 90 min depois)", [M.due(T(13, 0), {}).id, M.due(T(13, 31), {})], ["lunch", null]);
  M.silence(true, T(9)); t("silenciar hoje: nada", M.due(T(12), {}), null); M.silence(false, T(9));
  M.setCfg({ mode: "hide" }); t("Lume não ligado: nada", M.due(T(12), {}), null); M.setCfg({ mode: "on" });
  M.setCfg({ active: ["13:00", "20:00"] }); t("fora do horário ativo: nada", M.due(T(12), {}), null); M.setCfg({ active: ["08:00", "22:00"] });
  M.sleep(60, T(11, 30)); t("dormindo: ainda vem (o app mostra como toast)", M.due(T(12), {}).id, "lunch");
  t("Lume dormindo não bloqueia nem queima o limite de falas", M.lunchAsk(T(12)), 1);
}
{
  clock = T(9); const { M } = fresh(); M.setCfg({ water: { on: false } });
  const names = ["TOP MARKET - A4", "B - Job", "C - Job", "D - Job", "E - Job"];
  t("1 card", M.text("lunch2", { names: [names[0]] }), "Você tirou de Em execução? Ainda está: TOP MARKET - A4");
  t("até 3 nomes e +N", M.text("lunch2", { names }), "Você tirou de Em execução? Ainda estão: TOP MARKET - A4, B - Job, C - Job +2");
  t("sem card em execução = sem 2ª pergunta", [M.text("lunch2", { names: [] }), M.text("lunch2", {}), M.text("lunch2", { names: [null, ""] })], [null, null, null]);
  t("nome enorme é cortado", M.text("lunch2", { names: ["X".repeat(80)] }).length < 90, true);
  M.lunchRem(T(12, 0)); t("vou tirar vale 10 min", [M.lunchRemPending(T(12, 9)) > 0, M.lunchRemPending(T(12, 11))], [true, 0]);
  M.lunchRem(T(12, 0)); M.lunchRemClear(T(12, 2)); t("já tirei/lembrou: limpa", M.lunchRemPending(T(12, 3)), 0);
  M.lunchRem(T(12, 0)); clock = T(9, 0, 26); t("virada do dia zera o vou tirar", M.lunchRemPending(clock), 0);
  const raw = JSON.parse(fresh({ v: 1, cfg: { lunch: { on: false, at: "11:45", days: "all" } } }).mem.get("mascot") || "null");
  const r = fresh({ v: 1, cfg: { lunch: { on: false, at: "11:45", days: "all" } } }); t("lê a config guardada", r.M.cfg().lunch, { on: false, at: "11:45", days: "all" });
  t("estado antigo sem lunch carrega", fresh({ v: 1, daily: { day: "2026-11-25", count: 1 } }).M.lunchState(T(9)).n, 0);
  t("rota do almoço existe", fresh().M.cfg().route.lunch, "b");
}

console.log(fail ? `\n${fail} FALHA(S)` : "\nTodos OK"); process.exit(fail ? 1 : 0);
