"""Bateria de mouse com receptor 2,4 GHz: interpretação da resposta, filtro de interface, cadência de 5 min e aviso.
Só bytes de exemplo falsos (nenhum VID/PID/serial real). Rodar: py -3 tests/test_battery_receiver.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import battery  # noqa: E402

ECHO = bytes([0xAA, 0x30, 0xA5, 0x0B, 0x0A, 0x01, 0x01, 0x00])


def rep(pct, chg=0, extra=0):
    return ECHO + bytes([pct, chg]) + bytes(extra)


def pad(b, n=64):
    return b + bytes(n - len(b))


class ParseTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(battery.parse_receiver(pad(rep(97))), (97, False))

    def test_com_report_id_zero_na_frente(self):
        self.assertEqual(battery.parse_receiver(b"\x00" + pad(rep(61))), (61, False))

    def test_carregando(self):
        self.assertEqual(battery.parse_receiver(pad(rep(42, 1))), (42, True))
        self.assertEqual(battery.parse_receiver(pad(rep(42, 2))), (42, True))

    def test_zero_ou_fora_da_faixa_e_sem_dado(self):
        for v in (0, 101, 200, 255):
            self.assertIsNone(battery.parse_receiver(pad(rep(v))), v)

    def test_aa_fa_d0(self):
        r = pad(bytes([0xAA, 0xFA, 0, 0, 0, 0, 0, 0, 0xD0, 55, 1]))
        self.assertEqual(battery.parse_receiver(r), (55, True))
        r = pad(bytes([0xAA, 0xFA, 0, 0, 0, 0, 0, 0, 0xD0, 55, 0]))
        self.assertEqual(battery.parse_receiver(r), (55, False))
        self.assertIsNone(battery.parse_receiver(pad(bytes([0xAA, 0xFA, 0, 0, 0, 0, 0, 0, 0xD0, 0, 0]))))

    def test_aa_fa_sem_d0_nao_vale(self):
        self.assertIsNone(battery.parse_receiver(pad(bytes([0xAA, 0xFA, 0, 0, 0, 0, 0, 0, 0xD1, 55, 0]))))

    def test_eco_errado_ou_curto_ou_vazio(self):
        self.assertIsNone(battery.parse_receiver(pad(bytes([0xAA, 0x30, 0xA5, 0x0C, 0, 0, 0, 0, 80, 0]))))
        self.assertIsNone(battery.parse_receiver(pad(bytes([0x55, 0x30, 0xA5, 0x0B, 0, 0, 0, 0, 80, 0]))))
        self.assertIsNone(battery.parse_receiver(ECHO + b"\x50"))      # curto: sem o byte de carregando
        self.assertIsNone(battery.parse_receiver(b""))
        self.assertIsNone(battery.parse_receiver(None))


class PickTests(unittest.TestCase):
    def f(self, path, group, page, usage, n=65):
        return {"path": path, "group": group, "page": page, "usage": usage, "inlen": n, "outlen": n}

    def test_so_com_mouse_no_mesmo_dispositivo(self):
        ifs = [self.f("a", "g1", 0xFF01, 0x10), self.f("b", "g1", 0x01, 0x02), self.f("c", "g2", 0xFF01, 0x10), self.f("d", "g2", 0x01, 0x06)]
        self.assertEqual(battery.pick_targets(ifs), ["a"])

    def test_sem_mouse_nada_e_enviado(self):
        self.assertEqual(battery.pick_targets([self.f("a", "g1", 0xFF01, 0x10)]), [])
        self.assertEqual(battery.pick_targets([]), [])

    def test_tamanho_e_uso_exatos(self):
        ifs = [self.f("a", "g1", 0xFF01, 0x11), self.f("b", "g1", 0xFF01, 0x10, 64), self.f("m", "g1", 0x01, 0x02)]
        self.assertEqual(battery.pick_targets(ifs), [])

    def test_limite_de_aparelhos(self):
        ifs = []
        for g in ("g1", "g2", "g3"):
            ifs += [self.f("v" + g, g, 0xFF01, 0x10), self.f("m" + g, g, 0x01, 0x02)]
        self.assertEqual(len(battery.pick_targets(ifs)), battery.MAX_TARGETS)

    def test_pedido_e_o_unico_conhecido(self):
        self.assertEqual(len(battery.HID_REQUEST), 65)
        self.assertEqual(battery.HID_REQUEST[:8], bytes([0, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01]))
        self.assertEqual(set(battery.HID_REQUEST[8:]), {0})


class PollTests(unittest.TestCase):
    def mk(self, hid_rows, bt=()):
        t = [0.0]
        calls = [0]

        def hid_fn():
            calls[0] += 1
            return list(hid_rows[0])

        b = battery.Battery(lambda: list(bt), hid_fn=hid_fn, clock=lambda: t[0])
        return b, t, calls

    def row(self, pct, chg=False):
        return {"key": "receptor-0", "name": battery.RECEIVER_NAME, "pct": pct, "charging": chg, "kind": "mouse"}

    def test_item_do_mouse_e_cadencia_de_5_min(self):
        rows = [[self.row(80)]]
        b, t, calls = self.mk(rows)
        b.poll()
        self.assertEqual(b.state()["items"][0]["name"], "Mouse (receptor)")
        self.assertEqual((b.items[0]["kind"], b.items[0]["charging"], b.items[0]["pct"]), ("mouse", False, 80))
        t[0] = 60; b.poll(); t[0] = 299; b.poll()
        self.assertEqual(calls[0], 1)                      # polls de 1 min não releem o receptor
        t[0] = 300; b.poll()
        self.assertEqual(calls[0], 2)

    def test_mouse_dormindo_some_e_volta(self):
        rows = [[self.row(80)]]
        b, t, _ = self.mk(rows)
        b.poll()
        rows[0] = []
        t[0] = 300
        self.assertEqual(b.poll()[0], True)
        self.assertEqual(b.items, [])
        rows[0] = [self.row(79)]
        t[0] = 600
        b.poll()
        self.assertEqual(b.items[0]["pct"], 79)

    def test_valor_zero_ou_invalido_nao_aparece(self):
        b, _, _ = self.mk([[self.row(0), self.row(101)]])
        b.poll()
        self.assertEqual(b.items, [])

    def test_aviso_igual_aos_outros_abaixo_de_15(self):
        rows = [[self.row(14)]]
        b, t, _ = self.mk(rows)
        ch, al = b.poll()
        self.assertEqual([a["pct"] for a in al], [14])
        t[0] = 300
        self.assertEqual(b.poll()[1], [])                  # já avisado

    def test_carregando_nao_avisa(self):
        rows = [[self.row(10, True)]]
        b, t, _ = self.mk(rows)
        self.assertEqual(b.poll()[1], [])
        self.assertTrue(b.items[0]["charging"])
        rows[0] = [self.row(10, False)]                    # tirou o cabo ainda baixo: agora avisa
        t[0] = 300
        self.assertEqual(len(b.poll()[1]), 1)

    def test_id_opaco_e_bluetooth_intacto(self):
        b, _, _ = self.mk([[self.row(70)]], bt=[{"key": "AABBCCDDEEFF", "name": "JBL", "pct": 60}])
        b.poll()
        mouse = [i for i in b.items if i.get("kind")][0]
        bt = [i for i in b.items if not i.get("kind")][0]
        self.assertEqual(len(mouse["id"]), 12)
        self.assertNotIn("receptor", repr(b.state()).replace("Mouse (receptor)", ""))
        self.assertEqual(set(bt), {"id", "name", "pct"})

    def test_erro_do_receptor_nao_derruba_o_bluetooth(self):
        def boom():
            raise OSError("x")
        b = battery.Battery(lambda: [{"key": "A", "name": "Fone", "pct": 50}], hid_fn=boom, clock=lambda: 0.0)
        b.poll()
        self.assertEqual([i["name"] for i in b.items], ["Fone"])

    def test_sem_identificador_literal_no_modulo(self):
        import re
        src = Path(battery.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"vid_[0-9a-f]{4}|pid_[0-9a-f]{4}|ven_[0-9a-f]{4}", src, re.I))


if __name__ == "__main__":
    unittest.main()
