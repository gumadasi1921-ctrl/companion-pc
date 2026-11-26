"""GSI do Dota (dota_gsi.py): mapeamento de fases, campos, token, listener em 127.0.0.1 e instalador do .cfg.
Fixtures falsas em tests/fixtures/gsi-<fase>.json. Rodar: py -3 tests/test_dota_gsi.py"""
import asyncio
import json
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "windows" / "server"))
import dota_gsi  # noqa: E402

FIX = ROOT / "fixtures"
TOKEN = "tok-de-teste"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


def fx(n):
    return json.loads((FIX / f"gsi-{n}.json").read_text(encoding="utf-8"))


def authed(n):
    d = fx(n)
    d["auth"]["token"] = TOKEN
    return d


class Look:
    def hero_name(self, i):
        return {74: "Invoker"}.get(i)

    def item(self, raw):
        return {"item_blink": {"id": 1, "name": "Blink Dagger"}}.get(raw)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def mk(steam=""):
    c = Clock()
    return dota_gsi.GSI(lambda: TOKEN, lambda: steam, c), c


def unit():
    g, c = mk()
    check("sem payload = sem estado", g.state() is None)
    check("token errado recusado", not g.accept(fx("game")) and g.state() is None)
    check("payload sem auth recusado", not g.accept({"map": {}}))
    check("não-dict recusado", not g.accept([1]))
    for n in ("menu", "draft", "pregame", "game", "postgame"):
        check(f"fase {n}", g.accept(authed(n)) and g.state(Look())["phase"] == n)
    g.accept(authed("game"))
    s = g.state(Look())
    L = s["live"]
    check("herói por id e nome", s["hero"]["hero_id"] == 74 and s["hero"]["hero"] == "Invoker" and s["hero"]["build"] is None)
    check("live: números", (L["clock_s"], L["gold"], L["gpm"], L["xpm"], L["k"], L["d"], L["a"], L["last_hits"], L["denies"], L["level"]) == (400, 900, 410, 520, 3, 1, 7, 55, 4, 9))
    check("live: daytime", L["daytime"] is True)
    check("itens: vazio e estoque fora; neutro = slot 9", [(x["slot"], x["item"]) for x in L["items_detail"]] == [(0, "item_tango"), (2, "Blink Dagger"), (6, "item_branches"), (9, "item_mysterious_hat")])
    check("item conhecido ganha id, desconhecido id null", L["items_detail"][1]["item_id"] == 1 and L["items_detail"][0]["item_id"] is None)
    check("sem fonte: build/timers/next/posição null", L["timers"] is None and L["next_item"] is None and s["hero"]["position"] is None)
    check("sem lookup usa o nome interno", g.state()["live"]["items"][0] == "item_tango")
    g.accept(authed("pregame"))
    check("pregame: relógio negativo passa como veio", g.state()["live"]["clock_s"] == -30)
    g.accept(authed("draft"))
    d = g.state(Look())
    check("draft sem herói escolhido (id 0)", d["phase"] == "draft" and d["hero"] is None and d["live"] is None)
    g.accept(authed("menu"))
    m = g.state(Look())
    check("sem map = menu, sem blocos", m["phase"] == "menu" and m["hero"] is None and m["live"] is None and m["result"] is None)
    g.accept(authed("postgame"))
    r = g.state(Look())["result"]
    check("resultado: vitória, KDA, partida", r == {"hero_id": 74, "win": True, "k": 3, "d": 1, "a": 7, "match_id": 8000000001}, str(r))
    p2 = authed("postgame")
    p2["map"]["win_team"] = "dire"
    g.accept(p2)
    check("derrota", g.state()["result"]["win"] is False)
    p2["map"]["win_team"] = "none"
    g.accept(p2)
    check("win_team desconhecido = null (não adivinha)", g.state()["result"]["win"] is None)
    p3 = authed("game")
    p3["map"]["game_state"] = "DOTA_GAMERULES_STATE_ALGO_NOVO"
    g.accept(p3)
    check("estado desconhecido = menu", g.state()["phase"] == "menu")
    g.accept(authed("spectator"))
    sp = g.state(Look())
    check("espectador: sem herói/live/resultado", sp["hero"] is None and sp["live"] is None and sp["result"] is None)
    g.accept(authed("game"))
    c.t += 36
    check("payload com mais de 35 s some", g.state() is None and not g.fresh())
    c.t -= 5
    check("com 31 s ainda vale", g.state() is not None)
    g2, _ = mk("76561197960265729")
    g2.accept(authed("game"))
    check("steam id 64 igual: vale", g2.state()["live"] is not None)
    g3, _ = mk("1")
    g3.accept(authed("game"))
    check("steam id de 32 bits igual: vale", g3.state()["live"] is not None)
    g4, _ = mk("76561197960265730")
    g4.accept(authed("game"))
    check("outro steam id (replay/outra conta): sem live", g4.state()["live"] is None and g4.state()["hero"] is None)
    check("steam64", dota_gsi.steam64("1") == 76561197960265729 and dota_gsi.steam64("76561197960265729") == 76561197960265729
          and dota_gsi.steam64("abc") is None and dota_gsi.steam64("") is None and dota_gsi.steam64("0") is None and dota_gsi.steam64("7656119796026572") is None)
    n = []
    g5, _ = mk()
    g5.on_change = lambda: n.append(1)
    g5.accept(authed("game"))
    g5.accept(fx("game"))   # o token do fixture é "TOKEN" (errado)
    check("on_change só em payload aceito", len(n) == 1)
    bad = {"auth": {"token": TOKEN}, "map": {"game_state": 5, "clock_time": "x", "daytime": "sim"}, "player": {"kills": "9", "gold": True}, "hero": {"id": "74"}, "items": {"slot0": {"name": 3}}}
    check("tipos errados viram null", g5.accept(bad) and g5.state()["phase"] == "menu")


