"""Testes de exportação concluída, arquivo fora do lugar, páginas automáticas do deck e modo foco.
Rodar: py -3 tests/test_notices.py   (o teste "real" copia um arquivo grande devagar e leva ~15 s)"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import alerts  # noqa: E402
import extras  # noqa: E402
import pomodoro  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def touch(p, size=10):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_bytes(b"x" * size)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.dir = Path(self._d.name)
        self.c = Clock()
        self.w = extras.FolderWatcher(lambda: [str(self.dir)], exts=extras.EXPORT_EXT, depth=2, stable_s=5,
                                      check_open=True, clock=self.c)

    def tearDown(self):
        self._d.cleanup()

    def names(self, r):
        return sorted(f["name"] for f in r)

    def test_old_files_ignored(self):
        touch(self.dir / "velho.mp4")
        self.assertEqual(self.w.poll(), [])
        self.c.t += 60
        self.assertEqual(self.w.poll(), [])

    def test_growing_file_only_when_stable(self):
        self.w.poll()
        f = self.dir / "final.mp4"
        touch(f, 100)
        self.assertEqual(self.w.poll(), [])           # primeira vez
        for i in range(4):                             # cresce a cada 3 s: nunca fica 5 s igual
            self.c.t += 3
            touch(f, 200 + i * 100)
            self.assertEqual(self.w.poll(), [])
        self.c.t += 3
        self.assertEqual(self.w.poll(), [])           # estável só há 3 s
        self.c.t += 3
        r = self.w.poll()                              # 6 s estável
        self.assertEqual(self.names(r), ["final.mp4"])
        self.c.t += 30
        self.assertEqual(self.w.poll(), [])           # só uma vez

    def test_temp_and_other_ext_ignored(self):
        self.w.poll()
        for n in ["a.mp4.tmp", "b.part", "~c.mp4", "d.crdownload", "e.txt", "f.tmp.mp4", ".g.mp4", "render.temp.mov"]:
            touch(self.dir / n)
        self.w.poll()
        self.c.t += 10
        self.assertEqual(self.w.poll(), [])
        touch(self.dir / "ok.PNG")
        self.w.poll()
        self.c.t += 10
        self.assertEqual(self.names(self.w.poll()), ["ok.PNG"])

    def test_subfolder_depth(self):
        self.w.poll()
        touch(self.dir / "a" / "b" / "ok.mp4")
        touch(self.dir / "a" / "b" / "c" / "fundo.mp4")   # 3 níveis: fora
        self.w.poll()
        self.c.t += 10
        self.assertEqual(self.names(self.w.poll()), ["ok.mp4"])

    def test_entry_limit(self):
        w = extras.FolderWatcher(lambda: [str(self.dir)], exts=extras.EXPORT_EXT, max_entries=20, stable_s=0, clock=self.c)
        for i in range(30):
            touch(self.dir / f"f{i:02d}.txt")
        w.poll()
        self.assertTrue(w.truncated)
        touch(self.dir / "zz.mp4")
        w.poll()
        self.c.t += 1
        w.poll()
        self.assertTrue(w.truncated)   # não varre além do limite (e não quebra)

    def test_new_folder_primed_not_announced(self):
        self._o = tempfile.TemporaryDirectory()
        self.addCleanup(self._o.cleanup)
        other = Path(self._o.name)
        touch(other / "ja.mp4")
        folders = [str(self.dir)]
        w = extras.FolderWatcher(lambda: folders, exts=extras.EXPORT_EXT, depth=2, stable_s=0, clock=self.c)
        w.poll()
        folders.append(str(other))
        w.poll()
        self.c.t += 1
        self.assertEqual(w.poll(), [])
        touch(other / "novo.mp4")
        w.poll()
        self.c.t += 1
        self.assertEqual(self.names(w.poll()), ["novo.mp4"])

    def test_overlapping_folders_announce_once(self):
        sub = self.dir / "sub"
        sub.mkdir()
        w = extras.FolderWatcher(lambda: [str(self.dir), str(sub)], exts=extras.EXPORT_EXT, depth=2, stable_s=0, clock=self.c)
        w.poll()
        touch(sub / "x.mp4")
        out = []
        for _ in range(4):
            self.c.t += 1
            out += w.poll()
        self.assertEqual(self.names(out), ["x.mp4"])

    def test_downloads_behaviour_kept(self):
        w = extras.FolderWatcher(lambda: [str(self.dir)], clock=self.c)   # como o vigia de Downloads
        w.poll()
        touch(self.dir / "x.bin", 5)
        self.assertEqual(w.poll(), [])
        self.assertEqual(self.names(w.poll()), ["x.bin"])

    def test_real_slow_copy(self):
        real = extras.FolderWatcher(lambda: [str(self.dir)], exts=extras.EXPORT_EXT, depth=2, stable_s=5, check_open=True)
        real.poll()
        target = self.dir / "grande.mp4"

        def copy():
            with open(target, "wb") as f:
                for _ in range(8):
                    f.write(b"0" * 1_000_000)
                    f.flush()
                    time.sleep(1)
        t = threading.Thread(target=copy)
        t.start()
        got, during = [], []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20:
            time.sleep(1)
            r = real.poll()
            (during if t.is_alive() else got).extend(r)
            if got:
                break
        t.join()
        self.assertEqual(during, [], "avisou com o arquivo ainda sendo copiado")
        self.assertEqual(self.names(got), ["grande.mp4"])
        self.assertEqual(got[0]["size"], 8_000_000)


def info(app="Photoshop", f="Sem título-1", ident=False, ignored=False, counting=True):
    return {"app": app, "file": f, "identified": ident, "ignored": ignored, "counting": counting}


class MisplacedTests(unittest.TestCase):
    T0 = datetime(2026, 11, 24, 10, 0, 0).timestamp()

    def run_for(self, m, inf, secs, roots=True, minutes=10, t0=None):
        t, out = self.T0 if t0 is None else t0, []
        for _ in range(int(secs // 2) + 1):
            a = m.observe(inf, t, roots, minutes)
            if a:
                out.append(a)
            t += 2
        return out

    def test_generic_after_n_minutes_once(self):
        m = alerts.Misplaced()
        self.assertEqual(self.run_for(m, info(), 9 * 60), [])
        out = self.run_for(m, info(), 5 * 60, t0=self.T0 + 9 * 60 + 2)
        self.assertEqual(len(out), 1)
        self.assertIn("sem salvar", out[0]["body"])
        self.assertIn("'Sem título-1'", out[0]["body"])
        self.assertEqual(out[0]["kind"], "generic")
        self.assertEqual(self.run_for(m, info(), 20 * 60, t0=self.T0 + 30 * 60), [])   # só uma vez por dia

    def test_unidentified(self):
        m = alerts.Misplaced()
        out = self.run_for(m, info("Illustrator", "logo.ai"), 11 * 60)
        self.assertEqual([a["kind"] for a in out], ["outside"])
        self.assertEqual(out[0]["body"], "'logo.ai' está fora das pastas de clientes.")

    def test_identified_or_ignored_never(self):
        m = alerts.Misplaced()
        self.assertEqual(self.run_for(m, info(f="a.psd", ident=True), 30 * 60), [])
        self.assertEqual(self.run_for(m, info(f="b.psd", ignored=True), 30 * 60), [])
        self.assertEqual(self.run_for(m, info(f="Sem título-2", ident=True), 30 * 60), [])   # regra pontual

    def test_no_roots_single_notice_per_day(self):
        m = alerts.Misplaced()
        out = self.run_for(m, info(f="a.psd"), 11 * 60, roots=False)
        out += self.run_for(m, info(f="b.psd"), 11 * 60, roots=False, t0=self.T0 + 12 * 60)
        out += self.run_for(m, info(f="Sem título-3"), 11 * 60, roots=False, t0=self.T0 + 24 * 60)
        self.assertEqual([a["kind"] for a in out], ["setup"])
        self.assertIn("pastas de clientes", out[0]["body"])

    def test_new_day_again_and_off(self):
        m = alerts.Misplaced()
        self.assertEqual(len(self.run_for(m, info(), 11 * 60)), 1)
        self.assertEqual(len(self.run_for(m, info(), 11 * 60, t0=self.T0 + 86400)), 1)
        self.assertEqual(self.run_for(alerts.Misplaced(), info(), 60 * 60, minutes=0), [])

    def test_switching_file_resets_and_idle_pauses(self):
        m = alerts.Misplaced()
        t = self.T0
        for i in range(250):                       # 8 min em "a", troca para "b" e volta: zera
            m.observe(info(f="a.psd"), t, True, 10)
            t += 2
        for i in range(10):
            m.observe(info(f="b.psd"), t, True, 10)
            t += 2
        out = []
        for i in range(250):
            a = m.observe(info(f="a.psd"), t, True, 10)
            out += [a] if a else []
            t += 2
        self.assertEqual(out, [])
        m2 = alerts.Misplaced()                    # parado (counting False) não soma
        self.assertEqual(self.run_for(m2, info(counting=False), 30 * 60), [])

    def test_valid_options(self):
        for v in (0, 5, 10, 20):
            self.assertEqual(alerts.valid_misplaced_min(v), v)
        for v in (7, -1, True, "10", None):
            with self.assertRaises(ValueError):
                alerts.valid_misplaced_min(v)


class DeckTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.deck = extras.Deck(self.base)
        self.user = {"pages": [{"name": "Meu", "buttons": [{"label": "Oi", "type": "keys", "value": "ctrl+a", "icon": "A"}]}]}

    def tearDown(self):
        self._d.cleanup()

    def write(self, d):
        (self.base / "deck.json").write_text(json.dumps(d), encoding="utf-8")

    def baks(self):
        return list(self.base.glob("deck.json.bak-*"))

    def test_adds_illustrator_only_and_keeps_existing(self):
        self.write(self.user)
        d = self.deck.get()
        self.assertEqual(d["pages"][0], self.user["pages"][0])
        self.assertEqual([p["name"] for p in d["pages"]], ["Meu", "Illustrator"])
        self.assertEqual(d["pages"][1]["apps"], ["illustrator.exe"])
        self.assertEqual(len(d["pages"][1]["buttons"]), 15)
        self.assertEqual(len(self.baks()), 1)
        self.assertEqual(json.loads(self.baks()[0].read_text(encoding="utf-8")), self.user)   # backup = original
        self.assertEqual(self.deck.get(), d)
        self.assertEqual(len(self.deck.get()["pages"]), 2)
        self.assertEqual(len(self.baks()), 1)

    def test_not_added_when_page_already_linked(self):
        self.user["pages"].append({"name": "AI", "apps": ["Illustrator"], "buttons": []})
        self.write(self.user)
        self.assertEqual([p["name"] for p in self.deck.get()["pages"]], ["Meu", "AI"])

    def test_deleted_page_does_not_come_back(self):
        self.write(self.user)
        d = self.deck.get()
        d["pages"] = d["pages"][:1]
        self.deck.save(d, lambda c: None)
        self.assertEqual([p["name"] for p in self.deck.get()["pages"]], ["Meu"])

    def test_no_capcut_page_without_confirmed_shortcuts(self):
        self.write(self.user)
        self.assertFalse(any("capcut.exe" in p.get("apps", []) for p in self.deck.get()["pages"]))

    def test_buttons_use_known_icons_and_keys(self):
        for b in extras.ILLUSTRATOR_PAGE["buttons"]:
            self.assertIn(b["svg"], extras.SVG_ICONS)
            self.assertRegex(b["color"], extras.HEX)
        d = self.deck.get()   # sem deck.json: o padrão já traz a página
        self.assertIn("Illustrator", [p["name"] for p in d["pages"]])


class FocusTests(unittest.TestCase):
    def test_holds_only_during_focus_and_summarizes(self):
        f = alerts.FocusHold()
        self.assertFalse(f.should_hold("WhatsApp", []))
        self.assertIsNone(f.set_active(True))
        for app in ["WhatsApp", "WhatsApp", "Outlook", "WhatsApp"]:
            self.assertTrue(f.should_hold(app, []))
            f.hold({"app": app, "title": "t", "body": "b"})
        self.assertEqual(f.n, 4)
        s = f.set_active(False)
        self.assertEqual(s["text"], "Durante o foco: 3 do WhatsApp, 1 do Outlook")
        self.assertEqual(len(s["items"]), 4)
        self.assertEqual(f.n, 0)
        self.assertIsNone(f.set_active(False))
        self.assertFalse(f.should_hold("WhatsApp", []))

    def test_exceptions_pass(self):
        f = alerts.FocusHold()
        f.set_active(True)
        self.assertFalse(f.should_hold("whatsapp", ["WhatsApp"]))
        self.assertTrue(f.should_hold("Outlook", ["WhatsApp"]))

    def test_nothing_held_no_summary_and_new_focus_resets(self):
        f = alerts.FocusHold()
        f.set_active(True)
        self.assertIsNone(f.set_active(False))
        f.set_active(True)
        f.hold({"app": "A"})
        f.set_active(True)   # continua o mesmo foco: não zera
        self.assertEqual(f.n, 1)

    def test_item_cap_keeps_counting(self):
        f = alerts.FocusHold()
        f.set_active(True)
        for _ in range(80):
            f.hold({"app": "A"})
        s = f.take()
        self.assertEqual((s["total"], len(s["items"])), (80, 50))

    def test_pomodoro_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = pomodoro.Pomodoro(Path(d))
            self.assertTrue(p.get()["config"]["focus_mode"])
            p.set_config({"focus_mode": False, "focus_except": ["Teams", "teams", " Slack "]})
            q = pomodoro.Pomodoro(Path(d))
            self.assertEqual((q.cfg["focus_mode"], q.cfg["focus_except"]), (False, ["Teams", "Slack"]))
            for bad in ({"focus_mode": "sim"}, {"focus_except": "x"}, {"focus_except": [1]}, {"focus_except": ["a"] * 21 + ["b"]}):
                with self.assertRaises(ValueError):
                    p.set_config(bad)

    def test_old_pomodoro_json_without_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = pomodoro.Pomodoro(Path(d))
            p.set_config({"focus_min": 30})
            path = Path(d) / "pomodoro.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cfg"].pop("focus_mode", None)
            data["cfg"].pop("focus_except", None)
            path.write_text(json.dumps(data), encoding="utf-8")
            q = pomodoro.Pomodoro(Path(d))
            self.assertEqual((q.cfg["focus_min"], q.cfg["focus_mode"], q.cfg["focus_except"]), (30, True, []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
