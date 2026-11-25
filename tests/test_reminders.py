"""Lembretes (reminders.py): repetição (diária/semanal/dias úteis), adiar, perdido, Alta pendente, disco e validação.
Rodar: py -3 tests/test_reminders.py"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import reminders  # noqa: E402


def ep(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


# 2026-11-25 é quarta-feira
class RemindersTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.clk = Clock(ep(2026, 11, 25, 9, 0))
        self.r = reminders.Reminders(self.base, self.clk)

    def tearDown(self):
        self._d.cleanup()

    def fresh(self):
        return reminders.Reminders(self.base, self.clk)

    # -- validação ---------------------------------------------------------
    def test_validacao(self):
        for bad in [("", "2026-11-25 10:00", "none", "low"), ("x" * 121, "2026-11-25 10:00", "none", "low"),
                    ("ok", "25/11/2026 10:00", "none", "low"), ("ok", "2026-11-25 10:00", "mensal", "low"),
                    ("ok", "2026-11-25 10:00", "none", "urgente"), ("a\x00b", "2026-11-25 10:00", "none", "low"),
                    ("ok", "2026-02-31 10:00", "none", "low")]:
            with self.assertRaises(ValueError):
                self.r.add(*bad)

    def test_passado_recusado_e_repetido_no_passado_vai_para_a_proxima(self):
        with self.assertRaises(ValueError):
            self.r.add("x", "2026-11-25 08:00", "none", "normal")
        a = self.r.add("x", "2026-11-20 08:00", "daily", "normal")   # criado "no passado" mas repete
        self.assertEqual(a["next"], ep(2026, 11, 26, 8, 0))

    # -- repetição ---------------------------------------------------------
    def test_diaria(self):
        r = self.r.add("água", "2026-11-25 10:00", "daily", "normal")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        ev = self.r.tick(online=True)
        self.assertEqual([e["id"] for e in ev], [r["id"]])
        self.assertEqual(self.r.state()["items"][0]["next"], ep(2026, 11, 26, 10, 0))

    def test_semanal_mantem_o_dia_da_semana(self):
        r = self.r.add("x", "2026-11-25 10:00", "weekly", "low")   # quarta
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.r.tick(online=True)
        self.assertEqual(self.r.state()["items"][0]["next"], ep(2026, 12, 2, 10, 0))
        self.assertTrue(r["id"])

    def test_dias_uteis_pula_fim_de_semana(self):
        self.r.add("x", "2026-11-27 10:00", "weekdays", "low")   # sexta
        self.clk.t = ep(2026, 11, 27, 10, 0)
        self.r.tick(online=True)
        self.assertEqual(self.r.state()["items"][0]["next"], ep(2026, 11, 30, 10, 0))   # segunda

    def test_unico_termina(self):
        self.r.add("x", "2026-11-25 10:00", "none", "normal")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.assertEqual(len(self.r.tick(online=True)), 1)
        self.assertEqual(self.r.state()["items"][0]["state"], "done")
        self.assertEqual(self.r.tick(online=True), [])
        self.assertIsNone(self.r.next_due())

    # -- perdido (sem aparelho / atraso) -----------------------------------
    def test_sem_aparelho_vira_perdido_e_nao_avisa_depois(self):
        self.r.add("x", "2026-11-25 10:00", "none", "normal")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.assertEqual(self.r.tick(online=False), [])
        self.assertEqual(self.r.state()["items"][0]["state"], "missed")
        self.assertEqual(self.r.tick(online=True), [])

    def test_servidor_desligado_na_hora_perde_o_baixo_mas_nao_o_alto(self):
        self.r.add("b", "2026-11-25 10:00", "none", "low")
        a = self.r.add("a", "2026-11-25 10:00", "none", "high")
        self.clk.t = ep(2026, 11, 25, 15, 0)   # horas depois
        ev = self.r.tick(online=True)
        self.assertEqual([e["id"] for e in ev], [a["id"]])
        st = {i["text"]: i["state"] for i in self.r.state()["items"]}
        self.assertEqual(st, {"b": "missed", "a": "ringing"})

    def test_repetido_perdido_so_pula(self):
        self.r.add("x", "2026-11-25 10:00", "daily", "low")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.assertEqual(self.r.tick(online=False), [])
        it = self.r.state()["items"][0]
        self.assertEqual((it["state"], it["next"]), ("active", ep(2026, 11, 26, 10, 0)))

    # -- Alta: toca até Feito ou Adiar -------------------------------------
    def test_alta_toca_sem_aparelho_e_persiste_no_disco(self):
        a = self.r.add("remédio", "2026-11-25 10:00", "none", "high")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        ev = self.r.tick(online=False)
        self.assertTrue(ev and ev[0]["ringing"])
        again = self.fresh()   # servidor reiniciou
        self.assertEqual(again.state()["ringing"], [a["id"]])
        self.assertEqual(again.tick(online=True), [])   # já está tocando: não repete o evento

    def test_alta_feito(self):
        a = self.r.add("x", "2026-11-25 10:00", "none", "high")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.r.tick(online=True)
        self.r.done(a["id"])
        self.assertEqual(self.r.state()["ringing"], [])
        self.assertEqual(self.r.state()["items"][0]["state"], "done")

    def test_alta_repetida_feito_vai_para_a_proxima(self):
        a = self.r.add("x", "2026-11-25 10:00", "daily", "high")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.r.tick(online=True)
        self.r.done(a["id"])
        it = self.r.state()["items"][0]
        self.assertEqual((it["state"], it["next"]), ("active", ep(2026, 11, 26, 10, 0)))

    def test_adiar_10_min_e_toca_de_novo(self):
        a = self.r.add("x", "2026-11-25 10:00", "daily", "high")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.r.tick(online=True)
        self.r.snooze(a["id"], 10)
        it = self.r.state()["items"][0]
        self.assertEqual((it["state"], it["next"]), ("active", ep(2026, 11, 25, 10, 10)))
        self.assertEqual(self.r.next_due(), 600)
        self.clk.t = ep(2026, 11, 25, 10, 10)
        self.assertEqual(len(self.r.tick(online=True)), 1)
        self.r.done(a["id"])   # a agenda diária continua: amanhã 10:00, não 10:10
        self.assertEqual(self.r.state()["items"][0]["next"], ep(2026, 11, 26, 10, 0))

    def test_adiar_valor_invalido(self):
        a = self.r.add("x", "2026-11-25 10:00", "none", "high")
        with self.assertRaises(ValueError):
            self.r.snooze(a["id"], 7)

    # -- lista, editar, apagar, desfazer, disco ----------------------------
    def test_ordem_editar_apagar_desfazer(self):
        b = self.r.add("b", "2026-11-25 12:00", "none", "low")
        a = self.r.add("a", "2026-11-25 10:00", "none", "low")
        self.assertEqual([i["text"] for i in self.r.state()["items"]], ["a", "b"])
        self.r.edit(b["id"], "b2", "2026-11-25 09:30", "none", "normal")
        self.assertEqual([i["text"] for i in self.r.state()["items"]], ["b2", "a"])
        gone = self.r.remove(a["id"])
        self.assertEqual(len(self.r.items), 1)
        self.r.restore(gone)
        self.assertEqual(len(self.r.items), 2)
        with self.assertRaises(ValueError):
            self.r.restore(gone)   # id repetido

    def test_next_due_e_persistencia(self):
        self.r.add("x", "2026-11-25 09:30", "daily", "normal")
        self.assertEqual(self.r.next_due(), 1800)
        self.assertEqual(self.fresh().next_due(), 1800)

    def test_arquivo_corrompido_e_guardado(self):
        (self.base / "reminders.json").write_text("{nao é json", encoding="utf-8")
        r = reminders.Reminders(self.base, self.clk)
        self.assertEqual(r.items, [])
        self.assertTrue(list(self.base.glob("reminders.json.bad-*")))

    def test_limpa_encerrados_antigos_e_manual(self):
        self.r.add("x", "2026-11-25 10:00", "none", "normal")
        self.clk.t = ep(2026, 11, 25, 10, 0)
        self.r.tick(online=True)
        self.clk.t += 8 * 86400
        self.r.tick(online=True)
        self.assertEqual(self.r.items, [])
        self.r.add("y", "2026-12-10 10:00", "none", "low")
        self.assertEqual(self.r.clear_ended(), 0)

    def test_texto_nao_vai_ao_log(self):
        with self.assertNoLogs("companion.reminders"):
            self.r.add("segredo-xyz", "2026-11-25 10:00", "none", "high")
            self.clk.t = ep(2026, 11, 25, 10, 0)
            self.r.tick(online=True)


if __name__ == "__main__":
    unittest.main()
