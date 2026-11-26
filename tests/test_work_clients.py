"""Cliente de um card "CLIENTE - JOB" <-> cliente do Tempo de trabalho (work.py + trello.py), sem rede.
Rodar: py -3 tests/test_work_clients.py"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import trello  # noqa: E402
import work  # noqa: E402

TODAY = date(2026, 11, 25)


class Clients(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.t = work.WorkTracker(Path(self._d.name))
        self.t.days = {"2026-11-25": {"Top Market\tPost 20\tPhotoshop\ta.psd": 600.0, "El Viejo\tTV\tPremiere\tb.prproj": 300.0, "Imperatriz\tTabela\tPhotoshop\tc.psd": 60.0}}

    def tearDown(self):
        self._d.cleanup()

    def test_split_name_is_the_fixed_rule(self):
        self.assertEqual(trello.split_name("TOP MARKET - POST 20 - EP 1 ÁRVORE"), ("TOP MARKET", "POST 20 - EP 1 ÁRVORE"))
        self.assertEqual(trello.split_name("DE MANHÃ:")[0], None)
        self.assertEqual(trello.split_name("A-B")[0], None)

    def test_match_ignores_case_accents_and_spaces_only(self):
        m = self.t.card_match("TOP   MARKET - post 20")
        self.assertEqual(m, {"client": "Top Market", "job": "Post 20"})
        self.assertEqual(self.t.card_match("EL VÍEJO - TV")["client"], "El Viejo")        # acento ignorado
        self.assertEqual(self.t.card_match("EL VIEJOS - TV")["client"], None)            # só igualdade: nada de "parecido"
        self.assertEqual(self.t.card_match("el viejo - Outro job"), {"client": "El Viejo", "job": None})   # cliente casa, job não
        self.assertEqual(self.t.card_match("Cliente Novo - X"), {"client": None, "job": None})
        self.assertEqual(self.t.card_match("Kappa"), {"client": None, "job": None})      # sem " - " = sem cliente

    def test_accents_on_both_sides_match(self):
        self.t.days["2026-11-25"]["Açaí Bar\tPôster\tPhotoshop\td.psd"] = 10.0
        self.assertEqual(self.t.card_match("ACAI BAR - POSTER"), {"client": "Açaí Bar", "job": "Pôster"})

    def test_week_gets_done_per_client_and_unmatched_goes_to_other(self):
        w = self.t.week(0, TODAY)
        names = ["TOP MARKET - POST 21", "top market - banner", "EL VIEJO - TV VERTICAL", "NOVO CLIENTE - X", "Kappa"]
        r = self.t.with_done(w, names)
        done = {c["client"]: c.get("done", 0) for c in r["clients"]}
        self.assertEqual((done["Top Market"], done["El Viejo"], done.get("Imperatriz", 0)), (2, 1, 0))
        self.assertEqual((r["done_total"], r["done_other"]), (5, 2))
        self.assertEqual([c["s"] for c in r["clients"]], [c["s"] for c in w["clients"]])  # as horas nunca mudam

    def test_client_with_done_but_no_hours_becomes_a_row(self):
        self.t.days["2026-10-01"] = {"Antigo\tX\tPhotoshop\te.psd": 5.0}
        r = self.t.with_done(self.t.week(0, TODAY), ["ANTIGO - Y"])
        row = next(c for c in r["clients"] if c["client"] == "Antigo")
        self.assertEqual((row["s"], row["done"], row["jobs"]), (0, 1, []))

    def test_unidentified_never_gets_done_and_no_names_is_neutral(self):
        self.t.days["2026-11-25"][f"{work.UNIDENTIFIED}\t—\tPhotoshop\tz.psd"] = 5.0
        r = self.t.with_done(self.t.week(0, TODAY), ["NÃO IDENTIFICADO - X"])
        self.assertTrue(all("done" not in c for c in r["clients"] if c.get("unidentified")))
        r0 = self.t.with_done(self.t.week(0, TODAY), [])
        self.assertEqual((r0["done_total"], r0["done_other"]), (0, 0))

    def test_month_rows_keep_prev_s_and_trello_done_names_come_from_full_history(self):
        t = trello.Trello(Path(self._d.name), lambda: {}, lambda *a: [], lambda: 1795700000)
        t.done = {"c1": {"name": "TOP MARKET - POST 1", "src": "entered", "day": "2026-11-20"},
                  "c2": {"name": "X - Y", "src": "prior", "day": None},
                  "c3": {"name": "EL VIEJO - TV", "src": "seen", "day": "2026-10-02", "open": True},
                  "c4": {"name": "OLD - Z", "src": "entered", "day": "2026-01-05"}}
        self.assertEqual(t.done_names("2026-11-01", "2026-11-30"), ["TOP MARKET - POST 1"])   # prior e reaberto não contam
        self.assertEqual(t.done_names("2026-01-01", "2026-12-31"), ["TOP MARKET - POST 1", "OLD - Z"])
        m = self.t.month("2026-11", TODAY)
        r = self.t.with_done(m, ["TOP MARKET - POST 1"])
        self.assertIn("prev_s", r["clients"][0])


if __name__ == "__main__":
    unittest.main()
