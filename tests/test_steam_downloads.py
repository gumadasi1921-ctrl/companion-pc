"""Downloads da Steam (steam_dl.py) com manifestos .acf de mentira numa pasta temporária, sem Steam e sem rede.
Rodar: py -3 tests/test_steam_downloads.py"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import steam_dl  # noqa: E402

TPL = '''"AppState"
{
\t"appid"\t\t"%(appid)s"
\t"name"\t\t"%(name)s"
\t"StateFlags"\t\t"%(flags)s"
\t"BytesToDownload"\t\t"%(to_dl)s"
\t"BytesDownloaded"\t\t"%(dl)s"
\t"BytesToStage"\t\t"%(to_stage)s"
\t"BytesStaged"\t\t"%(staged)s"
}
'''


def acf(appid="100", name="Jogo A", flags=1026, to_dl=1000, dl=250, to_stage=0, staged=0):
    return TPL % dict(appid=appid, name=name, flags=flags, to_dl=to_dl, dl=dl, to_stage=to_stage, staged=staged)


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


class T(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = Path(self._d.name)
        (self.root / "steamapps").mkdir()
        self.clock = Clock()
        self.s = steam_dl.SteamDownloads(lambda: str(self.root), self.clock)

    def tearDown(self):
        self._d.cleanup()

    def put(self, appid, **kw):
        (self.root / "steamapps" / f"appmanifest_{appid}.acf").write_text(acf(appid=appid, **kw), encoding="utf-8")

    def test_progress_is_bytes_ratio(self):
        self.put("100", dl=250)
        changed, done = self.s.poll()
        it = self.s.state()["items"][0]
        self.assertTrue(changed)
        self.assertEqual(done, [])
        self.assertEqual((it["name"], it["pct"], it["done"], it["total"], it["phase"]), ("Jogo A", 25.0, 250, 1000, "download"))
        self.assertNotIn("speed", it)                               # nada de velocidade/tempo restante chutado

    def test_unchanged_poll_is_not_a_change(self):
        self.put("100")
        self.s.poll()
        self.assertFalse(self.s.poll()[0])
        self.put("100", dl=300)
        self.assertTrue(self.s.poll()[0])

    def test_only_started_or_paused_updates_listed(self):
        self.put("1", flags=2)                                     # só na fila: não aparece
        self.put("2", flags=4, to_dl=0, dl=0)                      # instalado
        self.put("3", flags=1026, to_dl=0, dl=0)                   # atualizando mas sem bytes a baixar
        self.put("4", flags=2 | 512, name="Pausado")
        self.put("5", flags=2 | 256, name="Rodando")
        self.s.poll()
        got = {i["appid"]: i["phase"] for i in self.s.state()["items"]}
        self.assertEqual(got, {"4": "paused", "5": "download"})

    def test_stage_phase(self):
        self.put("100", flags=1026, to_dl=1000, dl=1000, to_stage=4000, staged=1000)
        self.s.poll()
        it = self.s.state()["items"][0]
        self.assertEqual((it["phase"], it["pct"]), ("stage", 25.0))

    def test_done_notice_once_after_seen_downloading(self):
        self.put("100", dl=500)
        self.s.poll()
        self.put("100", flags=4, to_dl=0, dl=0)
        changed, done = self.s.poll()
        self.assertTrue(changed)
        self.assertEqual(done, [{"appid": "100", "name": "Jogo A"}])
        self.assertEqual(self.s.state()["items"], [])
        self.assertEqual(self.s.poll()[1], [])                     # só uma vez

    def test_no_notice_for_game_never_seen_downloading(self):
        self.put("100", flags=4, to_dl=0, dl=0)
        self.assertEqual(self.s.poll()[1], [])

    def test_no_notice_when_manifest_vanishes_or_is_old(self):
        self.put("100", dl=500)
        self.s.poll()
        (self.root / "steamapps" / "appmanifest_100.acf").unlink()
        self.assertEqual(self.s.poll()[1], [])
        self.put("200", dl=500)
        self.s.poll()
        self.clock.t += steam_dl.SEEN_MAX_AGE_S + 1
        self.put("200", flags=4, to_dl=0, dl=0)
        self.assertEqual(self.s.poll()[1], [])                     # visto há tempo demais: aviso velho não vale

    def test_unavailable_when_no_steam(self):
        s = steam_dl.SteamDownloads(lambda: None, self.clock)
        self.assertFalse(s.polled)
        s.poll()
        self.assertTrue(s.polled)
        self.assertEqual(s.state(), {"available": False, "items": []})
        s2 = steam_dl.SteamDownloads(lambda: str(self.root / "nao-existe"), self.clock)
        s2.poll()
        self.assertFalse(s2.state()["available"])

    def test_garbage_manifest_is_ignored(self):
        (self.root / "steamapps" / "appmanifest_9.acf").write_text("lixo { ", encoding="utf-8")
        self.put("100")
        self.s.poll()
        self.assertEqual([i["appid"] for i in self.s.state()["items"]], ["100"])
        self.assertIsNone(steam_dl.read_manifest('"AppState"\n{\n"appid" "x"\n}'))

    def test_second_library(self):
        lib2 = self.root / "lib2"
        (lib2 / "steamapps").mkdir(parents=True)
        (self.root / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n"0"\n{\n"path" "%s"\n}\n}\n' % str(lib2).replace("\\", "\\\\"), encoding="utf-8")
        (lib2 / "steamapps" / "appmanifest_7.acf").write_text(acf(appid="7", name="Outro"), encoding="utf-8")
        self.s.poll()
        self.assertEqual([i["name"] for i in self.s.state()["items"]], ["Outro"])


if __name__ == "__main__":
    unittest.main()
