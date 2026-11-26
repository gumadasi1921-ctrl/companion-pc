"""Junção das fontes do Dota (dota.compose) e imagens do CDN da Valve no proxy (dota_img). Sem rede.
Rodar: py -3 tests/test_dota_compose.py"""
import json
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "windows" / "server"))
import dota  # noqa: E402
import dota_img  # noqa: E402

FIX = ROOT / "fixtures"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


def bridge(phase):
    return dota.with_process(json.loads((FIX / f"dota-{phase}.json").read_text(encoding="utf-8")), True, False, None)


class Od:
    official = None

    def __init__(self, lobby=None):
        self._l = lobby

    def lobby(self):
        return self._l

    def hero_mine(self, hid):
        return {"matches": 5, "winrate": 0.6, "last_played": 1, "avg_kda": None} if hid == 74 else None


GSI = {"v": 1, "phase": "game", "lobby": None, "hero": {"hero_id": 74, "hero": "Invoker", "position": None, "position_source": None, "mine": None, "build": None},
       "live": {"clock_s": 5, "gold": 1}, "result": None}
LOBBY = {"mmr": {"value": None, "medal": "Lenda 3"}, "recent": [], "source": "opendota"}


def main():
    c = dota.compose(bridge("game"), True, GSI, Od(LOBBY), Od(LOBBY), True, 1700000000)
    check("Copilot com fase real vence a GSI", c["live"]["gold"] != 1 and c["companion"]["source"] == "copilot")
    check("lobby do Copilot não é trocado pelo OpenDota", c["lobby"] == bridge("game")["lobby"])
    c = dota.compose(bridge("offline"), True, GSI, Od(LOBBY), Od(LOBBY), True, 1700000000)
    check("Copilot offline + GSI = GSI", c["phase"] == "game" and c["companion"]["source"] == "gsi" and c["live"]["gold"] == 1)
    check("GSI: lobby vem do OpenDota", c["lobby"] == LOBBY)
    check("GSI: hero.mine vem do OpenDota", c["hero"]["mine"]["matches"] == 5)
    c = dota.compose(dota.offline(), False, GSI, Od(LOBBY), Od(LOBBY), True, 1700000000)
    check("sem Copilot: GSI", c["phase"] == "game" and c["companion"]["source"] == "gsi" and c.get("reason") is None)
    c = dota.compose(dota.offline(), False, None, Od(LOBBY), Od(LOBBY), True, 1700000000)
    check("sem Copilot e sem GSI, Dota aberto = menu pelo processo", c["phase"] == "menu" and c["companion"]["menu_from_process"] and c["companion"]["source"] == "process" and "reason" not in c)
    check("menu pelo processo mostra o lobby do OpenDota", c["lobby"] == LOBBY)
    check("dota_started repassado", c["companion"]["dota_started"] == 1700000000 and c["companion"]["dota_running"] is True)
    c = dota.compose(dota.offline(), False, None, Od(LOBBY), Od(LOBBY), False, None)
    check("tudo fechado = offline sem motivo (a tela diz 'Dota fechado')", c["phase"] == "offline" and c["reason"] is None and c["companion"]["source"] is None)
    check("fechado ainda mostra o lobby copiado do OpenDota", c["lobby"] == LOBBY)
    c = dota.compose(dota.offline(), False, None, None, None, False, None)
    check("sem OpenDota: lobby null, nada inventado", c["lobby"] is None)
    c = dota.compose(dota.with_process(json.loads((FIX / "dota-offline.json").read_text(encoding="utf-8")), True, True, 5), True, None, Od(None), Od(None), True, 5)
    check("Copilot no ar + Dota aberto sem GSI = menu pelo processo do Copilot", c["phase"] == "menu" and c["companion"]["menu_from_process"] and c["companion"]["source"] == "copilot")
    c = dota.compose(bridge("offline"), True, None, Od(None), Od(None), False, None)
    check("Copilot no ar, Dota fechado = offline com o lobby dele", c["phase"] == "offline" and c["lobby"] is not None)
    # resultado oficial
    post = {**GSI, "phase": "postgame", "live": None, "result": {"hero_id": 74, "win": True, "k": 1, "d": 2, "a": 3, "match_id": 77}}
    od = Od(LOBBY)
    od.official = {"match_id": 77, "win": False, "k": 8, "d": 4, "a": 11, "duration_s": 2100, "gpm": 600, "xpm": 700, "last_hits": 250, "hero_damage": 31000, "source": "opendota"}
    c = dota.compose(dota.offline(), False, post, od, od, True, 5)
    check("resultado oficial acompanha o da GSI (não o substitui)", c["result"]["win"] is True and c["result"]["official"]["win"] is False and c["result"]["official"]["gpm"] == 600)
    od.official["match_id"] = 78
    check("oficial de outra partida não entra", "official" not in dota.compose(dota.offline(), False, post, od, od, True, 5)["result"])
    check("compose não altera as entradas", GSI["hero"]["mine"] is None and "companion" not in GSI)

    # ---- imagens do CDN no proxy
    class Resp:
        def __init__(self, b):
            self.b = b
            self.headers = type("H", (), {"get_content_type": lambda s: "image/png"})()

        def read(self, n):
            return self.b[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    png = dota_img.PNG + b"x" * 20

    def bridge_none(req, timeout):
        raise urllib.error.URLError("sem ponte")
    seen = []

    def cdn_open(req, timeout):
        seen.append(req.full_url)
        if "boom" in req.full_url:
            raise urllib.error.URLError("x")
        return Resp(b"<html>" if "html" in req.full_url else png)

    paths = {("portrait", 1): "/apps/dota2/images/dota_react/heroes/antimage.png", ("item", 44): "/apps/dota2/images/dota_react/items/tango.png",
             ("icon", 2): "/apps/dota2/images/dota_react/heroes/boom.png", ("loadout", 3): "/apps/dota2/images/dota_react/heroes/html.png",
             ("portrait", 4): "https://evil.example/x.png", ("portrait", 5): "/apps/dota2/images/../x.png"}
    t = [0.0]
    with tempfile.TemporaryDirectory() as d:
        def bridge_down(req, timeout):
            raise urllib.error.URLError("fora")
        px = dota_img.ImageProxy(d, lambda: "tok", lambda: "http://127.0.0.1:1/companion/v1/state", opener=bridge_down, clock=lambda: t[0],
                                 cdn_path=lambda k, i: paths.get((k, i)), cdn_opener=cdn_open)
        k = px.key()
        r = px.handle(f"/dota/img/hero/1?kind=portrait&k={k}")
        check("ponte fora do ar + id conhecido = imagem do CDN da Valve", r[0] == 200 and r[2] == png and seen == [dota_img.CDN_HOST + "/apps/dota2/images/dota_react/heroes/antimage.png"], str(seen))
        n = len(seen)
        r = px.handle(f"/dota/img/hero/1?kind=portrait&k={k}")
        check("segunda vez sai do disco (sem nova busca)", r[0] == 200 and len(seen) == n)
        check("item pelo CDN", px.handle(f"/dota/img/item/44?k={k}")[0] == 200)
        t[0] += 11
        check("id desconhecido = erro da ponte (502), sem buscar no CDN", px.handle(f"/dota/img/hero/999?kind=portrait&k={k}")[0] == 502 and len(seen) == n + 1)
        check("caminho fora do CDN/com '..' nunca é buscado", px.handle(f"/dota/img/hero/4?kind=portrait&k={k}")[0] == 502 and px.handle(f"/dota/img/hero/5?kind=portrait&k={k}")[0] == 502 and len(seen) == n + 1)
        check("CDN devolveu HTML = não vira imagem", px.handle(f"/dota/img/hero/3?kind=loadout&k={k}")[0] == 502)
        check("erro de rede no CDN = 502", px.handle(f"/dota/img/hero/2?kind=icon&k={k}")[0] == 502)
        m = len(seen)
        px.handle(f"/dota/img/hero/1?kind=loadout&k={k}")
        check("CDN em recuo (10 s) não é tentado de novo", len(seen) == m)
        check("sem a chave continua 403", px.handle("/dota/img/hero/1?kind=portrait&k=errada")[0] == 403)
        # com a ponte no ar, ela vem primeiro
        px2 = dota_img.ImageProxy(Path(d) / "b", lambda: "tok", lambda: "http://127.0.0.1:1/companion/v1/state", opener=lambda req, timeout: Resp(png),
                                  cdn_path=lambda k, i: paths.get((k, i)), cdn_opener=cdn_open)
        m = len(seen)
        check("ponte no ar: usa a ponte, não o CDN", px2.handle(f"/dota/img/hero/1?kind=portrait&k={px2.key()}")[0] == 200 and len(seen) == m)
    print("\nFALHARAM:", fails) if fails else print("\nTudo certo.")
    return 1 if fails else 0


sys.exit(main())
