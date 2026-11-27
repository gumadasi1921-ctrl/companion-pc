"""monday somente leitura (monday.py) com respostas falsas, sem rede. Rodar: py -3 tests/test_monday.py"""
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "server"))
import monday  # noqa: E402

TOKEN = "eyJsecrettoken" + "x" * 40
BOARDS = json.loads((Path(__file__).parent / "fixtures" / "monday-boards.json").read_text(encoding="utf-8"))
ME = {"me": {"id": "77", "name": "Gui"}}


def utc(y, mo, d, h=15):
    return datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp()


NOW = utc(2026, 11, 24, 15)          # terça 12:00 em Brasília


def person(*ids):
    return json.dumps({"personsAndTeams": [{"id": i, "kind": "person"} for i in ids]})


def dv(day, time=None):
    return json.dumps({"date": day, "time": time}) if day else None


def item(iid, name, status, who=("77",), day=None, time=None, group="Fazendo", state="active", url=None):
    return {"id": str(iid), "name": name, "state": state, "url": url or f"https://acme.monday.com/boards/1001/pulses/{iid}",
            "group": {"id": "g", "title": group},
            "column_values": [{"id": "status", "type": "status", "text": status, "value": None},
                              {"id": "pessoas", "type": "people", "text": "", "value": person(*who)},
                              {"id": "prazo", "type": "date", "text": day or "", "value": dv(day, time)}]}


ITEMS = [
    item(1, "Atrasado", "Em execução", day="2026-11-23"),
    item(2, "Hoje 23h30 BR", "Em execução", day="2026-11-25", time="02:30:00"),
    item(3, "Amanhã", "Em execução", day="2026-11-25", time="15:00:00"),
    item(4, "Sem prazo", "Em execução"),
    item(5, "De outra pessoa", "Em execução", who=("99",)),
    item(6, "Parado", "Parado"),
    item(7, "Já aprovado", "Aprovar"),
    item(8, "Arquivado", "Em execução", state="archived"),
    item(9, "Link ruim", "Em execução", url="https://evil.example.com/x"),
]
CFG = {"token": TOKEN, "on": True, "boards": [{"id": "1001", "name": "Projetos Acme", "source": "status", "status_col": "status",
                                                "people_col": "pessoas", "date_col": "prazo", "running": ["Em execução"], "approved": ["Aprovar"]}]}


class Fake:
    def __init__(self, items=None):
        self.calls, self.fail, self.items, self.act = [], None, items if items is not None else ITEMS, []
        self.paged = items is None

    def __call__(self, query, variables, token):
        monday.check_query(query)
        self.calls.append((query, variables, token))
        if self.fail:
            raise self.fail
        if "me {" in query:
            return ME
        if "columns" in query:
            return BOARDS
        if "activity_logs" in query:
            return {"boards": [{"activity_logs": self.act}]}
        if "next_items_page" in query:
            return {"next_items_page": {"cursor": None, "items": [item(50, "Pagina 2", "Em execução")]}}
        cur = "CUR" if self.paged else None
        return {"boards": [{"id": "1001", "items_page": {"cursor": cur, "items": json.loads(json.dumps(self.items))}}]}


def make(cfg=CFG, fake=None, t=NOW):
    d = tempfile.mkdtemp()
    box = {"t": t, "cfg": json.loads(json.dumps(cfg))}
    m = monday.Monday(Path(d), lambda: box["cfg"], fetch=fake or Fake(), clock=lambda: box["t"])
    return m, box, Path(d)


def log_ev(pid, text, day=(2026, 11, 20)):
    ts = int(datetime(*day, 15, tzinfo=timezone.utc).timestamp() * 1e7)
    return {"id": "1", "event": "update_column_value", "created_at": str(ts),
            "data": json.dumps({"pulse_id": int(pid), "column_id": "status", "value": {"label": {"text": text}}})}


