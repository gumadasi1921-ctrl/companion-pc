"""Retrospectiva do mês: recap.build (só o que existe no histórico), arquivo de partidas do Dota e contagem de feitos por intervalo.
Rodar: py -3 tests/test_recap.py"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import dota_od  # noqa: E402
import monday  # noqa: E402
import recap  # noqa: E402
import trello  # noqa: E402

YM = "2026-11"


def wm(total=36000, jobs=True, prev=None):
    cl = [{"client": "A4", "s": 20000, "prev_s": 0, "unidentified": False, "jobs": [{"job": "Logo", "s": 12000, "prev_s": 0}, {"job": "Flyer", "s": 8000, "prev_s": 0}]},
          {"client": "Não identificado", "s": 9000, "prev_s": 0, "unidentified": True, "jobs": []},
          {"client": "B2", "s": 7000, "prev_s": 0, "unidentified": False, "jobs": [{"job": "Site", "s": 7000, "prev_s": 0}]}]
    return {"month": YM, "total_s": total, "prev_total_s": prev or 0, "has_prev": bool(prev), "days_worked": 3,
            "days": [{"date": "2026-11-19", "s": 10000}, {"date": "2026-11-20", "s": 26000}, {"date": "2026-11-21", "s": 0}], "clients": cl if jobs else []}


def ids(r):
    return [s["id"] for s in r["slides"]]


class Build(unittest.TestCase):
    def test_mes_sem_dado_nao_tem_tela(self):
        r = recap.build(YM)
        self.assertEqual((r["slides"], r["empty"]), ([], True))

    def test_trabalho(self):
        r = recap.build(YM, wm(prev=18000))
        self.assertEqual(ids(r), ["open", "clients", "jobs"])
        o = r["slides"][0]
        self.assertEqual((o["total_s"], o["prev_total_s"], o["best_day"]), (36000, 18000, {"date": "2026-11-20", "s": 26000}))
        self.assertEqual([c["name"] for c in r["slides"][1]["items"]], ["A4", "B2"])   # "Não identificado" fora
        self.assertEqual(r["slides"][2]["items"][0], {"client": "A4", "job": "Logo", "s": 12000})

    def test_sem_mes_anterior_nao_compara(self):
        self.assertIsNone(recap.build(YM, wm())["slides"][0]["prev_total_s"])

    def test_menos_de_um_minuto_nao_conta(self):
        self.assertTrue(recap.build(YM, wm(total=50))["empty"])

    def test_foco_so_do_mes(self):
        r = recap.build(YM, pomo_hist={"2026-10-30": 9, "2026-11-02": 2, "2026-11-05": 4, "2026-11-06": 0})
        s = r["slides"][0]
        self.assertEqual((s["id"], s["n"], s["days"], s["best_day"]), ("focus", 6, 2, {"date": "2026-11-05", "n": 4}))

    def test_checkins_somam_jobs_e_dias(self):
        cw = {"2026-11-02": {"A4\tLogo": {"r": 2, "t": 1}, "": {"d": 1}}, "2026-11-09": {"A4\tLogo": {"r": 1}}, "2026-10-31": {"x": {"r": 99}}}
        s = recap.build(YM, cowork_days=cw)["slides"][0]
        self.assertEqual((s["id"], s["r"], s["t"], s["d"], s["total"]), ("checkins", 3, 1, 1, 5))

    def test_entregas(self):
        s = recap.build(YM, trello_by_day={"2026-11-03": 2, "2026-11-04": 1, "2026-12-01": 5}, monday_by_day={"2026-11-03": 2})["slides"][0]
        self.assertEqual((s["id"], s["trello"], s["monday"], s["total"], s["best_day"]), ("done", 3, 2, 5, {"date": "2026-11-03", "n": 4}))

    def test_dota(self):
        ms = [{"day": "2026-11-02", "w": True, "h": 5, "t": 1800}, {"day": "2026-11-03", "w": False, "h": 5, "t": 2400},
              {"day": "2026-11-03", "h": 7, "t": 600}]
        s = recap.build(YM, dota_matches=ms, hero_name=lambda h: None)["slides"][0]
        self.assertEqual((s["matches"], s["wins"], s["losses"], s["minutes"]), (3, 1, 1, 80))   # sem "w" não vira vitória nem derrota
        self.assertEqual(s["top_hero"], {"id": 5, "name": None, "n": 2})

    def test_serializa_e_nao_traz_caminho(self):
        txt = json.dumps(recap.build(YM, wm(), {"2026-11-02": 1}, None, {"2026-11-02": 1}, None, [{"day": "2026-11-02", "w": True}]))
        self.assertNotIn("\\\\", txt)
        self.assertNotIn(":/", txt)


class DotaArchive(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = Path(self.d.name, "dota-month.json")

    def tearDown(self):
        self.d.cleanup()

    def rec(self, mid, start=None, **kw):
        base = {"match_id": mid, "hero_id": 5, "win": True, "k": 5, "d": 2, "a": 9, "duration_s": 1900, "start": start or int(datetime(2026, 11, 3, 20, 0).timestamp())}
        base.update(kw)
        return base

    def test_guarda_so_o_combinado_e_nao_repete(self):
        a = recap.DotaMonth(self.p)
        self.assertEqual(a.add([self.rec(1, gpm=500, last_hits=300)]), 1)
        self.assertEqual(a.add([self.rec(1)]), 0)
        self.assertEqual(json.loads(self.p.read_text(encoding="utf-8"))["m"]["1"],
                         {"day": "2026-11-03", "w": True, "h": 5, "k": 5, "d": 2, "a": 9, "t": 1900})   # sem GPM, itens, dano
        self.assertEqual(len(recap.DotaMonth(self.p).month(YM)), 1)                                       # volta do disco
        self.assertEqual(recap.DotaMonth(self.p).month("2026-10"), [])

    def test_descarta_lixo(self):
        a = recap.DotaMonth(self.p)
        self.assertEqual(a.add([{"match_id": "x"}, {"match_id": 2}, self.rec(3, start=-1), self.rec(True), None, self.rec(4, win=None)]), 1)
        self.assertEqual(a.month(YM)[0].keys() >= {"day"}, True)
        self.assertNotIn("w", a.month(YM)[0])          # vitória desconhecida fica sem campo, nunca vira derrota

    def test_arquivo_corrompido_comeca_vazio(self):
        self.p.write_text("{nao e json", encoding="utf-8")
        self.assertEqual(recap.DotaMonth(self.p).month(YM), [])

    def test_gancho_do_opendota(self):
        got = []
        od = dota_od.OpenDota(Path(self.d.name, "dota-od.json"), lambda: "", lambda url: [], lambda: 1_000_000.0)
        od.on_recent = got.append
        od.fetch = lambda url: [{"match_id": 9, "hero_id": 5, "player_slot": 0, "radiant_win": True, "kills": 1, "deaths": 2, "assists": 3,
                                 "duration": 1800, "start_time": 1_700_000_000}]
        od._fill("sec", "recent", "/x", dota_od.trim_recent)
        self.assertEqual(got[0][0]["match_id"], 9)
        od.on_recent = lambda d: 1 / 0                  # hook quebrado nunca derruba a leitura
        od._fill("sec", "recent", "/x", dota_od.trim_recent)


class DoneRange(unittest.TestCase):
    def test_trello_e_monday_contam_so_o_que_conta(self):
        with tempfile.TemporaryDirectory() as d:
            t = trello.Trello(Path(d), lambda: None)
            t.done = {"a" * 24: {"src": "entered", "day": "2026-11-03"}, "b" * 24: {"src": "prior", "day": "2026-11-03"},
                      "c" * 24: {"src": "seen", "day": "2026-11-03", "open": True}, "d" * 24: {"src": "seen", "day": "2026-11-30"},
                      "e" * 24: {"src": "seen", "day": "2026-12-01"}}
            self.assertEqual(t.done_range("2026-11-01", "2026-11-30"), {"2026-11-03": 1, "2026-11-30": 1})
            m = monday.Monday(Path(d), lambda: None)
            m.done = {"1": {"src": "entered", "day": "2026-11-05"}, "2": {"src": "prior", "day": "2026-11-05"}, "3": {"src": "seen", "day": "2026-10-31"}}
            self.assertEqual(m.done_range("2026-11-01", "2026-11-30"), {"2026-11-05": 1})


if __name__ == "__main__":
    unittest.main()
