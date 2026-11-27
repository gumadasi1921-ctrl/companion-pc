"""Ocioso descartado (WorkTracker.idle_report): tempo com o programa em foco que NÃO entrou no total. Relógio injetado.
Rodar: py -3 tests/test_work_idle.py"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import work  # noqa: E402

PS = ("photoshop.exe", "logo.psd @ 100% (RGB/8) - Adobe Photoshop")
AI = ("illustrator.exe", "arte.ai @ 66 % (RGB/Visualizar)")
OTHER = ("chrome.exe", "Página")
T0 = 1_800_000_000 - (1_800_000_000 % 86400) + 12 * 3600   # meio do dia (UTC), longe da meia-noite local na maioria dos fusos


class Idle(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.w = work.WorkTracker(self.base, 2)

    def tearDown(self):
        self._d.cleanup()

    def work_then_away(self, away, fg=PS, work_s=60):
        """work_s com input constante, depois `away` s sem nenhum input (idle cresce)."""
        t = T0
        while t < T0 + work_s:
            self.w.poll(now=t, fg=fg, idle=1)
            t += 2
        last = t - 1
        end = t + away
        while t < end:
            self.w.poll(now=t, fg=fg, idle=t - last)
            t += 2
        return t

    def counted(self):
        return sum(s for d in self.w.days.values() for s in d.values())

    def test_pausa_longa_vira_descartado_e_nao_soma(self):
        self.work_then_away(400)
        counted = self.counted()
        self.assertAlmostEqual(counted, 60, delta=4)           # só o tempo com input
        r = self.w.idle_report(7, today=date.fromtimestamp(T0))
        self.assertEqual(r["limit_min"], 2)
        self.assertAlmostEqual(r["total_s"], 400, delta=6)     # o resto ficou anotado à parte
        self.assertEqual(r["n"], 1)
        self.assertEqual(list(r["days"][-1]["by_app"]), ["Photoshop"])

    def test_pausa_curta_conta_e_nao_descarta(self):
        self.work_then_away(60)                                # 60 s < 120 s: ainda pendente
        r = self.w.idle_report(7, today=date.fromtimestamp(T0))
        self.assertEqual(r["total_s"], 0)

    def test_teto_de_30_minutos(self):
        self.work_then_away(3 * 3600)
        r = self.w.idle_report(7, today=date.fromtimestamp(T0))
        self.assertLessEqual(r["total_s"], work.IDLE_DROP_MAX_S + 4)
        self.assertGreater(r["total_s"], work.IDLE_DROP_MAX_S - 10)

    def test_fora_de_programa_de_trabalho_nao_anota(self):
        self.work_then_away(400, fg=OTHER)
        self.assertEqual(self.w.idle_report(7, today=date.fromtimestamp(T0))["total_s"], 0)

    def test_duas_pausas_dois_cortes_por_programa(self):
        t = self.work_then_away(300)
        last = t
        while t < last + 40:
            self.w.poll(now=t, fg=AI, idle=1)
            t += 2
        for k in range(150):
            self.w.poll(now=t + 2 * k, fg=AI, idle=2 * k + 1)
        r = self.w.idle_report(7, today=date.fromtimestamp(T0))
        self.assertEqual(r["n"], 2)
        self.assertEqual(set(r["days"][-1]["by_app"]), {"Photoshop", "Illustrator"})

    def test_grava_e_recarrega_sem_nome_de_arquivo(self):
        self.work_then_away(400)
        self.w.save()
        raw = (self.base / "work-idle.json").read_text(encoding="utf-8")
        self.assertNotIn("logo", raw)
        w2 = work.WorkTracker(self.base, 2)
        self.assertEqual(w2.idle_report(7, today=date.fromtimestamp(T0))["total_s"],
                         self.w.idle_report(7, today=date.fromtimestamp(T0))["total_s"])


if __name__ == "__main__":
    unittest.main()
