"""Trello somente leitura (trello.py) com fixtures, sem rede. Rodar: py -3 tests/test_trello.py"""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import trello  # noqa: E402

KEY, TOKEN = "k" * 32, "ATTAsecretsecretsecret" + "t" * 40


def utc(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


NOW = utc(2026, 11, 24, 15)       # terça 24/11/2026 12:00 em Brasília


def due(y, mo, d, h=15, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


CARDS = [
    {"id": "c1", "name": "Atrasado", "due": due(2026, 11, 23), "dueComplete": False, "idBoard": "b1", "idList": "l1", "shortLink": "AAAAAAA1",
     "labels": [{"name": "Urgente", "color": "red"}, {"name": "", "color": None}], "dateLastActivity": "2026-11-20T10:00:00.000Z"},
    {"id": "c2", "name": "Hoje cedo", "due": due(2026, 11, 24, 12), "dueComplete": False, "idBoard": "b1", "idList": "l2", "shortLink": "AAAAAAA2", "labels": []},
    {"id": "c3", "name": "Hoje 23h30 em Brasília", "due": due(2026, 11, 25, 2, 30), "dueComplete": False, "idBoard": "b2", "idList": "l3", "shortLink": "AAAAAAA3", "labels": []},
    {"id": "c4", "name": "Amanhã", "due": due(2026, 11, 25, 15), "dueComplete": False, "idBoard": "b2", "idList": "l3", "shortLink": "AAAAAAA4", "labels": []},
    {"id": "c5", "name": "Sexta", "due": due(2026, 11, 27), "dueComplete": False, "idBoard": "b1", "idList": "l2", "shortLink": "AAAAAAA5", "labels": []},
    {"id": "c6", "name": "Domingo", "due": due(2026, 11, 29), "dueComplete": False, "idBoard": "b1", "idList": "l2", "shortLink": "AAAAAAA6", "labels": []},
    {"id": "c7", "name": "Semana que vem", "due": due(2026, 11, 30), "dueComplete": False, "idBoard": "b1", "idList": "l2", "shortLink": "AAAAAAA7", "labels": []},
    {"id": "c8", "name": "Sem prazo", "due": None, "dueComplete": False, "idBoard": "b2", "idList": "l3", "shortLink": "AAAAAAA8", "labels": []},
    {"id": "c9", "name": "Prazo concluído", "due": due(2026, 11, 20), "dueComplete": True, "idBoard": "b1", "idList": "l1", "shortLink": "AAAAAAA9", "labels": []},
    {"id": "cx", "name": "shortLink inválido", "due": None, "dueComplete": False, "idBoard": "b1", "idList": "l1", "shortLink": "../etc", "labels": []},
]
BOARDS = [{"id": "b1", "name": "Quadro Acme"}, {"id": "b2", "name": "Pessoal"}]
LISTS = {"b1": [{"id": "l1", "name": "A fazer"}, {"id": "l2", "name": "Fazendo"}], "b2": [{"id": "l3", "name": "Caixa de entrada"}]}


class Fake:
    """fetch() falso que registra as chamadas."""
    def __init__(self):
        self.calls, self.fail = [], None

    def __call__(self, path, params, key, token):
        self.calls.append(path)
        if self.fail:
            raise self.fail
        if path == "members/me/cards":
            return json.loads(json.dumps(CARDS))
        if path == "members/me/boards":
            return BOARDS
        if path.startswith("boards/") and path.endswith("/lists"):
            return LISTS[path.split("/")[1]]
        if path == "members/me":
            return {"fullName": "Maria Teste", "username": "maria"}
        raise AssertionError("chamada inesperada " + path)


class TrelloTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.cfg = {"key": KEY, "token": TOKEN, "on": True}
        self.now = [NOW]
        self.fake = Fake()
        self.t = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])

    def tearDown(self):
        self._d.cleanup()

    def test_parse_and_groups(self):
        self.assertTrue(self.t.refresh())
        st = self.t.state()
        g = {k: [c["name"] for c in v] for k, v in st["groups"].items()}
        self.assertEqual(g["overdue"], ["Atrasado"])
        self.assertEqual(g["today"], ["Hoje cedo", "Hoje 23h30 em Brasília"])    # 02:30Z de 25/11 = 23:30 de 24/11 em Brasília
        self.assertEqual(g["tomorrow"], ["Amanhã"])
        self.assertEqual(g["week"], ["Sexta", "Domingo"])
        self.assertEqual(g["later"], ["Semana que vem"])
        self.assertEqual(g["none"], ["Sem prazo"])
        self.assertEqual(st["hidden_done"], 1)
        self.assertEqual(st["today"], "2026-11-24")
        c = st["groups"]["overdue"][0]
        self.assertEqual((c["board"], c["list"], c["short"], c["due"]), ("Quadro Acme", "A fazer", "AAAAAAA1", "2026-11-23"))
        self.assertEqual(c["labels"][0], {"n": "Urgente", "c": "red"})
        self.assertEqual(st["groups"]["today"][1]["time"], "23:30")
        self.assertEqual(st["groups"]["none"][0]["due"], None)                   # nunca inventa prazo
        self.assertEqual(st["counts"]["week"], 2)
        self.assertEqual(sorted(self.fake.calls), sorted(["members/me/cards", "members/me/boards", "boards/b1/lists", "boards/b2/lists"]))
        self.assertFalse(any(c["short"] == "../etc" for c in self.t.all_cards()))

    def test_midnight_in_brasilia(self):
        self.t.refresh()
        self.now[0] = utc(2026, 11, 25, 2, 59)    # 23:59 de 24/11 em Brasília: ainda "hoje"
        self.assertEqual(self.t.state()["today"], "2026-11-24")
        self.now[0] = utc(2026, 11, 25, 3, 1)     # 00:01 de 25/11
        st = self.t.state()
        self.assertEqual(st["today"], "2026-11-25")
        self.assertEqual([c["name"] for c in st["groups"]["overdue"]], ["Atrasado", "Hoje cedo", "Hoje 23h30 em Brasília"])

    def test_lists_cached_between_refreshes(self):
        self.t.refresh()
        self.fake.calls.clear()
        self.now[0] += 400
        self.t.refresh()
        self.assertEqual(self.fake.calls, ["members/me/cards"])

    def test_401_stops_trying_until_credentials_change(self):
        self.t.refresh()
        self.fake.fail = trello.CredError("http 401")
        self.now[0] += 400
        self.assertTrue(self.t.due(True))
        self.t.refresh()
        self.assertEqual(self.t.status()["state"], "bad_cred")
        n = len(self.fake.calls)
        self.now[0] += 4000
        self.assertFalse(self.t.due(True))
        self.assertFalse(self.t.refresh_manual())
        self.assertEqual(len(self.fake.calls), n)                    # parou de tentar
        self.assertEqual(len(self.t.cards), 9)                       # a última leitura continua
        again = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])
        self.assertTrue(again.blocked)                               # sobrevive a reiniciar o servidor
        self.fake.fail = None
        self.t.reset()
        self.assertTrue(self.t.due(True))
        self.t.refresh()
        self.assertEqual(self.t.status()["state"], "ok")

    def test_network_error_keeps_last_reading(self):
        self.t.refresh()
        at = self.t.at
        self.fake.fail = trello.NetError("rede")
        self.now[0] += 400
        self.t.refresh()
        st = self.t.status()
        self.assertEqual(st["state"], "net_error")
        self.assertEqual(self.t.at, at)
        self.assertEqual(len(self.t.state()["groups"]["today"]), 2)
        self.assertEqual(self.t.state()["hhmm"], "12:00")            # "atualizado às 12:00"
        self.assertFalse(self.t.due(True))
        self.now[0] += 61
        self.assertTrue(self.t.due(True))                            # depois de erro de rede tenta de novo em 1 min
        self.fake.fail = None
        self.t.refresh()
        self.assertEqual(self.t.status()["state"], "ok")

    def test_throttling(self):
        self.assertTrue(self.t.due(True))
        self.assertFalse(self.t.due(False))                          # sem aparelho: parado
        self.t.refresh()
        self.assertFalse(self.t.due(True))
        self.now[0] += 299
        self.assertFalse(self.t.due(True))
        self.now[0] += 2
        self.assertTrue(self.t.due(True))
        n = len(self.fake.calls)
        self.now[0] += 61
        self.assertTrue(self.t.refresh_manual())
        self.assertFalse(self.t.refresh_manual())                    # 2ª seguida: no máximo 1x por minuto
        self.assertEqual(len(self.fake.calls), n + 1)
        self.cfg["on"] = False
        self.assertFalse(self.t.due(True))

    def test_state_and_status_never_carry_credentials(self):
        self.t.refresh()
        blob = json.dumps([self.t.state(), self.t.status(), self.t.all_cards()]) + (self.base / "trello.json").read_text(encoding="utf-8")
        self.assertNotIn(KEY, blob)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("secretsecret", blob)

    def test_has_card_only_cached_valid_short_links(self):
        self.t.refresh()
        self.assertTrue(self.t.has_card("AAAAAAA4"))
        for bad in ("ZZZZZZZZ", "../../x", "AAAAAAA", "AAAAAAA44", None, 5, "http://x"):
            self.assertFalse(self.t.has_card(bad))

    def test_digest_once_per_day_after_9am(self):
        self.t.refresh()
        self.now[0] = utc(2026, 11, 24, 11, 59)                      # 08:59 em Brasília
        self.assertIsNone(self.t.digest())
        self.now[0] = utc(2026, 11, 24, 12, 1)                       # 09:01
        d = self.t.digest()
        self.assertEqual((d["overdue"], d["today"]), (1, 2))
        self.assertIn("1 atrasado", d["title"])
        self.assertIn("2 para hoje", d["title"])
        self.assertIsNone(self.t.digest())                           # 1x por dia
        again = trello.Trello(self.base, lambda: self.cfg, self.fake, lambda: self.now[0])
        self.assertIsNone(again.digest())                            # nem depois de reiniciar
        self.now[0] += 86400
        self.assertIsNotNone(again.digest() or {"x": 1})

    def test_test_button_returns_only_name(self):
        self.assertEqual(self.t.test(KEY, TOKEN), "Maria Teste")


