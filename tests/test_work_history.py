"""Histórico mensal, notas do dia e retenção do work.py. Dados fictícios, pasta temporária.
Rodar: py -3 tests/test_work_history.py"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import work  # noqa: E402

U = work.UNIDENTIFIED
TODAY = date(2026, 11, 24)


def days():
    return {
        "2026-11-03": {"Acme\tSite\tPremiere\ta.prproj": 3600.0, f"{U}\t—\tPhotoshop\tLogo.psd": 600.0},
        "2026-11-24": {"Acme\tSite\tPremiere\ta.prproj": 1800.0, "Beta\tMarca\tIllustrator\tb.ai": 900.0,
                       f"{U}\t—\tPhotoshop\tlixo.psd": 500.0},
        "2026-10-30": {"Acme\tSite\tPremiere\ta.prproj": 1000.0, "Acme\tApp\tPremiere\tc.prproj": 200.0},
        "2020-01-15": {"Acme\tSite\tPremiere\ta.prproj": 77.0},     # muito antigo: nunca é apagado
    }


class History(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.t = work.WorkTracker(self.base)
        self.t.days = days()

    def tearDown(self):
        self._d.cleanup()

    def test_month_total_is_sum_of_days_and_clients(self):
        m = self.t.month("2026-11", TODAY)
        self.assertEqual(m["total_s"], 3600 + 600 + 1800 + 900 + 500)
        self.assertEqual(sum(d["s"] for d in m["days"]), m["total_s"])
        self.assertEqual(sum(c["s"] for c in m["clients"]), m["total_s"])
        self.assertEqual(len(m["days"]), 30)
        self.assertEqual(m["days_worked"], 2)
        self.assertEqual(m["prev"], "2026-10")
        self.assertEqual(m["prev_total_s"], 1200)
        acme = next(c for c in m["clients"] if c["client"] == "Acme")
        self.assertEqual((acme["s"], acme["prev_s"]), (5400, 1200))
        self.assertEqual([(j["job"], j["s"], j["prev_s"]) for j in acme["jobs"]], [("Site", 5400, 1000)])
        self.assertTrue(m["days"][23]["today"])

    def test_rules_and_ignored_apply_like_the_week(self):
        self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Beta", "Marca", True, 0, TODAY)
        self.t.ignore([{"file": "lixo.psd", "app": "Photoshop"}], 0, TODAY)
        m = self.t.month("2026-11", TODAY)
        self.assertEqual(m["total_s"], 3600 + 600 + 1800 + 900)          # o ignorado saiu
        c = {x["client"]: x["s"] for x in m["clients"]}
        self.assertEqual(c["Beta"], 600 + 900)
        self.assertNotIn(U, c)
        # a semana de 23/11 bate com o mês no que as duas cobrem
        self.assertEqual(self.t.week(0, TODAY)["total_s"], 1800 + 900)

    def test_nothing_is_pruned(self):
        before = json.dumps(self.t.days, sort_keys=True)
        self.t.dirty = True
        self.t.save()
        again = work.WorkTracker(self.base)
        self.assertEqual(json.dumps(again.days, sort_keys=True), before)
        self.assertEqual(again.month("2020-01", TODAY)["total_s"], 77)
        # poll de hoje não apaga dias antigos
        again.poll(now=1_800_000_000.0, fg=("notepad.exe", "x"), idle=0)
        self.assertIn("2020-01-15", again.days)

    def test_month_validation(self):
        for bad in ("2026-13", "26-11", "2026-1", 202611, "1999-12", "2026-11-01"):
            with self.assertRaises(ValueError):
                self.t.month(bad, TODAY)
        self.assertEqual(self.t.month(None, TODAY)["month"], "2026-11")
        self.assertEqual(self.t.month("2026-01", TODAY)["prev"], "2025-12")
        self.assertEqual(work.prev_month("2026-01"), "2025-12")
        self.assertEqual(work.prev_month("2026-03"), "2026-02")

    def test_notes(self):
        r = self.t.note_set("2026-11-24", "  Reunião com a Acme\nentrega às 17h ")
        self.assertEqual(r["note"], "Reunião com a Acme\nentrega às 17h")
        again = work.WorkTracker(self.base)
        self.assertEqual(again.notes["2026-11-24"], r["note"])
        day = again.month("2026-11", TODAY)["days"][23]
        self.assertEqual(day["note"], r["note"])
        self.assertNotIn("note", again.month("2026-11", TODAY, notes=False)["days"][23])   # celular não recebe notas
        self.assertIsNone(next((d.get("note") for d in again.month("2026-11", TODAY)["days"] if d["date"] == "2026-11-03"), None))
        self.t.note_set("2026-11-24", "x" * 500)
        with self.assertRaises(ValueError):
            self.t.note_set("2026-11-24", "x" * 501)
        for bad in ("2026-02-30", "ontem", None, "1999-01-01"):
            with self.assertRaises(ValueError):
                self.t.note_set(bad, "oi")
        with self.assertRaises(ValueError):
            self.t.note_set("2026-11-24", 5)
        self.t.note_set("2026-11-24", "a\x00b\x07c")
        self.assertEqual(self.t.notes["2026-11-24"], "abc")
        self.t.note_set("2026-11-24", "   ")
        self.assertNotIn("2026-11-24", self.t.notes)
        self.assertFalse((self.base / "work-notes.json.tmp").exists())

    def test_bad_notes_file_is_set_aside(self):
        (self.base / "work-notes.json").write_text("{nao é json", encoding="utf-8")
        t = work.WorkTracker(self.base)
        self.assertEqual(t.notes, {})
        self.assertTrue((self.base / "work-notes.json.bak").exists())

    def test_csv_month(self):
        self.t.note_set("2026-11-03", "dia =cheio; \"ok\"")
        self.t.note_set("2026-11-10", "só nota")
        name, data = self.t.csv_month("2026-11", TODAY)
        self.assertEqual(name, "trabalho-2026-11.csv")
        txt = data.decode("utf-8-sig")
        lines = txt.strip().split("\r\n")
        self.assertEqual(lines[0], "data;cliente;job;app;arquivo;minutos;nota")
        d3 = [ln for ln in lines if ln.startswith("03/11/2026")]
        self.assertEqual(len(d3), 2)
        self.assertIn('"dia =cheio; ""ok"""', d3[0] + d3[1])
        self.assertEqual(sum('"dia =cheio' in ln for ln in d3), 1)         # nota só na 1ª linha do dia
        self.assertTrue(any(ln.startswith("10/11/2026;;;;;0;só nota") for ln in lines))
        self.assertFalse(any("2026-10" in ln or "30/10/2026" in ln for ln in lines))
        mins = sum(float(ln.split(";")[5].replace(",", ".")) for ln in lines[1:])
        self.assertAlmostEqual(mins * 60, self.t.month("2026-11", TODAY)["total_s"], delta=3)


if __name__ == "__main__":
    unittest.main(verbosity=1)
