"""Testes do pareamento (sem dados reais; relógio falso). Rodar: py -3 tests/test_pair.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import pair  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class PairTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.p = pair.Pairing(self.c)

    def test_formato(self):
        code = self.p.new()
        self.assertRegex(code, r"^\d{6}$")

    def test_certo(self):
        code = self.p.new()
        self.assertEqual(self.p.check("1.1.1.1", code), "ok")

    def test_errado(self):
        self.p.new()
        self.assertEqual(self.p.check("1.1.1.1", "abcdef"), "bad")
        self.assertEqual(self.p.check("1.1.1.1", None), "bad")
        self.assertEqual(self.p.check("1.1.1.1", "é" * 6), "bad")

    def test_vencido(self):
        code = self.p.new()
        self.c.t += pair.CODE_TTL_S + 1
        self.assertEqual(self.p.check("1.1.1.1", code), "bad")
        self.assertEqual(self.p.state()["dead"], "vencido")

    def test_ainda_vale_antes_do_prazo(self):
        code = self.p.new()
        self.c.t += pair.CODE_TTL_S - 1
        self.assertEqual(self.p.check("1.1.1.1", code), "ok")

    def test_reuso(self):
        code = self.p.new()
        self.assertEqual(self.p.check("1.1.1.1", code), "ok")
        self.assertEqual(self.p.check("2.2.2.2", code), "bad")

    def test_sem_codigo(self):
        self.assertEqual(self.p.check("1.1.1.1", "000000"), "bad")

    def test_bloqueio_por_ip(self):
        code = self.p.new()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.p.check("9.9.9.9", wrong), "bad")
        self.assertEqual(self.p.check("9.9.9.9", wrong), "blocked")
        self.assertEqual(self.p.check("9.9.9.9", code), "blocked")      # recusa até o código certo
        self.assertEqual(self.p.check("8.8.8.8", code), "ok")            # outro IP não é afetado
        self.p.new()
        self.c.t += pair.IP_BLOCK_S + 1
        code2 = self.p.new()
        self.assertEqual(self.p.check("9.9.9.9", code2), "ok")           # bloqueio acabou

    def test_janela_do_ip(self):
        code = self.p.new()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.p.check("9.9.9.9", wrong)
        self.c.t += pair.IP_WINDOW_S + 1                                  # erros velhos saem da conta
        self.p.check("9.9.9.9", wrong)
        self.assertEqual(self.p.check("9.9.9.9", code), "ok")

    def test_20_erros_invalidam_o_codigo(self):
        code = self.p.new()
        wrong = "000000" if code != "000000" else "111111"
        for i in range(20):
            self.p.check(f"10.0.0.{i}", wrong)                            # IPs diferentes: não cai no bloqueio por IP
        st = self.p.state()
        self.assertIsNone(st["code"])
        self.assertEqual(st["dead"], "tentativas")
        self.assertEqual(self.p.check("10.0.1.1", code), "bad")           # nem o certo vale mais
        self.assertRegex(self.p.new(), r"^\d{6}$")                        # novo código volta ao normal
        self.assertIsNone(self.p.state()["dead"])

    def test_painel_fechado(self):
        code = self.p.new()
        self.p.close()
        self.assertIsNone(self.p.state()["code"])
        self.assertEqual(self.p.state()["dead"], "fechado")
        self.assertEqual(self.p.check("1.1.1.1", code), "bad")

    def test_novo_codigo_invalida_o_anterior(self):
        a = self.p.new()
        b = self.p.new()
        if a != b:
            self.assertEqual(self.p.check("1.1.1.1", a), "bad")
        self.assertEqual(self.p.check("1.1.1.1", b), "ok")

    def test_contagem(self):
        self.p.new()
        self.assertEqual(self.p.state()["left"], pair.CODE_TTL_S)
        self.c.t += 15
        self.assertEqual(self.p.state()["left"], pair.CODE_TTL_S - 15)


if __name__ == "__main__":
    unittest.main()
