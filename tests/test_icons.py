"""Ícones dos apps (icons.py): extração real de exe do Windows, cache com limite, rota autenticada, ids opacos.
Rodar: py -3 tests/test_icons.py"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import icons  # noqa: E402

NOTEPAD = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
TOKEN = "token-de-teste"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class T(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.dir = Path(self._d.name)
        self.calls = []
        self.clock = Clock()
        self.ic = icons.Icons(self.dir / "cache", lambda: TOKEN, extractor=self.fake, clock=self.clock)
        self.exe = self.dir / "App.exe"
        self.exe.write_bytes(b"MZ")

    def tearDown(self):
        self._d.cleanup()

    def fake(self, path):
        self.calls.append(path)
        return None if "semicone" in path.lower() else PNG

    def url(self, i, k=None):
        return f"/icon/{i}?k={k or self.ic.key()}"

    def test_real_extraction(self):
        if not os.path.exists(NOTEPAD):
            self.skipTest("sem notepad")
        png = icons.extract_png(NOTEPAD)
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        self.assertEqual(im.size, (64, 64))
        self.assertGreater(im.convert("RGBA").getchannel("A").getextrema()[1], 0)
        self.assertIsNone(icons.extract_png(NOTEPAD + ".nao-existe"))

    def test_id_is_opaque(self):
        i = self.ic.register(str(self.exe))
        self.assertRegex(i, r"^[0-9a-f]{12}$")
        self.assertEqual(i, self.ic.register(str(self.exe).upper()))     # caminho sem diferenciar maiúsculas (Windows)
        self.assertIsNone(self.ic.register("texto.txt"))
        self.assertIsNone(self.ic.register(None))
        self.assertIsNone(self.ic.register("a" * 600 + ".exe"))

    def test_route_auth_and_errors(self):
        i = self.ic.register(str(self.exe))
        self.assertEqual(self.ic.handle(f"/icon/{i}")[0], 403)                      # sem chave
        self.assertEqual(self.ic.handle(self.url(i, "errada"))[0], 403)
        self.assertEqual(self.ic.handle(f"/icon/{i}?k={self.ic.key()}&k=x")[0], 403)  # chave repetida
        self.assertEqual(self.ic.handle(self.url("..%2f..%2fx"))[0], 404)          # só id hexadecimal
        self.assertEqual(self.ic.handle(self.url("a" * 12))[0], 404)               # id desconhecido
        self.assertEqual(self.ic.handle(f"/icon/{i}/extra?k={self.ic.key()}")[0], 404)
        st, ctype, body, hdr = self.ic.handle(self.url(i))
        self.assertEqual((st, ctype, body), (200, "image/png", PNG))
        self.assertIn("private", hdr["Cache-Control"])

    def test_cache_one_file_and_invalidation(self):
        i = self.ic.register(str(self.exe))
        self.ic.handle(self.url(i))
        self.ic.handle(self.url(i))
        self.assertEqual(len(self.calls), 1)                                        # segunda vez veio do disco
        self.assertEqual(len(list((self.dir / "cache").glob("*.png"))), 1)
        self.exe.write_bytes(b"MZ-novo-mais-longo")                                 # o exe mudou
        self.ic.handle(self.url(i))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(list((self.dir / "cache").glob("*.png"))), 1)          # continua um por exe

    def test_served_from_disk_after_restart(self):
        i = self.ic.register(str(self.exe))
        self.ic.handle(self.url(i))
        again = icons.Icons(self.dir / "cache", lambda: TOKEN, extractor=self.fake)
        self.assertEqual(again.handle(f"/icon/{i}?k={again.key()}")[0], 200)        # id ainda não registrado, mas está no disco

    def test_missing_exe_and_negative_cache(self):
        bad = self.dir / "SemIcone.exe"
        bad.write_bytes(b"MZ")
        i = self.ic.register(str(bad))
        self.assertEqual(self.ic.handle(self.url(i))[0], 404)
        self.assertEqual(self.ic.handle(self.url(i))[0], 404)
        self.assertEqual(len(self.calls), 1)                                        # não insiste por 10 min
        self.clock.t += icons.NEG_TTL_S + 1
        self.ic.handle(self.url(i))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(list((self.dir / "cache").glob("*.png")), [])              # falha nunca vai para o disco
        ghost = self.ic.register(str(self.dir / "Sumiu.exe"))
        self.assertEqual(self.ic.handle(self.url(ghost))[0], 404)

    def test_cache_limit_evicts_oldest(self):
        ic = icons.Icons(self.dir / "c2", lambda: TOKEN, extractor=self.fake, clock=self.clock, max_files=3)
        ids = []
        for n in range(5):
            f = self.dir / f"A{n}.exe"
            f.write_bytes(b"MZ")
            i = ic.register(str(f))
            ids.append(i)
            ic.handle(f"/icon/{i}?k={ic.key()}")
            for g in (self.dir / "c2").glob(f"{i}-*.png"):
                os.utime(g, (1000 + n, 1000 + n))
        names = sorted(p.name.split("-")[0] for p in (self.dir / "c2").glob("*.png"))
        self.assertEqual(len(names), 3)
        self.assertEqual(names, sorted(ids[2:]))                                    # saíram os mais antigos
        small = icons.Icons(self.dir / "c3", lambda: TOKEN, extractor=lambda p: b"\x89PNG\r\n\x1a\n" + b"y" * 100, max_bytes=250)
        for n in range(4):
            f = self.dir / f"B{n}.exe"
            f.write_bytes(b"MZ")
            small.handle(f"/icon/{small.register(str(f))}?k={small.key()}")
        self.assertLessEqual(sum(p.stat().st_size for p in (self.dir / "c3").glob("*.png")), 250)

    def test_no_path_in_response_or_cache_names(self):
        i = self.ic.register(str(self.exe))
        st, _, body, hdr = self.ic.handle(self.url(i))
        blob = repr((body, hdr)) + " ".join(p.name for p in (self.dir / "cache").glob("*"))
        self.assertNotIn(self.exe.name.lower(), blob.lower())
        self.assertNotIn(os.environ.get("USERNAME", "zzzz").lower(), blob.lower())


if __name__ == "__main__":
    unittest.main()
