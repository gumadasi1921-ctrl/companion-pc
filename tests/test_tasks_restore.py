"""Testes do tasks.restore (desfazer remoção). Dados fictícios, pasta temporária. Rodar: py -3 tests/test_tasks_restore.py"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import tasks  # noqa: E402


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.t = tasks.TaskList(Path(self._d.name))
        for x in ("a", "b", "c"):
            self.t.add(x)
        self.ids = [i["id"] for i in self.t.get()["items"]]

    def tearDown(self):
        self._d.cleanup()

    def texts(self):
        return [i["text"] for i in self.t.get()["items"]]

    def test_volta_na_posicao(self):
        gone = self.t.get()["items"][1]
        self.t.remove(gone["id"])
        self.assertEqual(self.texts(), ["a", "c"])
        self.t.restore(gone["id"], gone["text"], False, 1)
        self.assertEqual(self.texts(), ["a", "b", "c"])
        self.assertEqual(self.t.get()["items"][1]["id"], gone["id"])

    def test_feita_volta_feita(self):
        self.t.toggle(self.ids[0])
        d = next(i for i in self.t.get()["items"] if i["done"])
        pos = self.t.get()["items"].index(d)
        self.t.remove(d["id"])
        self.t.restore(d["id"], d["text"], True, pos)
        got = self.t.get()["items"][pos]
        self.assertTrue(got["done"] and got["id"] == d["id"])

    def test_persiste(self):
        gone = self.t.get()["items"][0]
        self.t.remove(gone["id"])
        self.t.restore(gone["id"], gone["text"], False, 0)
        again = tasks.TaskList(Path(self._d.name))
        self.assertEqual([i["id"] for i in again.get()["items"]], self.ids)

    def test_posicao_fora_da_lista_e_limitada(self):
        self.t.restore("0123abcd", "x", False, 999)
        self.assertEqual(self.texts()[-1], "x")
        self.t.restore("1123abcd", "y", False, -5)
        self.assertEqual(self.texts()[0], "y")

    def test_validacoes(self):
        for args in (("zz", "x", False, 0), (5, "x", False, 0), ("0123abcd", "", False, 0),
                     ("0123abcd", "   ", False, 0), ("0123abcd", "x" * 201, False, 0),
                     ("0123abcd", "x", "sim", 0), ("0123abcd", "x", False, "1"),
                     ("0123abcd", "x", False, True), ("0123abcd", "x", False, 1.5), ("0123abcd", None, False, 0)):
            with self.assertRaises(ValueError, msg=str(args)):
                self.t.restore(*args)
        self.assertEqual(self.texts(), ["a", "b", "c"])

    def test_id_repetido(self):
        with self.assertRaises(ValueError):
            self.t.restore(self.ids[0], "z", False, 0)

    def test_limite_de_itens(self):
        for n in range(tasks.MAX_ITEMS - 3):
            self.t.add(f"t{n}")
        self.assertEqual(self.t.get()["total"], tasks.MAX_ITEMS)
        with self.assertRaises(ValueError):
            self.t.restore("0123abcd", "x", False, 0)
        self.assertEqual(self.t.get()["total"], tasks.MAX_ITEMS)
        self.t.remove(self.t.get()["items"][0]["id"])
        self.t.restore("0123abcd", "x", False, 0)   # com vaga, volta a funcionar
        self.assertEqual(self.t.get()["total"], tasks.MAX_ITEMS)


if __name__ == "__main__":
    unittest.main()
