"""Perfis de áudio (audio_profiles.py): validação, limites, gravação atômica, arquivo corrompido.
Rodar: py -3 tests/test_audio_profiles.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import audio_profiles as ap  # noqa: E402

SNAP = {"output": {"id": "dev1", "name": "Fones"}, "master": {"level": 40, "muted": False},
        "mic": {"level": 70, "muted": True}, "apps": {"spotify": {"name": "Spotify", "level": 30, "muted": False}}}


class T(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_save_list_get_persist(self):
        p = ap.AudioProfiles(self.base)
        lst = p.save("  Jogo  ", SNAP)
        self.assertEqual(lst[0]["name"], "Jogo")
        self.assertEqual((lst[0]["output"], lst[0]["apps"], lst[0]["mic"]), ("Fones", 1, True))
        self.assertNotIn("dev1", json.dumps(lst))       # a lista para o aparelho não leva ids de dispositivo
        again = ap.AudioProfiles(self.base)
        self.assertEqual(again.get(lst[0]["id"])["data"]["master"], {"level": 40, "muted": False})

    def test_validation_nothing_invented(self):
        with self.assertRaises(ValueError):
            ap.clean_snapshot({})
        with self.assertRaises(ValueError):
            ap.clean_snapshot({"master": {"level": "x"}})
        s = ap.clean_snapshot({"mic": {"muted": True}, "master": {"level": 250}})
        self.assertEqual(s["mic"], {"muted": True})        # sem volume do microfone: só o mudo, nada inventado
        self.assertEqual(s["master"]["level"], 100)
        self.assertNotIn("apps", s)

    def test_name_rules(self):
        p = ap.AudioProfiles(self.base)
        for bad in ("", "   ", "x" * 41):
            with self.assertRaises(ValueError):
                p.save(bad, SNAP)
        p.save("A", SNAP)
        with self.assertRaises(ValueError):
            p.save("a", SNAP)                               # nome repetido (sem diferenciar maiúsculas)

    def test_limit_and_delete(self):
        p = ap.AudioProfiles(self.base)
        for i in range(ap.MAX_PROFILES):
            p.save(f"p{i}", SNAP)
        with self.assertRaises(ValueError):
            p.save("extra", SNAP)
        pid = p.items[0]["id"]
        self.assertEqual(len(p.delete(pid)), ap.MAX_PROFILES - 1)
        with self.assertRaises(ValueError):
            p.get(pid)

    def test_overwrite_keeps_name(self):
        p = ap.AudioProfiles(self.base)
        pid = p.save("Meu", SNAP)[0]["id"]
        p.save("", {"master": {"level": 10}}, pid)
        self.assertEqual(p.get(pid)["name"], "Meu")
        self.assertEqual(p.get(pid)["data"], {"master": {"level": 10}})

    def test_corrupt_file_is_renamed(self):
        (self.base / "audio_profiles.json").write_text("{nao json", encoding="utf-8")
        p = ap.AudioProfiles(self.base)
        self.assertEqual(p.list(), [])
        self.assertTrue(list(self.base.glob("audio_profiles.json.bad-*")))


if __name__ == "__main__":
    unittest.main()
