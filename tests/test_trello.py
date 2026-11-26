"""Trello somente leitura (trello.py) com fixtures, sem rede. Rodar: py -3 tests/test_trello.py"""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import trello  # noqa: E402

KEY, TOKEN = "k" * 32, "ATTAsecretsecretsecret" + "t" * 40


def utc(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


NOW = utc(2026, 11, 24, 15)       # terça 24/11/2026 12:00 em Brasília


def due(y, mo, d, h=15, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def card(cid, name, board, lst, due_=None, short=None, labels=None):
    return {"id": cid, "name": name, "due": due_, "idBoard": board, "idList": lst, "shortLink": short or (cid + "AAAAAAAA")[:8],
            "labels": labels or [], "dateLastActivity": "2026-11-20T10:00:00.000Z"}


# b1 = TIME | NOVEMBRO (CONCEPT; a 1ª lista é a do dia, que muda de nome), b2 = Pessoal (sem área), b4 = TIME | OUTRA (outra área);
# b3 = TIME | DEZEMBRO só aparece depois. A ordem do dicionário é a ordem (pos) das listas no quadro.
BOARDS = {
    "b1": {"name": "TIME | NOVEMBRO", "ws": "CONCEPT", "lists": {"l0": "Terça 24/11", "l1": "Backlog", "l2": "Em execução", "l3": "Aprovar"}},
    "b2": {"name": "Pessoal", "ws": "", "lists": {"l4": "Caixa de entrada", "l5": "Em execução"}},
    "b4": {"name": "TIME | OUTRA", "ws": "OUTRA", "lists": {"l9": "Em execução", "l10": "Aprovar"}},
}
DEC = {"name": "TIME | DEZEMBRO", "ws": "CONCEPT", "lists": {"l6d": "Quarta 02/12", "l6": " em  EXECUÇÃO ", "l7": "APROVAR", "l8": "Backlog"}}


def base_cards():
    return [
        card("c1", "Atrasado", "b1", "l2", due(2026, 11, 23), labels=[{"name": "Urgente", "color": "red"}, {"name": "", "color": None}]),
        card("c2", "Hoje cedo", "b1", "l2", due(2026, 11, 24, 12)),
        card("c3", "Hoje 23h30 em Brasília", "b2", "l5", due(2026, 11, 25, 2, 30)),
        card("c4", "Amanhã", "b2", "l5", due(2026, 11, 25, 15)),
        card("c5", "Sexta", "b1", "l2", due(2026, 11, 27)),
        card("c6", "Domingo", "b1", "l2", due(2026, 11, 29)),
        card("c7", "Semana que vem", "b1", "l2", due(2026, 11, 30)),
        card("c8", "Sem prazo", "b2", "l5"),
        card("g1", "No backlog (não aparece)", "b1", "l1", due(2026, 11, 24)),
        card("g2", "Caixa de entrada (não aparece)", "b2", "l4"),
        card("a1", "Já estava em Aprovar", "b1", "l3"),
        card("o1", "Outra área", "b4", "l9", due(2026, 11, 24)),
        card("t1", "Fazer hoje", "b1", "l0", due(2026, 11, 24, 18)),
        card("t2", "Fazer hoje atrasado", "b1", "l0", due(2026, 11, 22)),
        card("cx", "shortLink inválido", "b1", "l2", short="../etc"),
    ]


def action(cid, lst_after, when, lst_before="Em execução"):
    return {"id": "x" + cid, "type": "updateCard", "date": when, "data": {"card": {"id": cid, "name": "n"}, "listBefore": {"name": lst_before}, "listAfter": {"name": lst_after}}}


class Fake:
    """fetch() falso que registra as chamadas."""
    def __init__(self):
        self.calls, self.fail, self.cards, self.boards, self.actions, self.embed = [], None, base_cards(), json.loads(json.dumps(BOARDS)), {}, True
        self.others = []

    def lists(self, bid):
        return [{"id": k, "name": v, "pos": (i + 1) * 1000} for i, (k, v) in enumerate(self.boards[bid]["lists"].items())]

    def __call__(self, path, params, key, token):
        self.calls.append(path)
        if self.fail:
            raise self.fail
        if path == "members/me/cards":
            return json.loads(json.dumps(self.cards))
        if path == "members/me/boards":
            out = []
            for bid, b in self.boards.items():
                o = {"id": bid, "name": b["name"]}
                if b["ws"]:
                    o["organization"] = {"displayName": b["ws"]}
                if self.embed:
                    o["lists"] = self.lists(bid)
                out.append(o)
            return out
        if path.startswith("lists/") and path.endswith("/cards"):     # todos os cards abertos da lista (os meus + os dos outros/sem responsável)
            lid = path.split("/")[1]
            mine = [dict(c, idMembers=["me"]) for c in self.cards if c["idList"] == lid]
            return json.loads(json.dumps(mine + [c for c in self.others if c["idList"] == lid]))
        if path.startswith("boards/") and path.endswith("/lists"):
            return self.lists(path.split("/")[1])
        if path.startswith("boards/") and path.endswith("/actions"):
            return self.actions.get(path.split("/")[1], [])
        if path == "members/me":
            return {"fullName": "Maria Teste", "username": "maria"}
        raise AssertionError("chamada inesperada " + path)


class TrelloTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.cfg = {"key": KEY, "token": TOKEN, "on": True}
        self.now = [NOW]
        self.fake = Fake()
        self.t = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])

    def tearDown(self):
        self._d.cleanup()

    def names(self, g):
        return [c["name"] for c in self.t.state()["groups"][g]]

    def all_names(self):
        st = self.t.state()
        return [c["name"] for g in st["groups"].values() for c in g] + [c["name"] for c in st["todo"]["items"]]

    def test_only_running_lists_become_tasks(self):
        self.cfg["todo"] = {"mode": "off"}
        self.assertTrue(self.t.refresh())
        st = self.t.state()
        g = {k: [c["name"] for c in v] for k, v in st["groups"].items()}
        self.assertEqual(g["overdue"], ["Atrasado"])
        self.assertEqual(g["today"], ["Hoje cedo", "Outra área", "Hoje 23h30 em Brasília"])    # 02:30Z de 25/11 = 23:30 de 24/11 em Brasília
        self.assertEqual(g["tomorrow"], ["Amanhã"])
        self.assertEqual(g["week"], ["Sexta", "Domingo"])
        self.assertEqual(g["later"], ["Semana que vem"])
        self.assertEqual(g["none"], ["Sem prazo"])
        names = self.all_names()
        for hidden in ("No backlog (não aparece)", "Caixa de entrada (não aparece)", "Já estava em Aprovar", "Fazer hoje"):
            self.assertNotIn(hidden, names)                                   # Aprovar não vira tarefa; A fazer desligado não aparece
        self.assertEqual(st["ignored"], 4)                                    # backlog, caixa de entrada e os 2 da lista do dia (A fazer desligado)
        self.assertEqual((st["found"]["boards"], st["found"]["running"], st["found"]["approved"], st["found"]["todo"]), (3, 3, 2, "off"))
        self.assertEqual(st["todo"], {"state": "off", "items": [], "count": 0, "day": None})
        c = st["groups"]["overdue"][0]
        self.assertEqual((c["board"], c["list"], c["short"], c["due"]), ("TIME | NOVEMBRO", "Em execução", "c1AAAAAA", "2026-11-23"))
        self.assertEqual(c["labels"][0], {"n": "Urgente", "c": "red"})
        self.assertEqual(st["groups"]["today"][2]["time"], "23:30")
        self.assertEqual(st["groups"]["none"][0]["due"], None)                   # nunca inventa prazo
        self.assertFalse(any(c["short"] == "../etc" for c in self.t.all_cards()))
        self.assertEqual(sorted(set(self.fake.calls)), ["boards/b1/actions", "members/me/boards", "members/me/cards"])

    def test_due_complete_no_longer_hides_a_running_card(self):
        self.fake.cards.append({**card("c9", "Prazo marcado como feito", "b1", "l2", due(2026, 11, 20)), "dueComplete": True})
        self.t.refresh()
        self.assertIn("Prazo marcado como feito", self.names("overdue"))     # feito = Aprovar, não a caixinha

    # ---- quadros: filtro, prefixo + área, quadro mensal novo
    def test_scope_prefix_and_workspace_monthly_board(self):
        self.cfg["scope"] = {"mode": "prefix", "prefixes": ["TIME |"], "ids": [], "workspace": "concept"}
        self.t.refresh()
        self.assertEqual(self.names("today"), ["Hoje cedo"])                 # só o TIME | da área CONCEPT: Pessoal e a OUTRA área ficam fora
        # o mês vira: quadro novo entra sozinho, com o nome da lista fora do padrão de espaços/maiúsculas
        self.fake.boards["b3"] = json.loads(json.dumps(DEC))
        self.fake.cards += [card("d1", "Job de dezembro", "b3", "l6", due(2026, 12, 1)), card("d2", "Dez backlog", "b3", "l8")]
        self.now[0] += 400
        self.t.refresh()
        self.assertIn("Job de dezembro", self.names("later"))
        self.assertNotIn("Dez backlog", self.all_names())
        self.assertEqual(self.t.state()["found"]["boards"], 2)

    def test_pick_and_filter_that_matches_nothing(self):
        self.cfg["scope"] = {"mode": "pick", "prefixes": [], "ids": ["b2"], "workspace": ""}
        self.t.refresh()
        self.assertEqual(self.all_names(), ["Hoje 23h30 em Brasília", "Amanhã", "Sem prazo"])
        self.cfg["scope"] = {"mode": "prefix", "prefixes": ["NADA"], "ids": [], "workspace": ""}
        self.t.invalidate()
        self.t.refresh()
        st = self.t.state()
        self.assertEqual((sum(st["counts"].values()), st["found"]["boards"]), (0, 0))   # nada inventado quando o filtro não acha quadro

    def test_list_name_equality_only(self):
        self.cfg["running"], self.cfg["approved"] = ["EM  execução"], ["aprovar"]    # maiúsculas e espaços não importam
        self.t.refresh()
        self.assertEqual(self.t.state()["found"]["running"], 3)
        for nome in ("execução", "Em execucao"):                                     # "contém" não vale; acento também conta
            self.cfg["running"] = [nome]
            self.t.invalidate()
            self.t.refresh()
            self.assertEqual(sum(self.t.state()["counts"].values()), 0, nome)

    # ---- A fazer: a lista do dia, sem identificar por nome
    def test_todo_by_list_id_survives_rename_and_switches_in_one_step(self):
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.t.refresh()
        st = self.t.state()
        self.assertEqual([c["name"] for c in st["todo"]["items"]], ["Fazer hoje atrasado", "Fazer hoje"])   # por prazo
        self.assertEqual((st["todo"]["state"], st["todo"]["count"], st["todo"]["items"][0]["late"], st["todo"]["items"][1]["late"]), ("ok", 2, True, False))
        self.assertEqual(st["ignored"], 2)                                         # só backlog e caixa de entrada: a lista do dia agora é A fazer
        self.fake.boards["b1"]["lists"]["l0"] = "Quarta 25/11"                       # o dia virou e a lista mudou de nome
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["todo"]["count"], 2)                         # o id continua valendo
        self.fake.cards.append(card("t3", "Da lista de amanhã", "b1", "l1"))          # troca em 1 toque: outra lista (Backlog) vira A fazer
        self.cfg["todo"] = {"mode": "list", "list_id": "l1", "position": 1}
        self.t.invalidate()
        self.t.refresh()
        self.assertEqual([c["name"] for c in self.t.state()["todo"]["items"]], ["No backlog (não aparece)", "Da lista de amanhã"])

    def test_todo_by_position_follows_name_and_monthly_boards(self):
        self.cfg["scope"] = {"mode": "prefix", "prefixes": ["TIME |"], "ids": [], "workspace": "CONCEPT"}
        self.cfg["todo"] = {"mode": "position", "list_id": "", "position": 1}
        self.t.refresh()
        self.assertEqual({c["name"] for c in self.t.state()["todo"]["items"]}, {"Fazer hoje", "Fazer hoje atrasado"})
        self.fake.boards["b1"]["lists"] = {"lx": "Quinta 26/11", **{k: v for k, v in self.fake.boards["b1"]["lists"].items() if k != "l0"}}   # nome e id novos na 1ª posição
        self.fake.cards.append(card("t4", "Da lista nova", "b1", "lx"))
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual([c["name"] for c in self.t.state()["todo"]["items"]], ["Da lista nova"])
        # novo mês: o quadro de dezembro tem a lista do dia na 1ª posição e entra sozinho
        self.fake.boards["b3"] = json.loads(json.dumps(DEC))
        self.fake.cards.append(card("t5", "Dia 2/12", "b3", "l6d", due(2026, 12, 2)))
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual({c["name"] for c in self.t.state()["todo"]["items"]}, {"Da lista nova", "Dia 2/12"})
        self.cfg["todo"] = {"mode": "position", "list_id": "", "position": 2}
        self.t.invalidate()
        self.t.refresh()
        self.assertEqual([c["name"] for c in self.t.state()["todo"]["items"]], ["No backlog (não aparece)"])   # 2ª lista: Backlog (b1); a de b3 é "em execução" e o nome ganha
        self.assertEqual(self.t.state()["found"]["todo_clash"], 1)

    def test_todo_missing_list_is_never_guessed(self):
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.t.refresh()
        del self.fake.boards["b1"]["lists"]["l0"]                                    # lista arquivada
        self.now[0] += 400
        self.t.refresh()
        st = self.t.state()
        self.assertEqual((st["todo"]["state"], st["todo"]["count"]), ("missing", 0))
        self.cfg["todo"] = {"mode": "position", "list_id": "", "position": 9}        # nenhum quadro tem a 9ª lista
        self.t.invalidate()
        self.t.refresh()
        self.assertEqual(self.t.state()["todo"]["state"], "missing")
        self.cfg["scope"] = {"mode": "pick", "prefixes": [], "ids": ["b2"], "workspace": ""}
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}          # a lista existe, mas o quadro saiu do filtro
        self.t.invalidate()
        self.t.refresh()
        self.assertEqual(self.t.state()["todo"]["state"], "missing")

    # ---- A fazer = "Lista de hoje pela data" (padrão): nome começa com "DIA <dia>" no fuso do Brasil
    def day_boards(self, names, bid="b1"):
        self.fake.boards[bid]["lists"] = {"x%d" % i: n for i, n in enumerate(names)} | {"l2": "Em execução", "l3": "Aprovar"}
        self.fake.cards = [card("t%d" % i, "Card " + n, bid, "x%d" % i) for i, n in enumerate(names)]

    def todo_names(self):
        return sorted(c["name"] for c in self.t.state()["todo"]["items"])

    def test_today_rule_is_exact_and_never_confuses_2_with_26(self):
        for day, ok in ((2, ["DIA 2 - SEGUNDA"]), (6, ["DIA 06 - SEXTA", "dia  6"]), (26, ["DIA 26 - QUINTA-FEIRA", "DIA 026"])):
            rx = trello.today_list_rx(day)
            for n in ["DIA 2 - SEGUNDA", "DIA 26 - QUINTA-FEIRA", "DIA 06 - SEXTA", "dia  6", "DIA 026", "DIA 6-X", "DIA 20", "DIA 12", "DIA", "Dia 2x", "DIA 2.", "DIAS 2", "Backlog 26", "Terça 26/11"]:
                self.assertEqual(bool(rx.match(trello.norm(n))), n in ok or (n == "DIA 6-X" and day == 6), (day, n))
        self.assertTrue(trello.today_list_rx(26).match(trello.norm(" DIA   26 ")))      # espaços colapsados; só o fim também vale
        self.assertTrue(trello.today_list_rx(2).match(trello.norm("DIA 02")))

    def test_today_mode_is_default_and_picks_only_todays_list(self):
        self.day_boards(["DIA 2 - SEGUNDA", "DIA 06 - SEXTA", "DIA 26 - QUINTA-FEIRA", "DIA 27 - SEXTA", "Backlog"])
        self.now[0] = utc(2026, 11, 26, 15)                                            # quinta 26/11 12:00 em Brasília; cfg sem "todo" = padrão
        self.t.refresh()
        st = self.t.state()
        self.assertEqual((self.todo_names(), st["todo"]["state"], st["todo"]["day"]), (["Card DIA 26 - QUINTA-FEIRA"], "ok", 26))
        self.assertEqual(self.t.cfg_view()["lists"]["todo"], [{"list": "DIA 26 - QUINTA-FEIRA", "board": "TIME | NOVEMBRO"}])
        self.assertEqual(self.t.cfg_view()["counts"]["todo"], 1)
        for d, want in ((2, "DIA 2 - SEGUNDA"), (6, "DIA 06 - SEXTA")):                # sem zero no nome / com zero: dia 2 não pega o 26
            self.now[0] = utc(2026, 12, d, 15)
            self.t.invalidate()
            self.t.refresh()
            self.assertEqual(self.todo_names(), ["Card " + want], d)

    def test_today_mode_without_list_never_takes_another_day(self):
        self.day_boards(["DIA 25 - QUARTA-FEIRA", "DIA 27 - SEXTA"])
        self.now[0] = utc(2026, 11, 26, 15)
        self.t.refresh()
        st = self.t.state()
        self.assertEqual((st["todo"]["state"], st["todo"]["count"], st["todo"]["day"], st["found"]["todo"]), ("missing", 0, 26, "missing"))
        self.assertEqual(self.t.cfg_view()["lists"]["todo"], [])

    def test_today_mode_rolls_over_at_midnight_brazil_without_waiting_5_min(self):
        self.day_boards(["DIA 26 - QUINTA-FEIRA", "DIA 27 - SEXTA"])
        self.now[0] = utc(2026, 11, 27, 1, 50)                                         # 22:50 de 26/11 em Brasília
        self.t.refresh()
        self.assertEqual(self.todo_names(), ["Card DIA 26 - QUINTA-FEIRA"])
        self.now[0] += 120                                                             # 22:52: nada mudou, a próxima leitura é só em 5 min
        self.assertFalse(self.t.due(True))
        self.now[0] = utc(2026, 11, 27, 3, 0, ) + 5                                    # 00:00:05 de 27/11 em Brasília (03:00 UTC)
        self.assertTrue(self.t.due(True))                                              # virou o dia: relê antes dos 5 min
        self.t.refresh()
        self.assertEqual((self.todo_names(), self.t.state()["todo"]["day"]), (["Card DIA 27 - SEXTA"], 27))
        self.now[0] += 10
        self.assertFalse(self.t.due(True))                                             # e não fica relendo em laço

    def test_today_mode_uses_brazil_day_not_utc(self):
        self.day_boards(["DIA 26 - QUINTA-FEIRA", "DIA 27 - SEXTA"])
        self.now[0] = utc(2026, 11, 27, 2, 30)                                         # 27/11 02:30 UTC = 26/11 23:30 em Brasília
        self.t.refresh()
        self.assertEqual(self.todo_names(), ["Card DIA 26 - QUINTA-FEIRA"])

    def test_today_mode_old_modes_still_work_and_clean_cfg(self):
        out = trello.clean_cfg({"on": True, "todo": {"mode": "today"}}, {"key": KEY, "token": TOKEN, "on": True})
        self.assertEqual(out["todo"], {"mode": "today", "list_id": "", "position": 1, "scope": "all", "job_only": True})
        self.day_boards(["DIA 26 - QUINTA-FEIRA", "Outra"])
        self.now[0] = utc(2026, 11, 26, 15)
        self.cfg["todo"] = {"mode": "list", "list_id": "x1", "position": 1}
        self.t.refresh()
        self.assertEqual(self.todo_names(), ["Card Outra"])
        self.cfg["todo"] = {"mode": "position", "list_id": "", "position": 1}
        self.t.invalidate()
        self.t.refresh()
        self.assertEqual(self.todo_names(), ["Card DIA 26 - QUINTA-FEIRA"])

    def test_todo_name_match_wins_over_position_and_is_reported(self):
        self.cfg["todo"] = {"mode": "list", "list_id": "l2", "position": 1}         # "Em execução" escolhida como A fazer: o nome ganha
        self.t.refresh()
        st = self.t.state()
        self.assertEqual((st["todo"]["count"], st["found"]["todo_clash"]), (0, 1))
        self.assertIn("Atrasado", self.names("overdue"))

    # ---- feito = card em Aprovar
    def test_done_prior_with_real_action_date_counts(self):
        self.fake.actions["b1"] = [action("a1", "Aprovar", "2026-11-20T15:00:00.000Z"), {"type": "updateCard", "date": "ruim", "data": {}}]
        self.t.refresh()
        d = self.t.state()["done"]
        self.assertEqual((d["month"], d["week"], d["today"], d["prior"]), (1, 0, 0, 0))   # já estava em Aprovar, mas a action traz a data real
        self.assertEqual(d["recent"][0]["label"], "entrou em Aprovar em 20/11")
        self.assertEqual(self.t.done["a1"]["src"], "entered")

    def test_done_prior_without_action_does_not_count_and_new_arrival_is_seen(self):
        self.t.refresh()
        d = self.t.state()["done"]
        self.assertEqual((d["month"], d["prior"], d["recent"]), (0, 1, []))        # a1 já estava em Aprovar: "anterior ao Companion"
        self.fake.cards.append(card("m1", "Chegou agora", "b1", "l3"))
        self.now[0] += 400
        self.t.refresh()                                                          # sem action da API: "visto em"
        d = self.t.state()["done"]
        self.assertEqual((d["today"], d["week"], d["month"], d["prior"]), (1, 1, 1, 1))
        self.assertEqual(d["recent"][0]["label"], "visto em 24/11")
        self.assertEqual(d["by_day"], {"2026-11-24": 1})
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["month"], 1)                      # não conta de novo

    def test_done_gets_real_date_from_action(self):
        self.t.refresh()
        self.fake.cards.append(card("m2", "Movido no domingo", "b1", "l3"))
        self.fake.actions["b1"] = [action("m2", "Em execução", "2026-11-22T10:00:00.000Z", "Backlog"), action("m2", "Aprovar", "2026-11-23T01:30:00.000Z", "Em execução")]
        self.now[0] += 400
        self.t.refresh()
        row = next(r for r in self.t.state()["done"]["recent"] if r["id"] == "m2")
        self.assertEqual(row["label"], "entrou em Aprovar em 22/11")             # 01:30Z de 23/11 = 22:30 de 22/11 em Brasília
        d = self.t.state()["done"]
        self.assertEqual((d["today"], d["week"], d["month"]), (0, 0, 1))          # 22/11 foi domingo: semana passada (esta começa em 23/11)

    def other(self, cid, name, lst="l0", members=()):
        c = card(cid, name, "b1", lst, None)
        c["idMembers"] = list(members)
        return c

    def test_a_fazer_all_cards_marks_unassigned_hides_separators_and_never_archived(self):
        self.fake.others = [self.other("u1", "TOP MARKET - A4"), self.other("u2", "DE MANHÃ:"), self.other("u3", "----"), self.other("u4", "EL VIEJO - TV VERTICAL"),
                            self.other("u5", "CDAVILA - POST", members=["ana"])]   # arquivados nem vêm da API (filter=open)
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}        # scope/job_only: padrão = todos + só CLIENTE - JOB
        self.t.refresh()
        st = self.t.state()["todo"]
        names = {c["name"]: c.get("nobody", False) for c in st["items"]}
        self.assertEqual(names, {"Fazer hoje": False, "Fazer hoje atrasado": False, "TOP MARKET - A4": True, "EL VIEJO - TV VERTICAL": True, "CDAVILA - POST": False})
        self.assertEqual((st["count"], self.t.found["todo_hidden"]), (5, 2))
        self.assertIn("lists/l0/cards", self.fake.calls)

    def test_a_fazer_job_only_off_shows_separators_and_mine_scope_is_the_old_behavior(self):
        self.fake.others = [self.other("u2", "DE MANHÃ:"), self.other("u1", "X - Y")]
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1, "scope": "all", "job_only": False}
        self.t.refresh()
        self.assertEqual(sorted(c["name"] for c in self.t.state()["todo"]["items"]), ["DE MANHÃ:", "Fazer hoje", "Fazer hoje atrasado", "X - Y"])
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1, "scope": "mine", "job_only": True}
        self.fake.calls.clear()
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(sorted(c["name"] for c in self.t.state()["todo"]["items"]), ["Fazer hoje", "Fazer hoje atrasado"])
        self.assertNotIn("lists/l0/cards", self.fake.calls)

    def test_a_fazer_my_card_without_dash_is_kept_and_running_stays_mine_only(self):
        self.fake.cards.append(card("k1", "Kappa", "b1", "l0"))
        self.fake.others = [self.other("u9", "OUTRO - EM EXECUCAO", lst="l2")]      # card de outro na lista Em execução: nunca entra
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.t.refresh()
        self.assertIn("Kappa", [c["name"] for c in self.t.state()["todo"]["items"]])
        self.assertNotIn("OUTRO - EM EXECUCAO", self.all_names())
        self.assertNotIn("lists/l2/cards", self.fake.calls)

    def test_split_name_rule(self):
        self.assertEqual(trello.split_name("TOP MARKET - POST 20 - EP 1"), ("TOP MARKET", "POST 20 - EP 1"))
        for bad in ("Kappa", "- x", "x - ", "A-B", "DE MANHÃ:", ""):
            self.assertEqual(trello.split_name(bad)[0], None)

    def test_a_fazer_all_failure_keeps_last_reading(self):
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.t.refresh()
        orig = self.fake.__call__

        def boom(path, params, key, token):
            if path.startswith("lists/"):
                raise trello.NetError("rede")
            return orig(path, params, key, token)
        self.t.fetch = boom
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.status()["state"], "net_error")
        self.assertEqual(self.t.state()["todo"]["count"], 2)

    def test_today_ids_only_cards_counted_today_never_prior_or_reopened(self):
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["today_ids"], [])                  # a1 já estava em Aprovar: anterior, nunca vai ao Lume
        self.fake.cards.append(card("n1", "Novo", "b1", "l3"))
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["today_ids"], ["n1"])
        self.fake.cards = [c for c in self.fake.cards if c["id"] != "n1"] + [card("n1", "Novo", "b1", "l2")]
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["today_ids"], [])                  # reaberto não conta
        self.now[0] += 86400
        self.assertEqual(self.t.state()["done"]["today_ids"], [])

    def test_done_is_permanent_when_card_leaves_aprovar_to_another_list(self):
        self.t.refresh()
        self.fake.cards.append(card("m5", "Finalizado", "b1", "l3"))
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["month"], 1)
        self.fake.cards = [c for c in self.fake.cards if c["id"] != "m5"]       # saiu de Aprovar para uma lista fora do processo (não vem mais)
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual((self.t.state()["done"]["month"], self.t.state()["done"]["today"]), (1, 1))
        self.fake.cards.append(card("m5", "Finalizado", "b1", "l1"))            # em outra lista conhecida (Backlog): ainda conta
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["month"], 1)

    def test_reopened_card_stops_counting_and_new_entry_counts_on_new_date_once(self):
        self.t.refresh()
        self.fake.cards.append(card("m6", "Vai e volta", "b1", "l3"))
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["today"], 1)
        self.fake.cards = [c for c in self.fake.cards if c["id"] != "m6"] + [card("m6", "Vai e volta", "b1", "l2")]   # voltou para Em execução
        self.now[0] += 400
        self.t.refresh()
        d = self.t.state()["done"]
        self.assertEqual((d["today"], d["month"], d["recent"]), (0, 0, []))
        self.assertTrue(self.t.done["m6"]["open"])
        self.fake.cards = [c for c in self.fake.cards if c["id"] != "m6"] + [card("m6", "Vai e volta", "b1", "l3")]   # entrou de novo no mesmo dia
        self.now[0] += 400
        self.t.refresh()
        d = self.t.state()["done"]
        self.assertEqual((d["today"], d["month"]), (1, 1))                       # conta de novo, sem duplicar
        self.assertFalse(self.t.done["m6"]["open"])
        self.fake.cards = [c for c in self.fake.cards if c["id"] != "m6"] + [card("m6", "Vai e volta", "b1", "l0")]   # reaberto para A fazer
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.state()["done"]["month"], 0)
        self.fake.cards = [c for c in self.fake.cards if c["id"] != "m6"] + [card("m6", "Vai e volta", "b1", "l3")]
        self.now[0] += 86400
        self.t.refresh()                                                         # outro dia: nova data
        self.assertEqual(self.t.done["m6"]["day"], "2026-11-25")
        self.assertEqual(self.t.state()["done"]["month"], 1)

    def test_reopened_flag_survives_restart(self):
        self.t.refresh()
        self.fake.cards.append(card("m7", "x", "b1", "l3"))
        self.now[0] += 400
        self.t.refresh()
        self.fake.cards = [c for c in self.fake.cards if c["id"] != "m7"] + [card("m7", "x", "b1", "l2")]
        self.now[0] += 400
        self.t.refresh()
        again = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])
        self.assertEqual(again.state()["done"]["month"], 0)

    def test_new_monthly_board_first_read_is_prior(self):
        self.cfg["scope"] = {"mode": "prefix", "prefixes": ["TIME |"], "ids": [], "workspace": "CONCEPT"}
        self.t.refresh()
        self.fake.boards["b3"] = json.loads(json.dumps(DEC))
        self.fake.cards.append(card("d3", "Em Aprovar no quadro novo", "b3", "l7"))
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.done["d3"]["src"], "prior")                       # quadro novo na 1ª leitura: anterior, não conta
        self.fake.cards.append(card("d4", "Chegou depois", "b3", "l7"))
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.done["d4"]["src"], "seen")

    def test_action_failure_falls_back_to_seen(self):
        self.t.refresh()
        orig = self.fake.__call__
        self.fake.cards.append(card("m3", "Sem data", "b1", "l3"))

        def boom(path, params, key, token):
            if path.endswith("/actions"):
                raise trello.NetError("rede")
            return orig(path, params, key, token)
        self.t.fetch = boom
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.t.done["m3"]["src"], "seen")
        self.assertEqual(self.t.status()["state"], "ok")

    def test_history_survives_restart_reset_and_changed_credentials(self):
        self.t.refresh()
        self.fake.cards.append(card("m4", "Guardado", "b1", "l3"))
        self.now[0] += 400
        self.t.refresh()
        self.assertTrue((self.base / "trello-done.json").exists())
        again = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])
        self.assertEqual(again.done["m4"]["src"], "seen")
        again.reset()
        self.assertEqual(again.state()["done"]["month"], 1)
        self.assertEqual(again.cards, [])                                          # os cards da conta anterior somem; o histórico fica

    def test_old_trello_json_v1_is_ignored(self):
        (self.base / "trello.json").write_text(json.dumps({"v": 1, "cards": [{"short": "AAAAAAA1", "name": "velho"}], "at": 5, "notified": "2026-11-23"}), encoding="utf-8")
        t = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])
        self.assertEqual((t.cards, t.at, t.notified), ([], 0.0, "2026-11-23"))

    def test_lists_embedded_with_fallback_per_board(self):
        self.fake.embed = False
        self.t.refresh()
        self.assertIn("boards/b1/lists", self.fake.calls)
        self.assertEqual(self.names("overdue"), ["Atrasado"])

    def test_boards_and_lists_are_read_every_cycle(self):
        self.t.refresh()
        self.fake.calls.clear()
        self.now[0] += 400
        self.t.refresh()
        self.assertIn("members/me/boards", self.fake.calls)                        # a lista do dia muda de nome e de lugar

    def test_midnight_in_brasilia(self):
        self.t.refresh()
        self.now[0] = utc(2026, 11, 25, 2, 59)    # 23:59 de 24/11 em Brasília: ainda "hoje"
        self.assertEqual(self.t.state()["today"], "2026-11-24")
        self.now[0] = utc(2026, 11, 25, 3, 1)     # 00:01 de 25/11
        st = self.t.state()
        self.assertEqual(st["today"], "2026-11-25")
        self.assertEqual([c["name"] for c in st["groups"]["overdue"]], ["Atrasado", "Hoje cedo", "Outra área", "Hoje 23h30 em Brasília"])

    def test_401_stops_trying_until_credentials_change(self):
        self.t.refresh()
        self.fake.fail = trello.CredError("http 401")
        self.now[0] += 400
        self.assertTrue(self.t.due(True))
        self.t.refresh()
        self.assertEqual(self.t.status()["state"], "bad_cred")
        n = len(self.fake.calls)
        self.now[0] += 4000
        self.assertFalse(self.t.due(True))
        self.assertFalse(self.t.refresh_manual())
        self.assertEqual(len(self.fake.calls), n)                    # parou de tentar
        self.assertEqual(len(self.t.cards), 10)                      # a última leitura continua (9 em execução + 1 em Aprovar)
        again = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])
        self.assertTrue(again.blocked)                               # sobrevive a reiniciar o servidor
        self.fake.fail = None
        self.t.reset()
        self.assertTrue(self.t.due(True))
        self.t.refresh()
        self.assertEqual(self.t.status()["state"], "ok")

    def test_network_error_keeps_last_reading(self):
        self.t.refresh()
        at = self.t.at
        self.fake.fail = trello.NetError("rede")
        self.now[0] += 400
        self.t.refresh()
        st = self.t.status()
        self.assertEqual(st["state"], "net_error")
        self.assertEqual(self.t.at, at)
        self.assertEqual(len(self.t.state()["groups"]["today"]), 3)
        self.assertEqual(self.t.state()["hhmm"], "12:00")            # "atualizado às 12:00"
        self.assertFalse(self.t.due(True))
        self.now[0] += 61
        self.assertTrue(self.t.due(True))                            # depois de erro de rede tenta de novo em 1 min
        self.fake.fail = None
        self.t.refresh()
        self.assertEqual(self.t.status()["state"], "ok")

    def test_throttling(self):
        self.assertTrue(self.t.due(True))
        self.assertFalse(self.t.due(False))                          # sem aparelho: parado
        self.t.refresh()
        self.assertFalse(self.t.due(True))
        self.now[0] += 299
        self.assertFalse(self.t.due(True))
        self.now[0] += 2
        self.assertTrue(self.t.due(True))
        n = len(self.fake.calls)
        self.now[0] += 61
        self.assertTrue(self.t.refresh_manual())
        self.assertFalse(self.t.refresh_manual())                    # 2ª seguida: no máximo 1x por minuto
        self.assertGreater(len(self.fake.calls), n)
        self.cfg["on"] = False
        self.assertFalse(self.t.due(True))

    def test_state_and_status_never_carry_credentials(self):
        self.t.refresh()
        blob = json.dumps([self.t.state(), self.t.status(), self.t.all_cards(), self.t.cfg_view()]) + (self.base / "trello.json").read_text(encoding="utf-8") \
            + (self.base / "trello-done.json").read_text(encoding="utf-8")
        self.assertNotIn(KEY, blob)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("secretsecret", blob)

    def test_status_keys_for_the_panel(self):
        self.assertEqual(sorted(self.t.status()), ["at", "has_key", "has_secret", "on", "state", "user"])   # o painel lê has_key/has_secret
        s = self.t.status()
        self.assertEqual((s["on"], s["has_key"], s["has_secret"], s["state"]), (True, True, True, "waiting"))
        self.assertNotIn("has_token", s)

    def test_has_card_only_cached_valid_short_links(self):
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.t.refresh()
        self.assertTrue(self.t.has_card("c4AAAAAA"))
        self.assertTrue(self.t.has_card("a1AAAAAA"))                 # Aprovar também pode ser ligado a um job
        self.assertTrue(self.t.has_card("t1AAAAAA"))                 # A fazer também
        self.assertFalse(self.t.has_card("g1AAAAAA"))                # lista fora da configuração: não
        for bad in ("ZZZZZZZZ", "../../x", "AAAAAAA", "AAAAAAA44", None, 5, "http://x"):
            self.assertFalse(self.t.has_card(bad))
        self.assertEqual({c["cat"] for c in self.t.all_cards()}, {"running", "approved", "todo"})

    def test_digest_counts_todo_and_running_once_per_day(self):
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.t.refresh()
        self.now[0] = utc(2026, 11, 24, 11, 59)                      # 08:59 em Brasília
        self.assertIsNone(self.t.digest())
        self.now[0] = utc(2026, 11, 24, 12, 1)                       # 09:01
        d = self.t.digest()
        self.assertEqual((d["todo"], d["running"]), (2, 9))
        self.assertEqual(d["title"], "Trello: 2 a fazer · 9 em execução")
        self.assertEqual(d["body"], "Atrasado")
        self.assertIsNone(self.t.digest())                           # 1x por dia
        again = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])
        self.assertIsNone(again.digest())                            # nem depois de reiniciar

    def test_digest_only_the_parts_that_exist_and_nothing_when_empty(self):
        self.fake.cards = [card("a1", "Só em Aprovar", "b1", "l3")]
        self.t.refresh()
        self.now[0] = utc(2026, 11, 24, 13)
        self.assertIsNone(self.t.digest())                           # Aprovar não entra no aviso
        self.fake.cards = [card("t1", "Um", "b1", "l0"), card("c1", "Dois", "b1", "l2")]
        self.cfg["todo"] = {"mode": "list", "list_id": "l0", "position": 1}
        self.t.invalidate()
        self.t.refresh()
        self.now[0] += 86400
        self.assertEqual(self.t.digest()["title"], "Trello: 1 a fazer · 1 em execução")

    def test_test_button_returns_only_name(self):
        self.assertEqual(self.t.test(KEY, TOKEN), "Maria Teste")

    def test_boards_info_for_the_panel(self):
        self.fake.boards["b3"] = json.loads(json.dumps(DEC))
        r = self.t.boards_info(KEY, TOKEN, {"mode": "prefix", "prefixes": ["time |"], "ids": [], "workspace": "CONCEPT"})
        self.assertEqual([(b["name"], b["in"]) for b in r["boards"]], [("Pessoal", False), ("TIME | DEZEMBRO", True), ("TIME | NOVEMBRO", True), ("TIME | OUTRA", False)])
        merged = {trello.norm(n["name"]): n["boards"] for n in r["names"]}
        self.assertEqual(merged["aprovar"], 2)                                      # nomes iguais (sem maiúsculas) viram um só
        self.assertEqual(merged["em execução"], 2)
        self.assertEqual(r["workspaces"], ["CONCEPT", "OUTRA"])
        nov = next(b for b in r["boards"] if b["name"] == "TIME | NOVEMBRO")
        self.assertEqual(nov["ordered"][0], {"id": "l0", "name": "Terça 24/11", "n": 1})   # o seletor da lista do dia mostra a ordem e o id

    def test_clean_cfg(self):
        cur = {"key": KEY, "token": TOKEN, "on": True}
        out = trello.clean_cfg({"on": True}, cur)                                   # formato antigo: só liga/desliga
        self.assertEqual((out["key"], out["token"], out["scope"]["mode"], out["running"], out["approved"], out["todo"]["mode"]),
                         (KEY, TOKEN, "all", ["Em execução"], ["Aprovar"], "today"))   # sem escolha salva: lista de hoje pela data
        ok = trello.clean_cfg({"on": True, "scope": {"mode": "prefix", "prefixes": ["TIME |"], "workspace": " CONCEPT "}, "running": ["Em execução", "Fazendo"],
                               "approved": ["Aprovar"], "todo": {"mode": "position", "position": 1}}, cur)
        self.assertEqual((ok["scope"]["prefixes"], ok["scope"]["workspace"], ok["running"], ok["todo"]), (["TIME |"], "CONCEPT", ["Em execução", "Fazendo"], {"mode": "position", "list_id": "", "position": 1, "scope": "all", "job_only": True}))
        self.assertEqual(trello.clean_cfg({"on": True, "todo": {"mode": "list", "list_id": "l0"}}, cur)["todo"]["list_id"], "l0")
        for bad in ({"on": "sim"}, {"on": True, "x": 1}, {"on": True, "scope": {"mode": "prefix", "prefixes": []}}, {"on": True, "scope": {"mode": "pick", "ids": []}},
                    {"on": True, "scope": {"mode": "pick", "ids": ["../x"]}}, {"on": True, "scope": {"mode": "tudo"}}, {"on": True, "running": ["A"] * 6},
                    {"on": True, "running": ["Aprovar"], "approved": ["aprovar "]}, {"on": True, "running": [], "approved": []}, {"on": True, "running": [5]},
                    {"on": True, "todo": {"mode": "list"}}, {"on": True, "todo": {"mode": "list", "list_id": "../x"}}, {"on": True, "todo": {"mode": "position", "position": 0}},
                    {"on": True, "todo": {"mode": "position", "position": 21}}, {"on": True, "todo": {"mode": "position", "position": True}}, {"on": True, "todo": {"mode": "x"}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                trello.clean_cfg(bad, cur)
        with self.assertRaises(ValueError):
            trello.clean_cfg({"on": True}, {})                                      # ligar sem credencial


class NoWrites(unittest.TestCase):
    """Todo o acesso à rede passa por http_get: só GET, só api.trello.com, credencial só no cabeçalho."""

    def run_http(self, code=None, body=b"[]"):
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append((req.get_method(), req.full_url, dict(req.header_items()), req.data, timeout))
            if code:
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
            return mock.MagicMock(__enter__=lambda s: mock.MagicMock(read=lambda: body), __exit__=lambda *a: False)
        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            try:
                trello.http_get("members/me/cards", {"filter": "open", "fields": "name"}, KEY, TOKEN)
                err = None
            except Exception as e:  # noqa: BLE001
                err = e
        return seen, err

    def test_only_get_on_trello_host_credentials_in_header(self):
        seen, err = self.run_http()
        self.assertIsNone(err)
        m, url, headers, data, timeout = seen[0]
        self.assertEqual(m, "GET")
        self.assertTrue(url.startswith("https://api.trello.com/1/members/me/cards?"))
        self.assertIsNone(data)
        self.assertEqual(timeout, 10)
        self.assertNotIn(KEY, url)
        self.assertNotIn(TOKEN, url)
        self.assertIn(KEY, headers["Authorization"])

    def test_errors_are_fixed_texts(self):
        for code, cls in ((401, trello.CredError), (403, trello.CredError), (500, trello.NetError), (429, trello.NetError)):
            _seen, err = self.run_http(code)
            self.assertIsInstance(err, cls)
            self.assertNotIn(KEY, str(err))
            self.assertNotIn(TOKEN, str(err))
            self.assertNotIn("trello.com", str(err))
        _seen, err = self.run_http(body=b"nao json")
        self.assertIsInstance(err, trello.NetError)
        with mock.patch.object(urllib.request, "urlopen", side_effect=TimeoutError("api.trello.com?key=" + KEY)):
            with self.assertRaises(trello.NetError) as cm:
                trello.http_get("members/me", {}, KEY, TOKEN)
            self.assertNotIn(KEY, str(cm.exception))

    def test_module_source_has_no_write_methods(self):
        src = (Path(trello.__file__)).read_text(encoding="utf-8")
        for word in ('"POST"', '"PUT"', '"DELETE"', "'POST'", "'PUT'", "'DELETE'", "data=", ".post(", "method=\"P", "method=\"D"):
            self.assertNotIn(word, src, word)
        self.assertEqual(src.count("urlopen("), 1)
        self.assertEqual(src.count('method="GET"'), 1)

    def test_full_refresh_over_real_http_get_only_gets(self):
        calls = []
        fk = Fake()

        def fake_urlopen(req, timeout=None):
            p = req.full_url.split("?")[0].replace(trello.API, "")
            calls.append(req.get_method() + " " + p)
            body = fk(p, {}, "", "")
            return mock.MagicMock(__enter__=lambda s: mock.MagicMock(read=lambda: json.dumps(body).encode()), __exit__=lambda *a: False)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            t = trello.Trello(Path(d), lambda: {"key": KEY, "token": TOKEN, "on": True}, trello.http_get, lambda: NOW)
            t.refresh()
            self.assertEqual(t.status()["state"], "ok")
        self.assertTrue(calls and all(c.startswith("GET ") for c in calls), calls)


if __name__ == "__main__":
    unittest.main(verbosity=1)
