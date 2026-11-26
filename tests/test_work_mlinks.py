"""Ligação job <-> item do monday e tempo por item (work.py). Rodar: py -3 tests/test_work_mlinks.py"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import work  # noqa: E402

TODAY = date(2026, 11, 25)         # quarta; a semana começa em 23/11


def days():
    return {
        "2026-11-25": {"Acme\tSite\tPremiere\ta.prproj": 600.0},
        "2026-11-23": {"Acme\tSite\tPremiere\ta.prproj": 1200.0, "Beta\tMarca\tIllustrator\tb.ai": 100.0},
        "2026-10-01": {"Acme\tSite\tPremiere\ta.prproj": 5000.0},
    }


class MLinks(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.t = work.WorkTracker(self.base)
        self.t.days = days()

    def tearDown(self):
        self._d.cleanup()

    def test_times(self):
        self.t.mlink_set("Acme", "Site", "123", "Cartaz")
        self.assertEqual(self.t.mlink_times(TODAY), {"123": {"today_s": 600, "week_s": 1800, "total_s": 6800}})
        row = self.t.mlinks_get(TODAY)[0]
        self.assertEqual((row["client"], row["job"], row["item"], row["item_name"]), ("Acme", "Site", "123", "Cartaz"))

    def test_one_item_per_job_and_one_job_per_item(self):
        a = self.t.mlink_set("Acme", "Site", "1", "A")["link"]
        r = self.t.mlink_set("acme", "site", "2", "B")
        self.assertEqual([x["id"] for x in r["replaced"]], [a["id"]])
        r2 = self.t.mlink_set("Beta", "Marca", "2", "B")
        self.assertEqual(len(r2["replaced"]), 1)
        self.assertEqual([(x["client"], x["item"]) for x in self.t.mlinks], [("Beta", "2")])

    def test_invalid_item_and_persistence(self):
        for bad in ("abc", "../1", "", None, "1" * 21):
            with self.assertRaises(ValueError):
                self.t.mlink_set("Acme", "Site", bad)
        self.t.mlink_set("Acme", "Site", "55", "X")
        t2 = work.WorkTracker(self.base)
        self.assertEqual([x["item"] for x in t2.mlinks], ["55"])

    def test_delete_restore_keeps_history_and_trello_links(self):
        self.t.link_set("Beta", "Marca", "AAAAAAA1", "T")
        a = self.t.mlink_set("Acme", "Site", "9", "A")["link"]
        before = json.dumps(self.t.days, sort_keys=True)
        gone = self.t.mlink_delete(a["id"])["removed"]
        self.assertEqual(self.t.mlinks, [])
        self.assertEqual(len(self.t.links), 1)                 # Trello intocado
        self.t.mlink_restore(gone)
        self.assertEqual([x["id"] for x in self.t.mlinks], [a["id"]])
        self.assertEqual(json.dumps(self.t.days, sort_keys=True), before)

    def test_job_can_have_both_trello_and_monday(self):
        self.t.link_set("Acme", "Site", "AAAAAAA1", "T")
        self.t.mlink_set("Acme", "Site", "7", "M")
        self.assertEqual(len(self.t.links), 1)
        self.assertEqual(len(self.t.mlinks), 1)


if __name__ == "__main__":
    unittest.main()
