"""Ligação job ↔ card do Trello e tempo por card (work.py + trello.py). Rodar: py -3 tests/test_work_links.py"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import trello  # noqa: E402
import work  # noqa: E402

U = work.UNIDENTIFIED
TODAY = date(2026, 11, 25)         # quarta; a semana começa em 23/11
CARD_A, CARD_B = "AAAAAAA1", "BBBBBBB2"


def days():
    return {
        "2026-11-25": {"Acme\tSite\tPremiere\ta.prproj": 600.0, f"{U}\t—\tPhotoshop\tLogo.psd": 300.0},
        "2026-11-23": {"Acme\tSite\tPremiere\ta.prproj": 1200.0, "Beta\tMarca\tIllustrator\tb.ai": 100.0},
        "2026-10-01": {"Acme\tSite\tPremiere\ta.prproj": 5000.0},
    }


class Links(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.t = work.WorkTracker(self.base)
        self.t.days = days()

    def tearDown(self):
        self._d.cleanup()

    def test_times_today_week_total(self):
        r = self.t.link_set("Acme", "Site", CARD_A, "Cartaz")
        self.assertEqual(r["replaced"], [])
        self.assertEqual(self.t.link_times(TODAY), {CARD_A: {"today_s": 600, "week_s": 1800, "total_s": 6800}})
        row = self.t.links_get(TODAY)[0]
        self.assertEqual((row["client"], row["job"], row["card"], row["card_name"], row["total_s"]), ("Acme", "Site", CARD_A, "Cartaz", 6800))

    def test_times_follow_manual_rules_and_ignored(self):
        self.t.link_set("Acme", "Site", CARD_A)
        self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Acme", "Site", True, 0, TODAY)
        self.assertEqual(self.t.link_times(TODAY)[CARD_A]["today_s"], 900)           # a regra move o tempo para o job ligado

    def test_one_card_per_job_and_one_job_per_card(self):
        a = self.t.link_set("Acme", "Site", CARD_A, "A")["link"]
        r = self.t.link_set("acme", "site", CARD_B, "B")             # mesmo job (sem diferenciar maiúsculas): troca o card
        self.assertEqual([x["id"] for x in r["replaced"]], [a["id"]])
        self.assertEqual([x["card"] for x in self.t.links], [CARD_B])
        r2 = self.t.link_set("Beta", "Marca", CARD_B, "B")           # mesmo card em outro job: troca o job
        self.assertEqual(len(r2["replaced"]), 1)
        self.assertEqual([(x["client"], x["card"]) for x in self.t.links], [("Beta", CARD_B)])

    def test_delete_and_restore_keep_history(self):
        a = self.t.link_set("Acme", "Site", CARD_A, "A")["link"]
        before = json.dumps(self.t.days, sort_keys=True)
        gone = self.t.link_delete(a["id"])["removed"]
        self.assertEqual(self.t.links, [])
        self.assertEqual(json.dumps(self.t.days, sort_keys=True), before)    # desligar nunca mexe no tempo
        self.assertEqual(self.t.link_times(TODAY), {})
        self.t.link_restore(gone)
        self.assertEqual(self.t.links[0]["id"], a["id"])
        # desfazer uma troca: apaga a nova e devolve a antiga
        r = self.t.link_set("Acme", "Site", CARD_B, "B")
        self.t.link_delete(r["link"]["id"])
        self.t.link_restore(r["replaced"])
        self.assertEqual([x["card"] for x in self.t.links], [CARD_A])

    def test_persisted_with_rules_and_old_file_without_links(self):
        self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Acme", "Site", True, 0, TODAY)
        self.t.link_set("Acme", "Site", CARD_A, "A")
        again = work.WorkTracker(self.base)
        self.assertEqual(len(again.rules), 1)
        self.assertEqual([x["card"] for x in again.links], [CARD_A])
        p = self.base / "work_rules.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d.pop("links")
        p.write_text(json.dumps(d), encoding="utf-8")               # arquivo antigo, sem "links"
        old = work.WorkTracker(self.base)
        self.assertEqual((len(old.rules), old.links), (1, []))
        d["links"] = [{"id": "x"}, {"id": "y", "client": "A", "job": "B", "card": "curto"}]   # lixo é ignorado
        p.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(work.WorkTracker(self.base).links, [])

    def test_validation(self):
        for bad in ("curto", "../../etc", None, "AAAAAAA!", "AAAAAAAAA"):
            with self.assertRaises(ValueError):
                self.t.link_set("Acme", "Site", bad)
        with self.assertRaises(ValueError):
            self.t.link_set("", "Site", CARD_A)
        self.assertEqual(self.t.link_restore([{"id": "z"}])["restored"], 0)

    def test_job_without_job_name(self):
        self.t.days["2026-11-25"]["Gama\t—\tPremiere\tg.prproj"] = 60.0
        self.t.link_set("Gama", "", CARD_A)
        self.assertEqual(self.t.link_times(TODAY)[CARD_A]["today_s"], 60)


class StateTimes(unittest.TestCase):
    def test_card_shows_spent_and_unlinked_do_not(self):
        cards = [{"id": "cardA", "short": CARD_A, "name": "Cartaz", "due": None, "dueComplete": False, "idBoard": "b", "idList": "l", "shortLink": CARD_A, "labels": []},
                 {"id": "cardB", "short": CARD_B, "name": "Outro", "due": None, "dueComplete": False, "idBoard": "b", "idList": "l", "shortLink": CARD_B, "labels": []}]

        def fetch(path, params, key, token):
            return cards if path == "members/me/cards" else [{"id": "b", "name": "Q"}] if path.endswith("boards") else [{"id": "l", "name": "Em execução"}]
        with tempfile.TemporaryDirectory() as d:
            t = trello.Trello(Path(d), lambda: {"key": "k" * 20, "token": "t" * 20, "on": True}, fetch, lambda: 1_800_000_000.0)
            t.refresh()
            st = t.state(times={CARD_A: {"today_s": 60, "week_s": 120, "total_s": 600}})
            by = {c["short"]: c for c in st["groups"]["none"]}
            self.assertEqual(by[CARD_A]["spent"], {"today_s": 60, "week_s": 120, "total_s": 600})
            self.assertNotIn("spent", by[CARD_B])
            self.assertTrue(t.has_card(CARD_A))
            self.assertEqual(t.card_name(CARD_A), "Cartaz")


if __name__ == "__main__":
    unittest.main(verbosity=1)
