"""Bateria Bluetooth: validação, aviso < 15% (uma vez, rearma > 20% ou ao sumir), erro de leitura sem inventar.
Rodar: py -3 tests/test_battery.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import alerts  # noqa: E402
import battery  # noqa: E402


def rd(*items):
    return lambda: [{"key": k, "name": n, "pct": p} for k, n, p in items]


class BatteryTests(unittest.TestCase):
    def test_valid_pct(self):
        for v in (0, 15, 100):
            self.assertEqual(battery.valid_pct(v), v)
        for v in (-1, 101, 50.5, "50", None, True):
            self.assertIsNone(battery.valid_pct(v))

    def test_descarta_valor_invalido_e_nunca_inventa(self):
        b = battery.Battery(rd(("A", "Fone", 80), ("B", "Mouse", 250), ("C", "Teclado", None)))
        b.poll()
        self.assertEqual([i["name"] for i in b.items], ["Fone"])

    def test_sem_aparelho_lista_vazia(self):
        b = battery.Battery(lambda: [])
        self.assertEqual(b.poll(), (True, []))
        self.assertEqual(b.state()["items"], [])
        self.assertEqual(b.poll(), (False, []))

    def test_id_opaco_sem_endereco(self):
        b = battery.Battery(rd(("AABBCCDDEEFF", "JBL", 60)))
        b.poll()
        i = b.items[0]
        self.assertEqual(len(i["id"]), 12)
        self.assertNotIn("AABBCCDDEEFF", repr(b.state()))

    def test_aviso_uma_vez_e_rearma(self):
        pct = [30]
        b = battery.Battery(lambda: [{"key": "A", "name": "Fone", "pct": pct[0]}])
        self.assertEqual(b.poll()[1], [])
        pct[0] = 14
        ch, al = b.poll()
        self.assertTrue(ch)
        self.assertEqual([a["pct"] for a in al], [14])
        pct[0] = 10
        self.assertEqual(b.poll()[1], [])            # já avisado
        pct[0] = 17                                  # entre 15 e 20: ainda não rearmou
        b.poll()
        pct[0] = 12
        self.assertEqual(b.poll()[1], [])
        pct[0] = 25                                  # passou de 20: rearma
        b.poll()
        pct[0] = 9
        self.assertEqual(len(b.poll()[1]), 1)

    def test_15_exato_nao_avisa(self):
        b = battery.Battery(rd(("A", "Fone", 15)))
        self.assertEqual(b.poll()[1], [])

    def test_sumiu_rearma(self):
        on = [True]
        b = battery.Battery(lambda: [{"key": "A", "name": "Fone", "pct": 5}] if on[0] else [])
        self.assertEqual(len(b.poll()[1]), 1)
        on[0] = False
        b.poll()
        on[0] = True
        self.assertEqual(len(b.poll()[1]), 1)

    def test_erro_de_leitura_mantem_estado(self):
        calls = [0]

        def fn():
            calls[0] += 1
            if calls[0] > 1:
                raise OSError("x")
            return [{"key": "A", "name": "Fone", "pct": 50}]
        b = battery.Battery(fn)
        b.poll()
        self.assertEqual(b.poll(), (False, []))
        self.assertEqual(len(b.items), 1)

    def test_addr_e_nome(self):
        self.assertEqual(battery.addr_of(r"BTHENUM\DEV_aabbccddeeff\8&362BB041&0&BLUETOOTHDEVICE_AABBCCDDEEFF"), "AABBCCDDEEFF")
        self.assertIsNone(battery.addr_of(r"USB\VID_0000&PID_0000\5&1"))
        self.assertEqual(battery.clean_name("  "), "Dispositivo Bluetooth")
        self.assertEqual(battery.message({"name": "JBL", "pct": 12}), ("Bateria baixa", "JBL: 12%"))

    def test_importante_conhece_battery(self):
        self.assertIn("battery", alerts.IMPORTANT_KINDS)
        cfg = alerts.clean_important({**alerts.IMPORTANT_DEFAULT, "kinds": ["battery"]})
        self.assertEqual(cfg["kinds"], ["battery"])
        self.assertIn("battery", alerts.IMPORTANT_DEFAULT["kinds"])

    def test_leitura_real_nao_quebra(self):
        out = battery.read_windows()          # sem aparelho conectado = []; nunca levanta
        self.assertIsInstance(out, list)
        for r in out:
            self.assertIsNotNone(battery.valid_pct(r["pct"]))


if __name__ == "__main__":
    unittest.main()
