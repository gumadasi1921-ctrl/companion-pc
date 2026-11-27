"""Teste ESTÁTICO das tabelas de ações do companion_server.py (ast, sem importar o servidor).
Para ACTIONS, ASYNC_ACTIONS, PANEL_OPS, PANEL_WS e PANEL_ASYNC:
  1. nome usado fora de lambda (avaliado ao criar a tabela) precisa estar definido ANTES dela;
  2. nome dentro de lambda precisa existir em algum lugar do módulo (a lambda resolve na chamada);
  3. sem chave repetida no mesmo literal (a última sobrescreve em silêncio);
  4. todo nome de QUIET_ACTIONS existe em alguma tabela (erro de digitação = ação que vai para o log).
Rodar: py -3 tests/test_actions_static.py"""
import ast
import builtins
import collections
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "windows" / "server" / "companion_server.py"
TABLES = ("ACTIONS", "ASYNC_ACTIONS", "PANEL_OPS", "PANEL_WS", "PANEL_ASYNC")
TREE = ast.parse(SRC.read_text(encoding="utf-8"))


def _defined():
    out = collections.defaultdict(list)

    def collect(body):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[n.name].append(n.lineno)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    out[(a.asname or a.name).split(".")[0]].append(n.lineno)
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    for x in ast.walk(t):
                        if isinstance(x, ast.Name):
                            out[x.id].append(n.lineno)
            elif isinstance(n, (ast.If, ast.Try, ast.With)):
                collect(n.body)
                collect(getattr(n, "orelse", []))
                for h in getattr(n, "handlers", []):
                    collect(h.body)
                collect(getattr(n, "finalbody", []))
    collect(TREE.body)
    return out


DEFINED = _defined()


def _names(node, in_lambda=False, bound=frozenset()):
    """(nome, linha, dentro_de_lambda) de cada uso de nome que não é parâmetro/variável de compreensão."""
    if isinstance(node, ast.Lambda):
        b = bound | {a.arg for a in node.args.args + node.args.kwonlyargs}
        if node.args.vararg:
            b |= {node.args.vararg.arg}
        if node.args.kwarg:
            b |= {node.args.kwarg.arg}
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            yield from _names(d, in_lambda, bound)
        yield from _names(node.body, True, b)
        return
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        b = set(bound)
        for g in node.generators:
            b |= {x.id for x in ast.walk(g.target) if isinstance(x, ast.Name)}
        b = frozenset(b)
        for g in node.generators:
            yield from _names(g.iter, in_lambda, bound)
            for i in g.ifs:
                yield from _names(i, in_lambda, b)
        for f in ("elt", "key", "value"):
            if hasattr(node, f):
                yield from _names(getattr(node, f), in_lambda, b)
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
        yield node.id, node.lineno, in_lambda
    for c in ast.iter_child_nodes(node):
        yield from _names(c, in_lambda, bound)


def _table_nodes():
    for n in TREE.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in TABLES:
                    yield t.id, n


def _expand(comp):
    """**{f"pomo_{n}": ... for n in ("a", "b")} -> ["pomo_a", "pomo_b"]; formato que não entende vira erro (nada é adivinhado)."""
    g = comp.generators[0]
    if not (isinstance(comp, ast.DictComp) and isinstance(comp.key, ast.JoinedStr) and len(comp.generators) == 1
            and isinstance(g.target, ast.Name) and isinstance(g.iter, (ast.Tuple, ast.List))
            and all(isinstance(e, ast.Constant) for e in g.iter.elts)):
        raise AssertionError(f"L{comp.lineno}: **{{...}} num formato que o teste não sabe expandir")
    out = []
    for e in g.iter.elts:
        out.append("".join(p.value if isinstance(p, ast.Constant) else str(e.value) for p in comp.key.values))
    return out


def _keys(dict_node):
    """Chaves do literal, inclusive as geradas por **{f"...{n}": ... for n in (...)}."""
    out = []
    for k, v in zip(dict_node.keys, dict_node.values):
        if k is None:
            out += _expand(v)
        elif isinstance(k, ast.Constant):
            out.append(k.value)
    return out


class ActionTables(unittest.TestCase):
    def test_tabelas_encontradas(self):
        self.assertEqual(sorted(t for t, _ in _table_nodes()), sorted(TABLES))

    def test_nomes_definidos(self):
        bad = []
        for tab, n in _table_nodes():
            for nm, ln, lam in _names(n.value):
                if hasattr(builtins, nm):
                    continue
                lines = DEFINED.get(nm)
                if not lines:
                    bad.append(f"{tab} L{ln}: '{nm}' nunca é definido")
                elif not lam and min(lines) > n.lineno:
                    bad.append(f"{tab} L{ln}: '{nm}' usado na criação da tabela mas só definido na L{min(lines)}")
        self.assertEqual(bad, [])

    def test_sem_chave_repetida(self):
        bad = []
        for tab, n in _table_nodes():
            bad += [f"{tab}: '{k}' repetida" for k, c in collections.Counter(_keys(n.value)).items() if c > 1]
        self.assertEqual(bad, [])

    def test_quiet_actions_existem(self):
        known = set()
        for _, n in _table_nodes():
            known |= set(_keys(n.value))
        # tabelas montadas por comprensão (pomo_*): aceita o nome gerado por f-string só se estiver na própria tabela
        quiet = None
        for n in TREE.body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "QUIET_ACTIONS" for t in n.targets):
                quiet = n.value
        self.assertIsNotNone(quiet)
        names = {e.value for e in quiet.elts if isinstance(e, ast.Constant)}
        self.assertTrue(names)
        self.assertEqual(sorted(names - known), [])


if __name__ == "__main__":
    unittest.main()
