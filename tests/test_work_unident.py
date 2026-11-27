"""Aviso diário de "Não identificado" (alerts.DailyUnident/unident_message) e WorkTracker.unident_summary. Sem rede, sem Windows.
Rodar: py -3 tests/test_work_unident.py"""
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import alerts  # noqa: E402
import work  # noqa: E402

TODAY = date(2026, 11, 26)


class Summary(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.w = work.WorkTracker(self.base)
        U = work.UNIDENTIFIED
        self.w.days = {TODAY.isoformat(): {
            f"{U}\t—\tPhotoshop\tSem título-1": 600, f"{U}\t—\tPhotoshop\tcartaz.psd": 300, f"{U}\t—\tPremiere\tsem título": 120,
            "Cli\tJob\tPhotoshop\tok.psd": 900}, "2026-11-25": {f"{U}\t—\tPhotoshop\tontem.psd": 999}}

    def tearDown(self):
        self._d.cleanup()

    def test_soma_so_de_hoje_e_so_nao_identificado(self):
        s = self.w.unident_summary(TODAY)
        self.assertEqual(s["total_s"], 1020)
        self.assertEqual(s["by_app"], {"Photoshop": 900, "Premiere": 120})
        self.assertEqual(s["generic_s"], 720)
        self.assertEqual({(f["file"], f["app"]): f["s"] for f in s["files"]}, {("Sem título-1", "Photoshop"): 600, ("sem título", "Premiere"): 120})

    def test_regra_manual_e_ignorado_saem(self):
        self.w.ignore([{"file": "cartaz.psd", "app": "Photoshop"}], 0, today=TODAY)
        s = self.w.unident_summary(TODAY)
        self.assertEqual(s["total_s"], 720)


class Notice(unittest.TestCase):
    def test_mensagem(self):
        t, b = alerts.unident_message({"total_s": 4500, "by_app": {"Photoshop": 3600, "CapCut": 900}})
        self.assertIn("1 h 15", b)
        self.assertIn("Photoshop 1 h 00", b)
        self.assertIn("CapCut 15 min", b)
        self.assertIsNone(alerts.unident_message({"total_s": 59, "by_app": {"Photoshop": 59}}))

    def test_config(self):
        self.assertEqual(alerts.clean_unident({"on": True, "at": "07:30"}), {"on": True, "at": "07:30"})
        for bad in ({"on": True, "at": "25:00"}, {"on": "sim", "at": "18:00"}, {"on": True}, "18:00", {"on": True, "at": "9:00"}):
            with self.assertRaises(ValueError):
                alerts.clean_unident(bad)
        self.assertEqual(alerts.unident_cfg(None), alerts.UNIDENT_DEFAULT)

    def test_uma_vez_por_dia_na_janela(self):
        d = alerts.DailyUnident()
        cfg = {"on": True, "at": "18:00"}
        self.assertFalse(d.due(datetime(2026, 11, 26, 17, 59), cfg))
        self.assertTrue(d.due(datetime(2026, 11, 26, 18, 0), cfg))
        d.mark(datetime(2026, 11, 26, 18, 0))
        self.assertFalse(d.due(datetime(2026, 11, 26, 18, 5), cfg))
        self.assertTrue(d.due(datetime(2026, 11, 27, 18, 1), cfg))
        self.assertFalse(d.due(datetime(2026, 11, 28, 22, 30), cfg))   # passou de 4 h: pula o dia
        self.assertFalse(d.due(datetime(2026, 11, 27, 18, 1), {"on": False, "at": "18:00"}))

    def test_reinicio_nao_repete(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "unident-notice.json"
            d = alerts.DailyUnident(p)
            d.mark(datetime(2026, 11, 26, 18, 0))
            d2 = alerts.DailyUnident(p)
            self.assertFalse(d2.due(datetime(2026, 11, 26, 18, 30), {"on": True, "at": "18:00"}))
            self.assertTrue(d2.due(datetime(2026, 11, 27, 18, 30), {"on": True, "at": "18:00"}))


if __name__ == "__main__":
    unittest.main()
