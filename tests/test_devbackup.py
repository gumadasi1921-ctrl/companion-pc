"""Backup do estado do app por aparelho (devbackup.py). py -3 tests/test_devbackup.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import devbackup as dbk  # noqa: E402

A = "a" * 32
B = "b" * 32


class Clock:
    t = 1_700_000_000.0

    def __call__(self):
        Clock.t += 10
        return Clock.t


class DevBackupTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.bk = dbk.DeviceBackups(self.base, clock=Clock())

    def tearDown(self):
        self._d.cleanup()

    def test_save_and_get(self):
        r = self.bk.save(A, "iPhone", {"theme": "modern", "home": {"v": 3}, "token": "SEGREDO", "pcs": {"x": 1}})
        self.assertTrue(r["saved"])
        g = self.bk.get(A)
        self.assertEqual(g["name"], "iPhone")
        self.assertEqual(g["data"], {"theme": "modern", "home": {"v": 3}})   # chaves fora da lista nunca entram
        self.assertNotIn("SEGREDO", "".join(p.read_text(encoding="utf-8") for p in self.base.rglob("*.json")))

    def test_same_data_no_new_version_and_five_kept(self):
        self.bk.save(A, "i", {"theme": "x"})
        self.assertFalse(self.bk.save(A, "i", {"theme": "x"})["saved"])
        self.assertEqual(len(self.bk.own(A)), 1)
        for i in range(8):
            self.bk.save(A, "i", {"theme": f"t{i}"})
        v = self.bk.own(A)
        self.assertEqual(len(v), 5)
        self.assertEqual(self.bk.get(A)["data"]["theme"], "t7")
        self.assertEqual(self.bk.get(A, at=v[4]["at"])["data"]["theme"], "t3")   # versão antiga pela data
        self.assertGreater(v[0]["at"], v[1]["at"])

    def test_empty_never_overwrites(self):
        self.bk.save(A, "i", {"theme": "x"})
        with self.assertRaises(dbk.BackupError):
            self.bk.save(A, "i", {})
        with self.assertRaises(dbk.BackupError):
            self.bk.save(A, "i", {"token": "t", "outra": 1})
        self.assertEqual(self.bk.get(A)["data"], {"theme": "x"})

    def test_size_limit_and_bad_id(self):
        with self.assertRaises(dbk.BackupError):
            self.bk.save(A, "i", {"mascot": "x" * (dbk.MAX_BYTES + 10)})
        for bad in ("", "abc", "../" + "a" * 29, None, "A" * 32):
            with self.assertRaises(dbk.BackupError):
                self.bk.save(bad, "i", {"theme": "x"})
        self.assertEqual(self.bk.list_all(), [])

    def test_wrong_device(self):
        self.bk.save(A, "iPhone", {"theme": "a"})
        with self.assertRaises(dbk.BackupError):
            self.bk.get(B)                       # outro id não vê o backup de A
        self.assertEqual(self.bk.own(B), [])
        self.bk.save(B, "iPad", {"theme": "b"})
        self.assertEqual(self.bk.get(A)["data"]["theme"], "a")
        o = self.bk.others(B)                    # B vê A só por nome/ref, nunca pelo id
        self.assertEqual([x["name"] for x in o], ["iPhone"])
        self.assertNotIn(A, json.dumps(o))
        self.assertEqual(self.bk.get(ref=o[0]["ref"])["data"]["theme"], "a")
        with self.assertRaises(dbk.BackupError):
            self.bk.get(ref="0" * 12)
        with self.assertRaises(dbk.BackupError):
            self.bk.get(ref="../..")
        self.bk.save(B, "iPad", {"theme": "b2"})
        self.assertEqual(self.bk.get(A)["data"]["theme"], "a")   # gravar em B nunca toca em A

    def test_panel_list_delete(self):
        self.bk.save(A, "iPhone", {"theme": "a"})
        self.bk.save(A, "iPhone", {"theme": "a2"})
        self.bk.save(B, "iPad", {"theme": "b"})
        lst = self.bk.list_all()
        self.assertEqual({x["name"] for x in lst}, {"iPhone", "iPad"})
        ref = next(x for x in lst if x["name"] == "iPhone")
        self.assertEqual(self.bk.delete(ref["ref"], ref["versions"][0]["at"]), 1)
        self.assertEqual(len(self.bk.own(A)), 1)
        self.assertEqual(self.bk.delete(ref["ref"]), 1)
        self.assertEqual(self.bk.own(A), [])
        self.assertEqual(len(self.bk.list_all()), 1)

    def test_name_clean_and_corrupt_file(self):
        self.bk.save(A, "  " + "x" * 80 + "\n", {"theme": "a"})
        self.assertEqual(len(self.bk.get(A)["name"]), 40)
        for p in self.base.rglob("v-*.json"):
            p.write_text("{quebrado", encoding="utf-8")
        with self.assertRaises(dbk.BackupError):
            self.bk.get(A)
        self.assertEqual(self.bk.list_all(), [])

    def test_atomic_no_tmp_left(self):
        self.bk.save(A, "i", {"theme": "a"})
        self.assertEqual([p for p in self.base.rglob("*.tmp")], [])


if __name__ == "__main__":
    unittest.main()
