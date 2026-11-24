"""Regras manuais, ignorados e parse de Illustrator/CapCut do work.py. Dados fictícios, pasta temporária.
Rodar: py -3 tests/test_work_rules.py   (autorização do painel: tests/test_panel_auth.py)"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import work  # noqa: E402

U = work.UNIDENTIFIED
MON = date(2026, 11, 23)           # segunda
TUE = "2026-11-24"
OLD = "2026-10-05"                 # outra semana


def mk(tmp, days=None):
    t = work.WorkTracker(Path(tmp))
    t.days = days if days is not None else {
        TUE: {f"{U}\t—\tPhotoshop\tLogo.psd": 600.0, f"{U}\t—\tPhotoshop\tsem título": 120.0,
              f"{U}\t—\tIllustrator\tTABELA.pdf": 300.0, "Acme\tSite\tPremiere\tx.prproj": 60.0},
        OLD: {f"{U}\t—\tPhotoshop\tlogo.PSD": 1000.0},
    }
    return t


def total(t):
    return sum(s for v in t.days.values() for s in v.values())


def week_total(t, today=MON):
    return t.week(0, today)["total_s"]


class Rules(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = self._d.name
        self.t = mk(self.tmp)

    def tearDown(self):
        self._d.cleanup()

    def clients(self, today=MON):
        return {c["client"]: c for c in self.t.week(0, today)["clients"]}

    def test_regra_exata_sem_diferenciar_maiusculas(self):
        r = self.t.assign([{"file": "LOGO.psd", "app": "Photoshop"}], "Beta", "Marca", True, 0, MON)
        self.assertEqual(r["rules"], 1)
        self.assertEqual(r["total_s"], 1600)   # 600 desta semana + 1000 de outra
        self.assertEqual(r["week_s"], 600)
        c = self.clients()
        self.assertEqual(c["Beta"]["s"], 600)
        self.assertEqual(c[U]["s"], 120 + 300)
        # outra semana também foi reclassificada (histórico inteiro)
        self.assertEqual(self.t.week(-7, MON)["clients"][0]["client"], "Beta")

    def test_nunca_por_parecido(self):
        self.t.days[TUE][f"{U}\t—\tPhotoshop\tLogo.psd copia.psd"] = 50.0
        self.t.days[TUE][f"{U}\t—\tPhotoshop\tLogo2.psd"] = 40.0
        self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Beta", "", True, 0, MON)
        files = {f["file"] for c in self.t.week(0, MON)["clients"] if c["client"] == U for j in c["jobs"] for f in j["files"]}
        self.assertIn("Logo.psd copia.psd", files)
        self.assertIn("Logo2.psd", files)
        self.assertNotIn("Logo.psd", files)

    def test_app_diferente_nao_casa(self):
        self.t.assign([{"file": "TABELA.pdf", "app": "Photoshop"}], "Beta", "", True, 0, MON)
        self.assertIn("TABELA.pdf", json.dumps(self.t.week(0, MON)["clients"][0]))  # Illustrator continua Não identificado
        self.assertEqual(self.clients()[U]["s"], 600 + 120 + 300)

    def test_generico_nao_vira_regra_so_pontual(self):
        for nome in ("sem título", "Sem Título-3", "Untitled-12", "untitled", "CapCut · sem projeto"):
            self.assertTrue(work.is_generic(nome), nome)
        self.assertFalse(work.is_generic("Untitled-final.psd"))
        r = self.t.assign([{"file": "sem título", "app": "Photoshop"}], "Beta", "J", True, 0, MON)
        self.assertEqual((r["rules"], r["once"]), (0, 1))
        self.assertEqual(self.t.rules, [])
        self.assertEqual(self.clients()["Beta"]["s"], 120)
        # fora do período (semana seguinte) o mesmo nome continua Não identificado
        self.t.days["2026-12-01"] = {f"{U}\t—\tPhotoshop\tsem título": 77.0}
        nxt = {c["client"]: c["s"] for c in self.t.week(0, date(2026, 12, 2))["clients"]}
        self.assertEqual(nxt, {U: 77})

    def test_pontual_sem_lembrar(self):
        r = self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Beta", "", False, 0, MON)
        self.assertEqual((r["rules"], r["once"]), (0, 1))
        self.assertEqual(self.clients()["Beta"]["s"], 600)
        self.assertEqual(self.t.week(-7, MON)["clients"][0]["client"], U)  # a outra semana não mudou

    def test_ignorar_restaurar(self):
        before = total(self.t)
        r = self.t.ignore([{"file": "Logo.psd", "app": "Photoshop"}], 0, MON)
        self.assertEqual(week_total(self.t), 600 + 120 + 300 + 60 - 600)
        self.assertNotIn("Logo.psd", self.t.csv(0, MON)[1].decode("utf-8"))
        self.assertEqual(total(self.t), before)                      # continua guardado
        self.assertEqual(len(self.t.rules_get(MON)["ignored"]), 1)
        self.assertEqual(self.t.rules_get(MON)["ignored"][0]["total_s"], 1600)
        self.t.unignore(r["ids"])
        self.assertEqual(week_total(self.t), 600 + 120 + 300 + 60)
        with self.assertRaises(ValueError):
            self.t.unignore(r["ids"])

    def test_ignorar_generico_so_no_periodo(self):
        self.t.ignore([{"file": "sem título", "app": "Photoshop"}], 0, MON)
        self.t.days["2026-12-01"] = {f"{U}\t—\tPhotoshop\tsem título": 77.0}
        self.assertEqual(self.t.week(0, date(2026, 12, 2))["total_s"], 77)
        self.assertEqual(week_total(self.t), 600 + 300 + 60)

    def test_apagar_regra_volta_a_nao_identificado(self):
        r = self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Beta", "", True, 0, MON)
        self.assertNotIn(U, [c["client"] for c in self.t.week(-7, MON)["clients"]])
        self.t.rule_delete(r["ids"])
        self.assertEqual(self.t.week(-7, MON)["clients"][0]["client"], U)
        self.assertEqual(self.clients()[U]["s"], 1020)

    def test_total_preservado(self):
        w0, t0 = week_total(self.t), total(self.t)
        self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}, {"file": "sem título", "app": "Photoshop"}], "Beta", "J", True, 0, MON)
        self.assertEqual(week_total(self.t), w0)
        self.assertEqual(total(self.t), t0)

    def test_cliente_job_validacao_e_grafia_existente(self):
        for bad in ("", "   ", "x" * 61, "a\x07b", U):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], bad, "", True, 0, MON)
        r = self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "acme", "SITE", True, 0, MON)
        self.assertEqual((r["client"], r["job"]), ("Acme", "Site"))   # usa a grafia que já existe
        with self.assertRaises(ValueError):
            self.t.assign([{"file": "Outro.psd", "app": "Photoshop"}], "Beta", "j" * 61, True, 0, MON)
        r = self.t.assign([{"file": "Outro.psd", "app": "Photoshop"}], "Beta", "", True, 0, MON)
        self.assertEqual(r["job"], work.NO_JOB)
        self.assertEqual(self.t.catalog()["Beta"], [])

    def test_regra_duplicada_recusada(self):
        self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Beta", "", True, 0, MON)
        with self.assertRaises(ValueError):
            self.t.assign([{"file": "LOGO.PSD", "app": "Photoshop"}], "Gama", "", True, 0, MON)

    def test_itens_invalidos(self):
        for bad in ([], "x", [{"file": "a", "app": "Word"}], [{"file": "", "app": "Photoshop"}], [{"file": "a\n", "app": "Photoshop"}],
                    [1], [{"file": "a"}]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.t.assign(bad, "Beta", "", True, 0, MON)

    def test_previa(self):
        p = self.t.assign_preview([{"file": "logo.psd", "app": "Photoshop"}, {"file": "sem título", "app": "Photoshop"}], 0, MON)
        self.assertEqual((p["week_s"], p["total_s"]), (720, 1720))
        self.assertEqual([i["generic"] for i in p["items"]], [False, True])
        self.assertEqual(self.t.rules, [])       # não grava nada

    def test_persistencia_e_json_corrompido(self):
        self.t.assign([{"file": "Logo.psd", "app": "Photoshop"}], "Beta", "", True, 0, MON)
        t2 = work.WorkTracker(Path(self.tmp))
        self.assertEqual(len(t2.rules), 1)
        (Path(self.tmp) / "work_rules.json").write_text("{quebrado", encoding="utf-8")
        t3 = work.WorkTracker(Path(self.tmp))
        self.assertEqual((t3.rules, t3.once, t3.ignored), ([], [], []))
        self.assertTrue(list(Path(self.tmp).glob("work_rules.json.bad-*")))
        (Path(self.tmp) / "work_rules.json").write_text(json.dumps({"rules": "x"}), encoding="utf-8")
        self.assertEqual(work.WorkTracker(Path(self.tmp)).rules, [])
        self.assertEqual(len(list(Path(self.tmp).glob("work_rules.json.bad-*"))) >= 1, True)

    def test_prioridade_raiz_indice_regra(self):
        root = Path(self.tmp) / "Clientes"
        (root / "Real" / "Job1").mkdir(parents=True)
        (root / "Real" / "Job1" / "Logo.psd").write_bytes(b"")
        t = work.WorkTracker(Path(self.tmp))
        t.roots = [str(root)]
        t.rules = [{"id": "r1", "file": "Logo.psd", "app": "Photoshop", "client": "Manual", "job": "M"},
                   {"id": "r2", "file": "Outro.ai", "app": "Illustrator", "client": "Manual", "job": "M"}]
        t._reindex_rules()
        # (2) índice do Photoshop vence a regra
        self.assertEqual(t.attribute("photoshop.exe", "Logo.psd @ 50% (RGB/8)", 10_000)[:2], ("Real", "Job1"))
        # (1) caminho completo dentro da raiz vence a regra
        p = str(root / "Real" / "Job1" / "Outro.ai")
        self.assertEqual(t.attribute("illustrator.exe", p + " @ 50 % (RGB/Visualizar)", 0)[:2], ("Real", "Job1"))
        # (3) sem raiz/índice: Não identificado no histórico, regra aplicada na leitura
        self.assertEqual(t.attribute("illustrator.exe", "Outro.ai @ 50 % (RGB/Visualizar)", 0)[:2], (U, work.NO_JOB))
        self.assertEqual(t._manual("Outro.ai", "Illustrator", MON), ("assign", "Manual", "M"))
        # (4) nada
        self.assertIsNone(t._manual("Nada.ai", "Illustrator", MON))

    def test_poll_guarda_nao_identificado_e_mostra_regra(self):
        self.t.rules = [{"id": "r1", "file": "Foo.psd", "app": "Photoshop", "client": "Beta", "job": "J"}]
        self.t._reindex_rules()
        self.t.days = {}
        info = self.t.poll(now=1_000_000.0, fg=("photoshop.exe", "Foo.psd @ 50% (x)"), idle=1)
        self.t.poll(now=1_000_002.0, fg=("photoshop.exe", "Foo.psd @ 50% (x)"), idle=1)
        self.assertEqual(self.t.now_info["client"], "Beta")
        keys = [k for v in self.t.days.values() for k in v]
        self.assertTrue(keys and all(k.startswith(U + "\t") for k in keys))


class Parse(unittest.TestCase):
    """Títulos reais vistos neste PC (nomes anonimizados)."""

    def test_illustrator(self):
        self.assertEqual(work.parse_title("illustrator.exe", "TABELA X V2.pdf @ 66,67 % (RGB/Visualizar) "), ("TABELA X V2.pdf", None))
        self.assertEqual(work.parse_title("illustrator.exe", "arte.ai* @ 100 % (CMYK/Visualizar)"), ("arte.ai", None))
        self.assertEqual(work.parse_title("illustrator.exe", "Sem título-1* @ 50 % (RGB/Visualizar)"), (None, None))
        self.assertEqual(work.parse_title("illustrator.exe", "Adobe Illustrator"), (None, None))
        self.assertEqual(work.parse_title("illustrator.exe", ""), (None, None))
        f, p = work.parse_title("illustrator.exe", "C:\\Clientes\\Acme\\Site\\a.ai @ 100 % (RGB)")
        self.assertEqual((f, p), ("a.ai", "C:\\Clientes\\Acme\\Site\\a.ai"))
        self.assertEqual(work.client_job(p, ["C:\\Clientes"]), ("Acme", "Site"))

    def test_capcut(self):
        self.assertEqual(work.parse_title("capcut.exe", "CapCut"), (work.CAPCUT_NO_PROJECT, None))
        self.assertEqual(work.parse_title("capcut.exe", "Outra coisa"), (None, None))
        self.assertEqual(work.parse_title("capcut.exe", "CapCutProjeto X (1)"), ("Projeto X (1)", None))
        self.assertFalse(work.is_generic("Projeto X (1)"))

    def test_capcut_draft_project(self):
        def w(p):
            return p.replace("/", chr(92))   # caminhos do Windows
        d = "C:/Users/U/CapCut/User Data/com.lveditor.draft/"
        self.assertEqual(work.draft_project([w(d + "Projeto X (1)/draft_content.json"), w(d + "Projeto X (1)/a/b.bin"), w("C:/outro.txt")]), "Projeto X (1)")
        self.assertIsNone(work.draft_project([w(d + "A/x"), w(d + "B/y")]))   # duas pastas: não adivinha
        self.assertIsNone(work.draft_project([w("C:/outro.txt"), w(d + "arquivo_solto.json")]))
        self.assertIsNone(work.draft_project([]))
        self.assertEqual(work.draft_project([d + "P/x"]), "P")
        work._CAPCUT_CACHE.clear()
        work._CAPCUT_CACHE[99] = (1000.0, "Em cache")
        self.assertEqual(work.capcut_project(99, now=1005.0), "Em cache")   # <10 s: sem nova consulta
        self.assertTrue(work.is_generic(work.CAPCUT_NO_PROJECT))
        self.assertEqual(work.APPS["capcut.exe"], "CapCut")
        self.assertEqual(work.APPS["illustrator.exe"], "Illustrator")

    def test_capcut_vira_unidentificado_pontual(self):
        with tempfile.TemporaryDirectory() as d:
            t = work.WorkTracker(Path(d))
            self.assertEqual(t.attribute("capcut.exe", "CapCut", 0), (U, work.NO_JOB, work.CAPCUT_NO_PROJECT, False))
            t.days = {TUE: {f"{U}\t—\tCapCut\t{work.CAPCUT_NO_PROJECT}": 90.0}}
            r = t.assign([{"file": work.CAPCUT_NO_PROJECT, "app": "CapCut"}], "Beta", "", True, 0, MON)
            self.assertEqual((r["rules"], r["once"]), (0, 1))

    def test_csv_tem_app_nova(self):
        with tempfile.TemporaryDirectory() as d:
            t = mk(d)
            self.assertIn("Illustrator", t.csv(0, MON)[1].decode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
