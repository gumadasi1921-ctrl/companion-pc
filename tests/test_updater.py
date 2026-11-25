"""Atualizar com um clique (updater.py): casos de erro e caminho feliz, com git/PowerShell falsos (nada de rede).
Rodar: py -3 tests/test_updater.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import updater  # noqa: E402


class Fake:
    """run() falso: responde por prefixo do comando git; guarda as chamadas."""

    def __init__(self, **answers):
        self.a = {"rev-parse --is-inside-work-tree": (0, "true"), "status": (0, ""), "fetch": (0, ""),
                  "rev-list": (0, "0\t0"), "rev-parse --short=12 HEAD": (0, "aaaaaaaaaaaa"), "pull": (0, "ok"),
                  "log -1": (0, "assunto novo"), "log --format": (0, ""), "powershell": (0, "")}
        self.a.update(answers)
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append(argv)
        key = "powershell" if argv[0] == "powershell" else " ".join(argv[3:])
        for k, v in self.a.items():
            if key.startswith(k):
                return v
        return 1, "?"


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        root = Path(self._d.name)
        self.repo = root / "repo"
        (self.repo / "windows").mkdir(parents=True)
        (self.repo / "windows" / "install-server.ps1").write_text("x", encoding="utf-8")
        self.base = root / "server"
        (self.base / "web").mkdir(parents=True)
        (self.base / "a.py").write_text("v1", encoding="utf-8")
        (self.base / "web" / "index.html").write_text("h1", encoding="utf-8")

    def tearDown(self):
        self._d.cleanup()

    def mk(self, fake, repo=True, installed=True):
        return updater.Updater(self.base, lambda: str(self.repo) if repo else "", "1.6", run=fake,
                               installed_dir=self.base if installed else self.base.parent / "outro")

    def err(self, fn):
        with self.assertRaises(updater.UpdateError) as c:
            fn()
        return str(c.exception)

    def test_sem_repo(self):
        self.assertIn("Escolha a pasta", self.err(self.mk(Fake(), repo=False).check))

    def test_nao_e_git(self):
        self.assertIn("não é um repositório", self.err(self.mk(Fake(**{"rev-parse --is-inside-work-tree": (128, "fatal")})).check))

    def test_mudancas_locais_param_sem_fetch(self):
        f = Fake(status=(0, " M windows/server/work.py"))
        self.assertIn("mudanças locais", self.err(self.mk(f).check))
        self.assertFalse(any(c[3] == "fetch" for c in f.calls))

    def test_fetch_falha(self):
        self.assertIn("Não consegui buscar", self.err(self.mk(Fake(fetch=(1, "no network"))).check))

    def test_sem_upstream(self):
        self.assertIn("upstream", self.err(self.mk(Fake(**{"rev-list": (128, "no upstream")})).check))

    def test_divergencia_para(self):
        f = Fake(**{"rev-list": (0, "2\t3")})
        self.assertIn("divergiu", self.err(self.mk(f).check))
        self.assertFalse(any(c[3] in ("pull", "merge", "rebase") for c in f.calls))

    def test_em_dia_e_a_frente(self):
        self.assertEqual(self.mk(Fake()).check()["state"], "uptodate")
        self.assertEqual(self.mk(Fake(**{"rev-list": (0, "2\t0")})).check()["state"], "ahead")

    def test_lista_commits_novos(self):
        f = Fake(**{"rev-list": (0, "0\t2"), "log --format": (0, "abc1234\tprimeiro\ndef5678\tsegundo")})
        r = self.mk(f).check()
        self.assertEqual((r["state"], r["behind"]), ("behind", 2))
        self.assertEqual([c["hash"] for c in r["commits"]], ["abc1234", "def5678"])

    def test_apply_so_na_pasta_instalada(self):
        f = Fake(**{"rev-list": (0, "0\t1")})
        self.assertIn("não está na pasta instalada", self.err(self.mk(f, installed=False).apply))
        self.assertFalse(any(c[3] == "pull" for c in f.calls if c[0] == "git"))

    def test_apply_ok_grava_versao_e_pede_reinicio(self):
        f = Fake(**{"rev-list": (0, "0\t1"), "rev-parse --short=12 HEAD": (0, "bbbbbbbbbbbb")})
        u = self.mk(f)
        r = u.apply()
        self.assertTrue(r["ok"] and r["restart"])
        pull = [c for c in f.calls if c[0] == "git" and c[3] == "pull"][0]
        self.assertEqual(pull[4:], ["--ff-only"])
        ps = [c for c in f.calls if c[0] == "powershell"][0]
        self.assertIn("-NoRestart", ps)
        self.assertEqual(json.loads((self.base / "installed-version.json").read_text(encoding="utf-8"))["hash"], "bbbbbbbbbbbb")
        self.assertEqual(u.info()["hash"], "bbbbbbbbbbbb")
        self.assertTrue((self.base / "server.prev" / "a.py").is_file())

    def test_apply_em_dia_nao_reinicia(self):
        r = self.mk(Fake()).apply()
        self.assertFalse(r["restart"])

    def test_pull_falha_nao_instala(self):
        f = Fake(**{"rev-list": (0, "0\t1"), "pull": (1, "not possible to fast-forward")})
        self.assertIn("sem merge", self.err(self.mk(f).apply))
        self.assertFalse(any(c[0] == "powershell" for c in f.calls))

    def test_instalacao_falha_restaura(self):
        f = Fake(**{"rev-list": (0, "0\t1"), "powershell": (1, "boom")})
        u = self.mk(f)

        def broken(argv, cwd, timeout):
            if argv[0] == "powershell":
                (self.base / "a.py").write_text("quebrado", encoding="utf-8")
            return f(argv, cwd, timeout)
        u.run = broken
        self.assertIn("restaurada", self.err(u.apply))
        self.assertEqual((self.base / "a.py").read_text(encoding="utf-8"), "v1")
        self.assertFalse((self.base / "installed-version.json").exists())

    def test_rollback(self):
        u = self.mk(Fake())
        self.assertIn("Não há versão anterior", self.err(u.rollback))
        prev = self.base / "server.prev"
        prev.mkdir()
        (prev / "a.py").write_text("antigo", encoding="utf-8")
        (self.base / "a.py").write_text("novo", encoding="utf-8")
        r = u.rollback()
        self.assertTrue(r["restart"])
        self.assertEqual((self.base / "a.py").read_text(encoding="utf-8"), "antigo")

    def test_info_sem_hash_nao_inventa(self):
        i = self.mk(Fake()).info()
        self.assertEqual((i["hash"], i["version"]), ("", "1.6"))


if __name__ == "__main__":
    unittest.main()