async def post(port, raw, wait=3):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(raw)
    await w.drain()
    try:
        data = await asyncio.wait_for(r.read(200), wait)
    except asyncio.TimeoutError:
        data = b""
    w.close()
    return data.split(b"\r\n")[0].decode() if data else ""


def req(body, method="POST"):
    b = body if isinstance(body, bytes) else json.dumps(body).encode()
    return f"{method} / HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {len(b)}\r\n\r\n".encode() + b


async def listener():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    g, _ = mk()
    lis = dota_gsi.GsiListener(g, port)
    await lis.start()
    check("POST válido = 200 e estado", "200" in await post(port, req(authed("game"))) and g.state()["phase"] == "game")
    check("token errado = 403", "403" in await post(port, req(fx("game"))))
    check("GET = 405", "405" in await post(port, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"))
    check("JSON inválido = 400", "400" in await post(port, req(b"{nao")))
    check("lista JSON = 400", "400" in await post(port, req([1])))
    check("corpo grande = 413", "413" in await post(port, b"POST / HTTP/1.1\r\nContent-Length: 9999999\r\n\r\n"))
    check("sem Content-Length = 411", "411" in await post(port, b"POST / HTTP/1.1\r\nHost: x\r\n\r\n"))
    check("chunked = 411", "411" in await post(port, b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"))
    check("requisição sem fim não trava (fecha em 5 s)", await post(port, b"POST / HTTP/1.1\r\n", 8) == "")
    check("recusados contados", lis.bad >= 8, str(lis.bad))
    check("escuta só em 127.0.0.1", lis.server.sockets[0].getsockname()[0] == "127.0.0.1")
    await lis.stop()


def installer():
    with tempfile.TemporaryDirectory() as t:
        lib = Path(t, "SteamLibrary")
        beta = lib / "steamapps" / "common" / "dota 2 beta"
        dota = beta / "game" / "dota"
        (dota / "cfg").mkdir(parents=True)
        check("find_dota acha pela biblioteca", dota_gsi.find_dota([str(Path(t, "x")), str(lib)]) == dota)
        check("find_dota sem Dota = None", dota_gsi.find_dota([str(Path(t, "x"))]) is None)
        for pasta in (beta, beta / "game", dota, dota / "cfg"):
            check(f"resolve_dota aceita {pasta.name}", dota_gsi.resolve_dota(pasta) == dota)
        check("resolve_dota recusa pasta qualquer", dota_gsi.resolve_dota(t) is None)
        check("status antes", dota_gsi.status(dota, 8771, TOKEN) == {"found": True, "installed": False})
        dota_gsi.install(dota, 8771, TOKEN)
        f = dota_gsi.target_for(dota)
        txt = f.read_text(encoding="utf-8")
        check("cfg no lugar certo", f.parent.name == "gamestate_integration" and f.name == "gamestate_integration_companion.cfg")
        check("cfg com uri 127.0.0.1, token e heartbeat", '"http://127.0.0.1:8771/"' in txt and TOKEN in txt and '"heartbeat"' in txt)
        check("status depois", dota_gsi.status(dota, 8771, TOKEN)["installed"] is True)
        check("token novo = precisa reinstalar", dota_gsi.status(dota, 8771, "outro")["installed"] is False)
        dota_gsi.install(dota, 8771, "outro")
        check("reinstalar atualiza o nosso", "outro" in f.read_text(encoding="utf-8"))
        f.write_text("// de outra pessoa\n", encoding="utf-8")
        try:
            dota_gsi.install(dota, 8771, TOKEN)
            check("não sobrescreve arquivo alheio", False)
        except ValueError:
            check("não sobrescreve arquivo alheio", f.read_text(encoding="utf-8") == "// de outra pessoa\n")


unit()
asyncio.run(listener())
installer()
print("\nFALHARAM:", fails) if fails else print("\nTudo certo.")
sys.exit(1 if fails else 0)
