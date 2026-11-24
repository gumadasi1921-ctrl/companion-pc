"""Backup diário (backup.py): cópia, retenção de 30 dias só dentro de Companion-backup, erro sem exceção, due().
Rodar: py -3 tests/test_backup.py"""
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import backup  # noqa: E402


class Clock:
    def __init__(self, dt):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t


class BackupTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        root = Path(self._d.name)
        self.base, self.dest = root / "srv", root / "dest"
        self.base.mkdir()
        self.dest.mkdir()
        for n in ("work-history.json", "work_rules.json", "work-notes.json", "tasks.json", "pomodoro.json", "deck.json"):
            (self.base / n).write_text(json.dumps({"n": n}), encoding="utf-8")
        (self.base / "config.json").write_text('{"token": "SEGREDO"}', encoding="utf-8")
        self.clk = Clock(datetime(2026, 11, 24, 10, 0, 0))
        self.b = backup.Backup(self.base, self.clk)

    def tearDown(self):
        self._d.cleanup()

    def test_copies_data_and_never_config(self):
        st = self.b.run(self.dest)
        self.assertTrue(st["ok"])
        day = self.dest / "Companion-backup" / "2026-11-24"
        names = sorted(p.name for p in day.iterdir())
        self.assertEqual(names, ["deck.json", "pomodoro.json", "tasks.json", "work-history.json", "work-notes.json", "work_rules.json"])
        self.assertEqual(st["files"], 6)
        self.assertEqual(json.loads((day / "tasks.json").read_text(encoding="utf-8")), {"n": "tasks.json"})
        self.assertNotIn("SEGREDO", "".join(p.read_text(encoding="utf-8") for p in day.iterdir()))
        self.assertFalse(list(day.glob("*.tmp")))

    def test_same_day_again_overwrites(self):
        self.b.run(self.dest)
        (self.base / "tasks.json").write_text('{"novo": 1}', encoding="utf-8")
        self.b.run(self.dest)
        self.assertEqual(json.loads((self.dest / "Companion-backup" / "2026-11-24" / "tasks.json").read_text(encoding="utf-8")), {"novo": 1})
        self.assertEqual(len(list((self.dest / "Companion-backup").iterdir())), 1)

    def test_retention_keeps_30_and_touches_only_dated_dirs(self):
        root = self.dest / "Companion-backup"
        for i in range(40):
            d = root / (datetime(2026, 10, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
            d.mkdir(parents=True)
            (d / "x.json").write_text("{}", encoding="utf-8")
        (root / "minhas-notas").mkdir()
        (root / "2026-10-05-extra").mkdir()
        (root / "leia-me.txt").write_text("fica", encoding="utf-8")
        outside = self.dest / "2020-01-01"
        outside.mkdir()
        st = self.b.run(self.dest)
        days = sorted(p.name for p in root.iterdir() if p.is_dir() and backup.DAY_RX.match(p.name))
        self.assertEqual(len(days), 30)
        self.assertEqual(days[-1], "2026-11-24")
        self.assertEqual(days[0], "2026-10-12")      # 40 antigas + hoje = 41: saem as 11 mais velhas
        self.assertEqual(st["kept"], 30)
        for keep in ("minhas-notas", "2026-10-05-extra", "leia-me.txt"):
            self.assertTrue((root / keep).exists(), keep)
        self.assertTrue(outside.exists())              # fora de Companion-backup: intocado

    def test_failure_does_not_raise_and_reports(self):
        bad = self.dest / "arquivo.txt"
        bad.write_text("sou um arquivo", encoding="utf-8")
        st = self.b.run(bad)                            # destino é um arquivo
        self.assertFalse(st["ok"])
        self.assertIn("error", st)
        self.assertEqual(self.b.status()["ok"], False)
        st = self.b.run("")                             # sem pasta
        self.assertFalse(st["ok"])
        st = self.b.run("relativa")
        self.assertFalse(st["ok"])
        self.assertNotIn(str(bad), json.dumps(self.b.status()))   # só o tipo do erro, nunca a mensagem/caminho de erro

    def test_due(self):
        self.assertFalse(self.b.due(""))
        self.assertTrue(self.b.due(self.dest))
        self.b.run(self.dest)
        self.assertFalse(self.b.due(self.dest))
        self.clk.t += 86400
        self.assertTrue(self.b.due(self.dest))
        self.assertTrue(self.b.due(self.dest / "outra"))   # trocou a pasta
        bad = Path(self._d.name) / "x.txt"
        bad.write_text("x", encoding="utf-8")
        self.b.run(bad)
        self.assertFalse(self.b.due(bad))                  # falhou agora: espera 1 h
        self.clk.t += backup.RETRY_S + 1
        self.assertTrue(self.b.due(bad))

    def test_missing_files_are_skipped(self):
        (self.base / "deck.json").unlink()
        (self.base / "pomodoro.json").unlink()
        st = self.b.run(self.dest)
        self.assertTrue(st["ok"])
        self.assertEqual(st["files"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=1)