class NoWrites(unittest.TestCase):
    """Todo o acesso à rede passa por http_get: só GET, só api.trello.com, credencial só no cabeçalho."""

    def run_http(self, code=None, body=b"[]"):
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append((req.get_method(), req.full_url, dict(req.header_items()), req.data, timeout))
            if code:
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
            return mock.MagicMock(__enter__=lambda s: mock.MagicMock(read=lambda: body), __exit__=lambda *a: False)
        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            try:
                trello.http_get("members/me/cards", {"filter": "open", "fields": "name"}, KEY, TOKEN)
                err = None
            except Exception as e:  # noqa: BLE001
                err = e
        return seen, err

    def test_only_get_on_trello_host_credentials_in_header(self):
        seen, err = self.run_http()
        self.assertIsNone(err)
        m, url, headers, data, timeout = seen[0]
        self.assertEqual(m, "GET")
        self.assertTrue(url.startswith("https://api.trello.com/1/members/me/cards?"))
        self.assertIsNone(data)
        self.assertEqual(timeout, 10)
        self.assertNotIn(KEY, url)
        self.assertNotIn(TOKEN, url)
        self.assertIn(KEY, headers["Authorization"])

    def test_errors_are_fixed_texts(self):
        for code, cls in ((401, trello.CredError), (403, trello.CredError), (500, trello.NetError), (429, trello.NetError)):
            _seen, err = self.run_http(code)
            self.assertIsInstance(err, cls)
            self.assertNotIn(KEY, str(err))
            self.assertNotIn(TOKEN, str(err))
            self.assertNotIn("trello.com", str(err))
        _seen, err = self.run_http(body=b"nao json")
        self.assertIsInstance(err, trello.NetError)
        with mock.patch.object(urllib.request, "urlopen", side_effect=TimeoutError("api.trello.com?key=" + KEY)):
            with self.assertRaises(trello.NetError) as cm:
                trello.http_get("members/me", {}, KEY, TOKEN)
            self.assertNotIn(KEY, str(cm.exception))

    def test_module_source_has_no_write_methods(self):
        src = (Path(trello.__file__)).read_text(encoding="utf-8")
        for word in ('"POST"', '"PUT"', '"DELETE"', "'POST'", "'PUT'", "'DELETE'", "data=", ".post(", "method=\"P", "method=\"D"):
            self.assertNotIn(word, src, word)
        self.assertEqual(src.count("urlopen("), 1)
        self.assertEqual(src.count('method="GET"'), 1)

    def test_full_refresh_over_real_http_get_only_gets(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_method() + " " + req.full_url.split("?")[0].replace(trello.API, ""))
            p = req.full_url.split("?")[0].replace(trello.API, "")
            body = {"members/me/cards": CARDS, "members/me/boards": BOARDS, "boards/b1/lists": LISTS["b1"], "boards/b2/lists": LISTS["b2"]}[p]
            return mock.MagicMock(__enter__=lambda s: mock.MagicMock(read=lambda: json.dumps(body).encode()), __exit__=lambda *a: False)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            t = trello.Trello(Path(d), lambda: {"key": KEY, "token": TOKEN, "on": True}, trello.http_get, lambda: NOW)
            t.refresh()
            self.assertEqual(t.status()["state"], "ok")
        self.assertTrue(calls and all(c.startswith("GET ") for c in calls), calls)


if __name__ == "__main__":
    unittest.main(verbosity=1)
