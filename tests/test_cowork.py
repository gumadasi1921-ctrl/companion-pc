"""Trabalhando junto (cowork.py): tempo no job durante o foco, check-in, resumo, disco e validação.
Rodar: py -3 tests/test_cowork.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import cowork  # noqa: E402

T0 = 1_800_000_000.0


def info(job="A4", client="Acme", app="Photoshop", ident=True, counting=True, ignored=False):
    return {"app": app, "client": client, "job": job, "identified": ident, "counting": counting, "ignored": ignored}


class CoWorkTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.cw = cowork.CoWork(self.base, clock=lambda: T0)

    def tearDown(self):
        self._d.cleanup()

    def run_focus(self, secs, inf, every=0, start=T0, running=True):
        t = start
        for _ in range(int(secs // 2)):
            t += 2
            self.cw.step(inf, "focus", running, True, every, now=t)
        return t

    def pub(self, running=True, every=0):
        return self.cw.public("focus", running, True, every)

    def test_soma_so_com_foco_rodando(self):
        self.cw.step(info(), "focus", True, True, 0, now=T0)
        t = self.run_focus(120, info(), start=T0)
        self.assertEqual(self.pub()["live"]["min"], 2)
        self.assertEqual(self.pub()["live"]["job"], "A4")
        # pausado: não soma
        self.cw.step(info(), "focus", False, True, 0, now=t + 2)
        self.run_focus(120, info(), start=t + 2, running=False)
        self.assertEqual(self.pub(False)["live"]["min"], 2)

    def test_sem_job_so_programa(self):
        self.cw.step(info(job=None, ident=False), "focus", True, True, 0, now=T0)
        self.run_focus(60, info(job=None, ident=False, client=None))
        live = self.pub()["live"]
        self.assertIsNone(live["job"])
        self.assertEqual(live["app"], "Photoshop")
        self.assertEqual(live["min"], 1)

    def test_ocioso_e_ignorado_nao_contam(self):
        self.cw.step(info(), "focus", True, True, 0, now=T0)
        self.run_focus(60, info(counting=False))
        self.run_focus(60, info(ignored=True), start=T0 + 60)
        self.assertEqual(self.pub()["live"]["min"], 0)
        self.assertIsNone(self.pub()["live"]["job"])

    def test_lacuna_grande_nao_soma(self):
        self.cw.step(info(), "focus", True, True, 0, now=T0)
        self.cw.step(info(), "focus", True, True, 0, now=T0 + 600)
        self.assertEqual(self.pub()["live"]["min"], 0)

    def test_fim_do_foco_pede_checkin_e_grava(self):
        self.cw.step(info(), "focus", True, True, 0, now=T0)
        self.run_focus(180, info())
        self.assertIsNone(self.pub()["ask"])      # padrão "fim": nada no meio
        self.cw.focus_done()
        p = self.cw.public("short", False, True, 0)
        self.assertEqual(p["summary"]["min"], 3)
        self.assertEqual(p["summary"]["job"], "A4")
        self.assertTrue(p["ask"]["end"])
        self.assertTrue(self.cw.answer(p["ask"]["id"], "t"))
        p2 = self.cw.public("short", False, True, 0)
        self.assertIsNone(p2["ask"])
        self.assertEqual(p2["summary"]["answer"], "t")
        day = next(iter(self.cw.days))
        self.assertEqual(self.cw.days[day]["Acme\tA4"], {"t": 1})
        self.assertEqual(cowork.CoWork(self.base).days, self.cw.days)   # persistiu

    def test_checkin_a_cada_15_min_e_nao_repete_depois_de_responder(self):
        self.cw.step(info(), "focus", True, True, 15, now=T0)
        self.run_focus(14 * 60, info(), every=15)
        self.assertIsNone(self.pub(every=15)["ask"])
        self.run_focus(60, info(), every=15, start=T0 + 14 * 60)
        ask = self.pub(every=15)["ask"]
        self.assertEqual(ask["end"], False)
        self.assertFalse(self.cw.answer(ask["id"] + 1, "r"))    # id errado
        self.assertTrue(self.cw.answer(ask["id"], "r"))
        self.run_focus(600, info(), every=15, start=T0 + 15 * 60)
        self.assertIsNone(self.pub(every=15)["ask"])
        self.cw.focus_done()
        self.assertIsNone(self.cw.public("short", False, True, 15)["ask"])   # já respondeu neste foco

    def test_pular_ou_zerar_nao_guarda_nada(self):
        self.cw.step(info(), "focus", True, True, 0, now=T0)
        self.run_focus(60, info())
        self.cw.sync("short", False, True)          # foco pulado
        p = self.cw.public("short", False, True, 0)
        self.assertIsNone(p["summary"])
        self.assertIsNone(p["ask"])
        self.assertEqual(self.cw.days, {})

    def test_valor_invalido_e_desligado(self):
        self.cw.step(info(), "focus", True, True, 0, now=T0)
        self.run_focus(10, info())
        self.cw.focus_done()
        with self.assertRaises(ValueError):
            self.cw.answer(self.cw.ask["id"], "x")
        self.cw.sync("focus", True, False)
        self.assertFalse(self.cw.public("focus", True, False, 0)["on"])
        self.assertIsNone(self.cw.public("focus", True, False, 0)["live"])

    def test_sem_tempo_no_job_resumo_sem_job(self):
        self.cw.step(info(job=None, ident=False, app=None), "focus", True, True, 0, now=T0)
        self.cw.focus_done()
        self.assertEqual(self.cw.summary["min"], 0)
        self.assertIsNone(self.cw.summary["job"])

    def test_disco_corrompido_vira_bad(self):
        (self.base / "cowork.json").write_text("{nao e json", encoding="utf-8")
        cw = cowork.CoWork(self.base)
        self.assertEqual(cw.days, {})
        self.assertTrue(list(self.base.glob("cowork.json.bad-*")))

    def test_arquivo_sem_texto_livre(self):
        self.cw.step(info(), "focus", True, True, 0, now=T0)
        self.cw.focus_done()
        self.cw.answer(self.cw.ask["id"], "d")
        d = json.loads((self.base / "cowork.json").read_text(encoding="utf-8"))
        self.assertEqual(set(d), {"v", "days"})

    def test_every_valido(self):
        self.assertEqual([cowork.clean_every(v) for v in (0, 15, 25, True, "15", None)], [0, 15, 0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
