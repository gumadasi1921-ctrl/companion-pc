"""Trabalho contínuo (WorkTracker.stream), usado pelo mascote. Relógio injetado, pasta temporária, sem Windows.
Rodar: py -3 tests/test_work_stream.py"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import work  # noqa: E402

PS = ("photoshop.exe", "logo.psd @ 100% (RGB/8) - Adobe Photoshop")
OTHER = ("chrome.exe", "Página")


class Stream(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.w = work.WorkTracker(Path(self._d.name))

    def tearDown(self):
        self._d.cleanup()

    def run_for(self, t0, secs, fg=PS, idle=1, step=2):
        t = t0
        while t < t0 + secs:
            self.w.poll(now=t, fg=fg, idle=idle)
            t += step
        return t

    def test_sem_trabalho(self):
        self.assertEqual(self.w.stream(1_800_000_000), {"minutes": 0, "app": None})

    def test_conta_minutos(self):
        t = self.run_for(1_800_000_000, 125)
        s = self.w.stream(t)
        self.assertEqual(s["minutes"], 2)
        self.assertEqual(s["app"], "Photoshop")

    def test_lacuna_curta_nao_quebra(self):
        t = self.run_for(1_800_000_000, 120)
        t = self.run_for(t, 120, fg=OTHER)       # 2 min em outro app
        t = self.run_for(t, 120)
        self.assertGreaterEqual(self.w.stream(t)["minutes"], 5)

    def test_lacuna_longa_reinicia(self):
        t = self.run_for(1_800_000_000, 600)
        t = self.run_for(t, 400, fg=OTHER)       # > 5 min fora
        self.assertEqual(self.w.stream(t), {"minutes": 0, "app": None})
        t = self.run_for(t, 70)
        self.assertEqual(self.w.stream(t)["minutes"], 1)

    def test_parado_nao_conta(self):
        t = self.run_for(1_800_000_000, 60)
        t = self.run_for(t, 400, idle=999)       # sem input
        self.assertEqual(self.w.stream(t), {"minutes": 0, "app": None})


if __name__ == "__main__":
    unittest.main()
