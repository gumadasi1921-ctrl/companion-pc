"""Build da página: a meta <meta name="build"> injetada no index.html servido e o ramo do app que a compara com o welcome.
Rodar: py -3 tests/test_web_build.py"""
import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "windows" / "server"
sys.path.insert(0, str(SRC))
import multipc  # noqa: E402

HTML = (SRC / "web" / "index.html").read_bytes()
APP = HTML.decode("utf-8")


class BuildMeta(unittest.TestCase):
    def test_injeta_uma_vez_no_head(self):
        out = multipc.with_build_meta(HTML, 1234567)
        self.assertEqual(out.count(b'<meta name="build" '), 1)
        self.assertIn(b'<head>\n<meta name="build" content="1234567">', out)
        self.assertEqual(multipc.with_build_meta(out, 99), out)

    def test_so_numero(self):
        out = multipc.with_build_meta(HTML, 42).decode("utf-8")
        m = re.search(r'<meta name="build" content="([^"]*)">', out)
        self.assertEqual(m.group(1), "42")
        self.assertEqual(len(out) - len(APP), len('\n<meta name="build" content="42">'))

    def test_sem_head_nao_muda(self):
        self.assertEqual(multipc.with_build_meta(b"<html></html>", 5), b"<html></html>")

    def test_app_usa_meta_e_limite_de_30s(self):
        self.assertIn('meta[name="build"]', APP)
        self.assertNotIn("pageBuild = pageBuild || m.web", APP)
        self.assertIn('pcc.reloadAt', APP)
        self.assertIn("30000", APP)


if __name__ == "__main__":
    unittest.main()
