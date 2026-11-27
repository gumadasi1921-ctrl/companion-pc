"""Testes do pomodoro.py com relógio injetável (sem esperar). Rodar: py -3 tests/test_pomodoro.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import pomodoro  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class PomoTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.c = Clock()
        self.p = pomodoro.Pomodoro(self.base, self.c)

    def tearDown(self):
        self._d.cleanup()

    def run_focus(self):
        self.p.start()
        self.c.t += 25 * 60
        return self.p.tick()

    def test_trabalhando_junto_config(self):
        c = self.p.get()["config"]
        self.assertEqual((c["cowork"], c["checkin_min"]), (True, 0))
        self.p.set_config({"cowork": False, "checkin_min": 15})
        q = pomodoro.Pomodoro(self.base, self.c).get()["config"]
        self.assertEqual((q["cowork"], q["checkin_min"]), (False, 15))
        for bad in ({"cowork": 1}, {"checkin_min": 25}, {"checkin_min": True}, {"checkin_min": "15"}):
            with self.assertRaises(ValueError):
                self.p.set_config(bad)


    def test_inicial(self):
        s = self.p.get()
        self.assertEqual((s["phase"], s["running"], s["remaining_s"]), ("idle", False, 1500))

    def test_start_pause_resume(self):
        self.p.start()
        self.assertEqual(self.p.get()["phase"], "focus")
        self.c.t += 600
        s = self.p.pause()
        self.assertFalse(s["running"])
        self.assertEqual(s["remaining_s"], 900)
        self.c.t += 5000   # pausado: o tempo não anda
        self.assertEqual(self.p.get()["remaining_s"], 900)
        self.assertIsNone(self.p.next_due())
        s = self.p.resume()
        self.assertTrue(s["running"])
        self.assertEqual(s["remaining_s"], 900)
        self.assertEqual(self.p.next_due(), 900)

    def test_tick_so_quando_vence(self):
        self.p.start()
        self.c.t += 1499
        self.assertIsNone(self.p.tick())
        self.c.t += 1
        ev = self.p.tick()
        self.assertEqual((ev["finished"], ev["next"], ev["next_min"]), ("focus", "short", 5))
        self.assertIsNone(self.p.tick())   # uma vez só
        s = self.p.get()
        self.assertEqual((s["phase"], s["running"], s["today"], s["cycle"]), ("short", False, 1, 1))

    def test_skip_nao_conta(self):
        self.p.start()
        s = self.p.skip()
        self.assertEqual((s["phase"], s["running"], s["today"], s["cycle"]), ("short", False, 0, 1))
        s = self.p.skip()
        self.assertEqual(s["phase"], "focus")

    def test_reset(self):
        self.run_focus()
        self.p.start(task_id="0123abcd")
        s = self.p.reset()
        self.assertEqual((s["phase"], s["running"], s["cycle"], s["task_id"]), ("idle", False, 0, None))
        self.assertEqual(s["today"], 1)   # histórico fica

    def test_pausa_longa_no_quarto_foco(self):
        nexts = []
        for _ in range(4):
            ev = self.run_focus()
            nexts.append(ev["next"])
            self.p.start()          # começa a pausa
            self.c.t += 15 * 60
            ev2 = self.p.tick()
            self.assertEqual(ev2["next"], "focus")
            self.assertEqual(ev2["finished"], "long" if ev["next"] == "long" else "short")
        self.assertEqual(nexts, ["short", "short", "short", "long"])
        self.assertEqual(self.p.get()["cycle"], 0)
        self.assertEqual(self.p.get()["today"], 4)

    def test_auto_next(self):
        ev = self.run_focus()
        self.assertFalse(ev["auto"])
        self.assertFalse(self.p.get()["running"])
        self.p.set_config({"auto_next": True})
        self.p.start()
        self.c.t += 5 * 60
        ev = self.p.tick()
        self.assertTrue(ev["auto"])
        s = self.p.get()
        self.assertEqual((s["phase"], s["running"], s["remaining_s"]), ("focus", True, 1500))
        self.assertEqual(self.p.next_due(), 1500)

    def test_config_limites(self):
        for bad in ({"focus_min": 4}, {"focus_min": 91}, {"short_min": 0}, {"short_min": 61}, {"long_min": 61},
                    {"long_every": 1}, {"long_every": 9}, {"focus_min": "25"}, {"focus_min": True},
                    {"auto_next": 1}, {"foco": 3}, {"focus_min": 25.5}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.p.set_config(bad)
        s = self.p.set_config({"focus_min": 5, "short_min": 1, "long_min": 60, "long_every": 8})
        self.assertEqual(s["config"]["focus_min"], 5)
        self.assertEqual(s["remaining_s"], 300)
        self.assertEqual(self.p.get()["config"]["long_every"], 8)

    def test_task_id(self):
        s = self.p.start(task_id="0123abcd")
        self.assertEqual(s["task_id"], "0123abcd")
        with self.assertRaises(ValueError):
            self.p.start(task_id="x")

    def test_json_corrompido(self):
        (self.base / "pomodoro.json").write_text("{nao é json", encoding="utf-8")
        p = pomodoro.Pomodoro(self.base, self.c)
        self.assertEqual(p.get()["phase"], "idle")
        self.assertEqual(len(list(self.base.glob("pomodoro.json.bad-*"))), 1)
        self.assertFalse((self.base / "pomodoro.json").exists())

    def test_json_estrutura_invalida(self):
        (self.base / "pomodoro.json").write_text(json.dumps({"v": 1, "cfg": {"focus_min": 999}}), encoding="utf-8")
        p = pomodoro.Pomodoro(self.base, self.c)
        self.assertEqual(p.get()["config"]["focus_min"], 25)
        self.assertEqual(len(list(self.base.glob("pomodoro.json.bad-*"))), 1)

    def test_restaura_nao_vencida(self):
        self.p.start(task_id="0123abcd")
        self.c.t += 300
        p2 = pomodoro.Pomodoro(self.base, self.c)
        s = p2.get()
        self.assertTrue(s["running"])
        self.assertEqual((s["remaining_s"], s["task_id"]), (1200, "0123abcd"))
        self.assertFalse(p2.restored_expired)

    def test_restaura_pausada(self):
        self.p.start()
        self.c.t += 100
        self.p.pause()
        self.c.t += 99999
        s = pomodoro.Pomodoro(self.base, self.c).get()
        self.assertEqual((s["running"], s["remaining_s"]), (False, 1400))

    def test_restaura_vencida(self):
        self.p.start()
        self.c.t += 25 * 60 + 3600
        p2 = pomodoro.Pomodoro(self.base, self.c)
        self.assertTrue(p2.restored_expired)
        s = p2.get()
        self.assertEqual((s["phase"], s["running"], s["today"], s["cycle"]), ("short", False, 1, 1))
        self.assertIsNone(p2.tick())   # sem evento (sem aviso)
        self.assertIsNone(p2.next_due())
        # e persistiu: um terceiro carregamento não conta de novo
        p3 = pomodoro.Pomodoro(self.base, self.c)
        self.assertEqual(p3.get()["today"], 1)
        self.assertFalse(p3.restored_expired)

    def test_mensagens(self):
        self.assertEqual(pomodoro.message({"finished": "focus", "next": "short", "next_min": 5}),
                         "Foco concluído! Pausa de 5 min")
        self.assertEqual(pomodoro.message({"finished": "short", "next": "focus", "next_min": 25}),
                         "Pausa acabou, bora focar")


if __name__ == "__main__":
    unittest.main()