class ReadOnly(unittest.TestCase):
    def test_only_queries(self):
        for bad in ("mutation { create_item(board_id:1,item_name:\"x\") { id } }", "subscription { x }", "{ me { id } }",
                    "query { me { id } } mutation { delete_item(item_id:1){id} }"):
            with self.assertRaises(ValueError):
                monday.check_query(bad)
        for q in (monday.Q_ME, monday.q_items_first(["status"]), monday.q_items_next(["status"]), monday.Q_BOARDS, monday.Q_ACT):
            monday.check_query(q)

    def test_module_has_no_write_words(self):
        src = (ROOT / "windows" / "server" / "monday.py").read_text(encoding="utf-8")
        self.assertNotIn("create_item", src)
        self.assertNotIn("change_column", src)
        self.assertNotIn("delete_item", src)

    def test_post_blocks_mutation_before_network(self):
        with self.assertRaises(ValueError):
            monday.http_post("mutation { x }", {}, TOKEN)


class Items(unittest.TestCase):
    def test_filters_and_groups(self):
        m, _, _ = make()
        self.assertTrue(m.refresh())
        st = m.state()
        names = {c["name"] for g in st["groups"].values() for c in g}
        self.assertEqual(names, {"Atrasado", "Hoje 23h30 BR", "Amanhã", "Sem prazo", "Link ruim", "Pagina 2"})
        self.assertEqual([c["name"] for c in st["groups"]["overdue"]], ["Atrasado"])
        self.assertEqual([c["name"] for c in st["groups"]["today"]], ["Hoje 23h30 BR"])
        self.assertEqual(st["groups"]["today"][0]["time"], "23:30")
        self.assertEqual([c["name"] for c in st["groups"]["tomorrow"]], ["Amanhã"])
        self.assertEqual({c["name"] for c in st["groups"]["none"]}, {"Sem prazo", "Link ruim", "Pagina 2"})
        self.assertEqual(st["state"], "ok")

    def test_people_filter_sent_and_pagination(self):
        f = Fake()
        m, _, _ = make(fake=f)
        m.refresh()
        first = next(c for c in f.calls if "items_page(limit" in c[0])
        self.assertEqual(first[1]["qp"]["rules"][0]["compare_value"], ["person-77"])
        self.assertTrue(any("next_items_page" in c[0] for c in f.calls))
        self.assertTrue(all(c[2] == TOKEN for c in f.calls))

    def test_open_only_safe_url(self):
        m, _, _ = make()
        m.refresh()
        self.assertTrue(m.item_url("1").startswith("https://acme.monday.com/"))
        self.assertIsNone(m.item_url("9"))
        self.assertFalse(m.has_item("../x"))
        self.assertFalse(any("url" in i for g in m.state()["groups"].values() for i in g))

    def test_group_source(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["boards"][0].update({"source": "group", "running": ["Fazendo"], "approved": ["Aprovar"]})
        its = [item(1, "A", "x", group="Fazendo"), item(2, "B", "x", group="Aprovar"), item(3, "C", "x", group="Outro")]
        m, _, _ = make(cfg, Fake(its))
        m.refresh()
        self.assertEqual([i["name"] for i in m.items if i["cat"] == "running"], ["A"])
        self.assertEqual([i["name"] for i in m.items if i["cat"] == "approved"], ["B"])

    def test_missing_column_is_config_error_not_data(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["boards"][0]["people_col"] = "naoexiste"
        m, _, _ = make(cfg)
        m.refresh()
        self.assertEqual(m.items, [])
        self.assertEqual(m.state()["state"], "config")


class TodoStage(unittest.TestCase):
    def cfg(self, todo):
        c = json.loads(json.dumps(CFG))
        c["boards"][0]["todo"] = todo
        return c

    def its(self):
        return ITEMS + [item(20, "Backlog tardio", "Backlog", day="2026-11-30"), item(21, "Backlog cedo", "BACKLOG", day="2026-11-26"),
                        item(22, "Backlog sem prazo", "Backlog"), item(23, "Cancelado", "Cancelado"),
                        item(24, "Backlog de outro", "Backlog", who=("99",))]

    def test_nothing_preselected_unmarked_is_dropped(self):
        m, _, _ = make(CFG, Fake(self.its()))
        m.refresh()
        self.assertFalse(any(i["cat"] == "todo" for i in m.items))
        self.assertEqual(m.state()["todo"], {"items": [], "count": 0})

    def test_todo_marked_status_only_mine_sorted_by_due(self):
        m, _, _ = make(self.cfg(["backlog"]), Fake(self.its()))
        m.refresh()
        st = m.state()
        self.assertEqual([i["name"] for i in st["todo"]["items"]], ["Backlog cedo", "Backlog tardio", "Backlog sem prazo"])
        self.assertEqual(st["todo"]["count"], 3)
        self.assertNotIn("Cancelado", {i["name"] for i in m.items})
        # A fazer não entra nos grupos por prazo nem em "feito"
        self.assertNotIn("Backlog cedo", {c["name"] for g in st["groups"].values() for c in g})
        self.assertFalse(any(v.get("name", "").startswith("Backlog") for v in m.done.values()))

    def test_todo_by_group(self):
        c = self.cfg(["Backlog"])
        c["boards"][0].update({"source": "group", "running": ["Fazendo"], "approved": ["Aprovar"]})
        its = [item(1, "A", "x", group="Fazendo"), item(2, "B", "x", group="Backlog"), item(3, "C", "x", group="Parado")]
        m, _, _ = make(c, Fake(its))
        m.refresh()
        self.assertEqual([i["name"] for i in m.items if i["cat"] == "todo"], ["B"])
        self.assertEqual([i["name"] for i in m.state()["todo"]["items"]], ["B"])

    def test_clean_board_todo_rules(self):
        b = CFG["boards"][0]
        ok = monday.clean_cfg({"on": False, "boards": [{**b, "running": [], "approved": [], "todo": ["Backlog"]}]}, {"token": TOKEN})
        self.assertEqual(ok["boards"][0]["todo"], ["Backlog"])
        self.assertEqual(monday.clean_cfg({"on": False, "boards": [b]}, {"token": TOKEN})["boards"][0]["todo"], [])   # config antiga
        for bad in ({**b, "todo": ["em execução"]}, {**b, "todo": ["APROVAR"]}, {**b, "running": [], "approved": [], "todo": []}):
            with self.assertRaises(ValueError):
                monday.clean_cfg({"on": False, "boards": [bad]}, {"token": TOKEN})


class Errors(unittest.TestCase):
    def test_no_token_off(self):
        m, _, _ = make({"token": "", "on": True, "boards": CFG["boards"]})
        self.assertFalse(m.enabled())
        self.assertFalse(m.refresh())
        self.assertEqual(m.state()["state"], "off")
        self.assertEqual(m.state()["groups"]["today"], [])

    def test_bad_cred_blocks(self):
        f = Fake()
        f.fail = monday.CredError("x")
        m, _, _ = make(fake=f)
        m.refresh()
        self.assertEqual(m.state()["state"], "bad_cred")
        n = len(f.calls)
        self.assertFalse(m.due(True))
        m.refresh()
        self.assertEqual(len(f.calls), n)
        m.reset()
        self.assertFalse(m.blocked)

    def test_net_error_keeps_last_and_rate_limit_backs_off(self):
        f = Fake()
        m, box, _ = make(fake=f)
        m.refresh()
        n_items = len(m.items)
        f.fail = monday.RateError("limite")
        box["t"] += 400
        m.refresh()
        self.assertEqual(len(m.items), n_items)
        self.assertEqual(m.state()["state"], "net_error")
        box["t"] += 100
        self.assertFalse(m.due(True))        # ainda no recuo de 5 min
        box["t"] += 300
        self.assertTrue(m.due(True))

    def test_interval(self):
        m, box, _ = make()
        self.assertTrue(m.due(True))
        m.refresh()
        self.assertFalse(m.due(True))
        self.assertFalse(m.due(False))
        self.assertFalse(m.refresh_manual())     # menos de 60 s
        box["t"] += 301
        self.assertTrue(m.due(True))

    def test_graphql_errors_mapping(self):
        with self.assertRaises(monday.CredError):
            monday.parse_response({"errors": [{"message": "x", "extensions": {"code": "UserUnauthorizedException"}}]})
        with self.assertRaises(monday.RateError):
            monday.parse_response({"errors": [{"extensions": {"code": "ComplexityException"}}]})
        with self.assertRaises(monday.NetError):
            monday.parse_response({"data": None})

    def test_token_never_in_state_or_files(self):
        m, _, d = make()
        m.refresh()
        blob = json.dumps(m.state()) + json.dumps(m.status()) + "".join(p.read_text(encoding="utf-8") for p in d.iterdir())
        self.assertNotIn(TOKEN, blob)


class Done(unittest.TestCase):
    def test_first_read_is_prior_then_seen(self):
        m, box, _ = make()
        m.refresh()
        st = m.state()
        self.assertEqual(st["done"]["today"], 0)            # já em Aprovar na primeira leitura = anterior, não conta
        self.assertEqual(st["done"]["prior"], 1)
        m.fetch.items = ITEMS + [item(20, "Novo aprovado", "Aprovar")]
        box["t"] += 600
        m.refresh()
        st = m.state()
        self.assertEqual(st["done"]["today"], 1)
        self.assertEqual(st["done"]["week"], 1)
        self.assertEqual(st["done"]["month"], 1)
        self.assertEqual(st["done"]["recent"][0]["label"], "visto em 24/11")
        self.assertEqual(st["done"]["by_day"], {"2026-11-24": 1})

    def test_activity_gives_real_date(self):
        f = Fake()
        f.act = [log_ev(7, "Aprovar", (2026, 11, 20)), log_ev(7, "Parado", (2026, 11, 21)), log_ev(999, "Aprovar")]
        m, _, _ = make(fake=f)
        m.refresh()
        st = m.state()
        self.assertEqual(st["done"]["prior"], 0)
        self.assertEqual(st["done"]["recent"][0]["label"], "entrou em Aprovar em 20/11")
        self.assertEqual(st["done"]["week"], 0)         # 20/11 é da semana anterior (24/11 é terça)
        self.assertEqual(st["done"]["month"], 1)

    def test_activity_garbage_is_ignored(self):
        f = Fake()
        f.act = [{"id": "1", "event": "update_column_value", "created_at": "abc", "data": "{{"}, {"event": "x"}]
        m, _, _ = make(fake=f)
        m.refresh()
        self.assertEqual(m.state()["done"]["recent"], [])
        self.assertEqual(m.state()["done"]["prior"], 1)

    def test_history_survives_reset_and_reload(self):
        m, box, d = make()
        m.refresh()
        m.fetch.items = ITEMS + [item(20, "Novo", "Aprovar")]
        box["t"] += 600
        m.refresh()
        m.reset()
        m2 = monday.Monday(d, lambda: box["cfg"], fetch=Fake(), clock=lambda: box["t"])
        self.assertEqual(m2.done_counts()["today"], 1)

    def test_back_and_forth_keeps_first_day(self):
        m, box, _ = make()
        m.refresh()
        m.fetch.items = ITEMS + [item(20, "Novo", "Aprovar")]
        box["t"] += 600
        m.refresh()
        m.fetch.items = ITEMS
        box["t"] += 86400
        m.refresh()
        m.fetch.items = ITEMS + [item(20, "Novo", "Aprovar")]
        box["t"] += 600
        m.refresh()
        self.assertEqual(m.done["20"]["day"], "2026-11-24")


class Config(unittest.TestCase):
    def test_clean_cfg_keeps_token_and_validates(self):
        out = monday.clean_cfg({"on": True, "boards": CFG["boards"]}, {"token": TOKEN})
        self.assertEqual(out["token"], TOKEN)
        b = CFG["boards"][0]
        for bad in ({"on": True, "boards": []}, {"on": True, "token": "curto", "boards": CFG["boards"]}, {"on": "sim"},
                    {"on": False, "boards": [{**b, "approved": ["Em execução"]}]},
                    {"on": False, "boards": [{**b, "id": "x"}]},
                    {"on": False, "boards": [{**b, "people_col": ""}]},
                    {"on": False, "boards": [{**b, "extra": 1}]},
                    {"on": False, "boards": [b, b]},
                    {"on": False, "boards": [{**b, "id": str(i)} for i in range(11)]}):
            with self.assertRaises(ValueError):
                monday.clean_cfg(bad, {"token": TOKEN})

    def test_boards_info_and_test(self):
        m, _, _ = make()
        info = m.boards_info()
        cols = {c["id"]: c for c in info[0]["columns"]}
        self.assertEqual(set(cols), {"status", "pessoas", "prazo"})
        self.assertIn("Aprovar", cols["status"]["labels"])
        self.assertEqual(m.test(TOKEN), "Gui")

    def test_digest_once_per_day(self):
        m, box, _ = make()
        m.refresh()
        d = m.digest()
        self.assertIn("1 atrasado", d["title"])
        self.assertIsNone(m.digest())


if __name__ == "__main__":
    unittest.main()
