"""Avisos Importantes (alerts.py): config, som gerado, fila (foco, reenvio, repetição). Rodar: py -3 tests/test_important.py"""
import io
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import alerts  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Cfg(unittest.TestCase):
    def test_default_marks_generic_and_linkcard(self):
        d = alerts.important_cfg(None)
        self.assertEqual(d["kinds"], ["misplaced_generic", "linkcard"])
        self.assertEqual(d["ignore_focus"], ["misplaced_generic"])      # "Ligar job ao card" espera a pausa

    def test_clean(self):
        ok = {"kinds": ["unident"], "ignore_focus": [], "sound": "bell", "volume": 0}
        self.assertEqual(alerts.clean_important(ok), ok)
        for bad in (None, {**ok, "kinds": ["nada"]}, {**ok, "sound": "x"}, {**ok, "volume": 101}, {**ok, "volume": True},
                    {**ok, "ignore_focus": "linkcard"}):
            with self.assertRaises(ValueError):
                alerts.clean_important(bad)
        self.assertEqual(alerts.important_cfg({**ok, "sound": "x"})["sound"], "chime")      # inválido volta ao padrão


class Wav(unittest.TestCase):
    def test_sounds_are_short_and_volume_scales(self):
        for n in alerts.IMPORTANT_SOUNDS:
            loud, soft = alerts.alert_wav(n, 100), alerts.alert_wav(n, 20)
            w = wave.open(io.BytesIO(loud))
            self.assertEqual((w.getnchannels(), w.getsampwidth()), (1, 2))
            self.assertTrue(0.4 < w.getnframes() / w.getframerate() < 1.6)
            pk = lambda b: max(abs(int.from_bytes(b[i:i + 2], "little", signed=True)) for i in range(44, len(b) - 1, 2))  # noqa: E731
            self.assertGreater(pk(loud), 3 * pk(soft))
            self.assertLess(pk(loud), 32767)
        self.assertEqual(max(abs(int.from_bytes(b, "little", signed=True)) for b in [alerts.alert_wav("chime", 0)[i:i + 2] for i in range(44, 200, 2)]), 0)


class Queue(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.q = alerts.ImportantQueue(self.c)

    def add(self, id="a:1", ign=False, ev=True):
        return self.q.add(id, "k", {"t": "event", "id": id} if ev else None, "T", "B", ign)

    def test_fires_once_then_repeats_once_after_15_min(self):
        self.add()
        self.assertEqual([a for a, _ in self.q.due(False)], ["fire"])
        self.q.mark(self.q.items["a:1"], "fire")
        self.assertEqual(self.q.due(False), [])
        self.c.t += alerts.REPEAT_S - 1
        self.assertEqual(self.q.due(False), [])
        self.c.t += 2
        act, it = self.q.due(False)[0]
        self.assertEqual(act, "repeat")
        self.q.mark(it, "repeat")
        self.c.t += 3600
        self.assertEqual(self.q.due(False), [])        # só 1 repetição

    def test_focus_holds_unless_ignoring_focus(self):
        self.add("a:1", ign=False)
        self.add("b:1", ign=True)
        self.assertEqual([it["id"] for _, it in self.q.due(True)], ["b:1"])
        self.assertEqual({it["id"] for _, it in self.q.due(False)}, {"a:1", "b:1"})   # acabou o foco: entrega o seguro

    def test_ack_stops_everything_and_ask_prefix(self):
        self.add("a:1")
        self.add("ask:xyz", ev=False)
        self.q.mark(self.q.items["a:1"], "fire")
        self.assertEqual(len(self.q.pending_events()), 1)
        self.q.ack("a:1")
        self.assertEqual(self.q.pending_events(), [])
        self.assertEqual(self.q.ask_info()[0], 0)
        self.q.ack(prefix="ask:")
        self.c.t += alerts.REPEAT_S + 5
        self.assertEqual(self.q.due(False), [])
        self.assertIsNone(self.q.ask_info())

    def test_pending_events_for_reconnect_carry_repeat_count(self):
        it = self.add()
        self.assertEqual(self.q.pending_events(), [])  # ainda não entregue (foco): nada a reenviar
        self.q.mark(it, "fire")
        self.assertEqual(self.q.pending_events()[0]["repeat"], 0)
        self.q.mark(it, "repeat")
        self.assertEqual(self.q.pending_events()[0]["repeat"], 1)

    def test_old_items_are_dropped(self):
        self.add()
        self.c.t += alerts.MAX_PENDING_S + 1
        self.assertEqual(self.q.due(False), [])
        self.assertEqual(self.q.items, {})


if __name__ == "__main__":
    unittest.main()
