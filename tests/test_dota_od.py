"""OpenDota (dota_od.py): conversões, cache em disco, limite de chamadas, recuo, offline com a última cópia, nomes/itens/imagens
e a procura da partida no pós-jogo. Fixtures falsas tests/fixtures/od-*.json; sem rede. Rodar: py -3 tests/test_dota_od.py"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "windows" / "server"))
import dota_od  # noqa: E402

FIX = ROOT / "fixtures"
T0 = 1795200000
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


def fx(n):
    return json.loads((FIX / f"od-{n}.json").read_text(encoding="utf-8"))


class Net:
    """Rede falsa: responde pelo caminho e conta as chamadas."""
    def __init__(self):
        self.calls, self.fail, self.recent, self.gone = [], None, fx("recent"), set()

    def __call__(self, url):
        path = url[len(dota_od.BASE):]
        self.calls.append(path)
        if self.fail:
            raise dota_od.OdError(self.fail, 429 if self.fail == "limite" else None)
        if path == "/players/1":
            return fx("player")
        if path == "/players/1/recentMatches":
            return self.recent
        if path == "/players/1/heroes":
            return fx("heroes")
        if path == "/players/1/wl":
            return fx("wl")
        if path == "/constants/heroes":
            return fx("constants-heroes")
        if path == "/constants/items":
            return fx("constants-items")
        if path.startswith("/matches/"):
            mid = int(path.rsplit("/", 1)[1])
            if mid in self.gone:
                raise dota_od.OdError("http", 404)
            d = fx("match")
            d["match_id"] = mid
            return d
        raise AssertionError(path)


class Clock:
    t = float(T0 + 3000)

    def __call__(self):
        return self.t


def mk(d, steam="1", net=None, clock=None):
    net, clock = net or Net(), clock or Clock()
    return dota_od.OpenDota(Path(d, "dota-od.json"), lambda: steam, net, clock), net, clock


def main():
    check("medalha 53 = Lenda 3", dota_od.medal(53) == "Lenda 3")
    check("medalha 80 = Imortal (sem estrelas)", dota_od.medal(80) == "Imortal")
    check("medalha 11 = Arauto 1; None/0/99 = None", dota_od.medal(11) == "Arauto 1" and dota_od.medal(None) is None and dota_od.medal(0) is None and dota_od.medal(99) is None)
    check("account_id: 64 e 32 bits", dota_od.account_id("76561197960265729") == 1 and dota_od.account_id("1") == 1 and dota_od.account_id("") is None and dota_od.account_id("x") is None)
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        check("sem leitura: lobby None", od.lobby() is None)
        check("status pendente", od.status()["state"] == "pending")
        check("precisa de aparelho para buscar", od.needs(False) == [] and len(od.needs(True)) == 6)
        check("refresh: 6 leituras + 3 partidas (a mais nova primeiro)", od.refresh() and len(net.calls) == 9 and net.calls[6:] == ["/matches/9000000005", "/matches/9000000004", "/matches/9000000003"], str(net.calls))
        check("só GET de caminhos conhecidos (sem /request)", all(c.startswith(("/players/1", "/constants/", "/matches/")) and "/request" not in c for c in net.calls))
        lb = od.lobby()
        check("medalha: nome, tier e estrelas, sem valor de MMR", lb["mmr"] == {"value": None, "estimated": False, "medal": "Lenda 3", "tier": 5, "stars": 3, "leaderboard": None}, str(lb["mmr"]))
        check("perfil (nome e avatar) e vitórias/derrotas", lb["profile"] == {"name": "Jogador Falso", "avatar": "32dbb7d95395f12254ba2d97ff56526a5d6989eb"} and lb["record"] == {"win": 120, "lose": 100})
        r = lb["recent"]
        check("recentes: herói pelo nome", [m["hero"] for m in r] == ["Invoker", "Axe", "Invoker", "Rubick", "Axe"])
        check("V/D pela player_slot", [m["win"] for m in r] == [True, True, True, False, False], str([m["win"] for m in r]))
        check("KDA e duração", (r[0]["k"], r[0]["d"], r[0]["a"], r[0]["duration_s"]) == (12, 2, 15, 2400))
        check("ended_at = início + duração", r[0]["ended_at"] == T0 + 2400)
        check("sequência de vitórias", lb["streak"] == 3)
        check("mais jogados: sem partidas zeradas, taxa", [(h["hero"], h["matches"], h["winrate"]) for h in lb["top_heroes"]] == [("Invoker", 40, 0.55), ("Axe", 10, 0.4)])
        check("hoje conta só o que terminou depois da meia-noite local", lb["today"]["wins"] + lb["today"]["losses"] <= 5)
        check("recentes trazem GPM/XPM/LH/dano e início", (r[0]["gpm"], r[0]["xpm"], r[0]["last_hits"], r[0]["hero_damage"]) == (400, 500, 200, 20000) and r[0]["ended_at"] == T0 + 2400)
        check("itens só das 3 partidas lidas; 0 = vazio; id sem catálogo fica sem nome", [("items" in m) for m in r] == [True, True, True, False, False]
              and [x and x["id"] for x in r[0]["items"]] == [1, 44, None, 9999, None, 1] and r[0]["items"][0]["name"] == "Blink Dagger" and r[0]["items"][3]["name"] is None
              and r[0]["neutral"]["id"] == 44, str(r[0].get("items")))
        check("top_heroes traz last_played", lb["top_heroes"][0]["last_played"] == T0)
        check("ext_url: medalha, estrela e avatar conhecidos", od.ext_url("rank", 5) == dota_od.EXT_HOSTS + "rank_icon_5.png" and od.ext_url("star", 3).endswith("rank_star_3.png")
              and od.ext_url("avatar", "32dbb7d95395f12254ba2d97ff56526a5d6989eb") == "https://avatars.steamstatic.com/32dbb7d95395f12254ba2d97ff56526a5d6989eb_medium.jpg")
        check("ext_url: id fora da faixa ou avatar desconhecido = None", od.ext_url("rank", 9) is None and od.ext_url("star", 6) is None and od.ext_url("star", 0) is None
              and od.ext_url("avatar", "0" * 40) is None and od.ext_url("rank", "5") is None and od.ext_url("hero", 5) is None)
        check("fetched_at e não velho", lb["fetched_at"] == T0 + 3000 and lb["stale"] is False)
        check("hero_mine do top, outro None", od.hero_mine(74) == {"matches": 40, "winrate": 0.55, "last_played": T0, "avg_kda": None} and od.hero_mine(86) is None)
        check("nome do herói e item", od.hero_name(74) == "Invoker" and od.hero_name(5) is None and od.item("item_blink") == {"id": 1, "name": "Blink Dagger", "cost": 2250} and od.item("item_nada") is None)
        check("imagem: só caminho do CDN conhecido", od.img_path("portrait", 1) == "/apps/dota2/images/dota_react/heroes/antimage.png"
              and od.img_path("icon", 1) == "/apps/dota2/images/dota_react/heroes/icons/antimage.png" and od.img_path("item", 1) == "/apps/dota2/images/dota_react/items/blink.png")
        check("URL externa e '..' descartados", od.img_path("portrait", 74) is None and od.img_path("icon", 74) is None)
        check("id desconhecido sem imagem; chave de item inválida fora", od.img_path("portrait", 12345) is None and od.img_path("item", 5) is None)
        check("cache em disco", json.loads(Path(d, "dota-od.json").read_text(encoding="utf-8"))["sec"]["profile"]["d"]["name"] == "Jogador Falso")
        n = len(net.calls)
        clock.t += 100
        check("lote de partidas: no máximo 3 por ciclo (nada de novo antes de 10 min)", not any("/matches/" in c for c in net.calls[9:]) and od.needs() == [])
        clock.t -= 100
        # nada vencido: sem chamadas
        clock.t += 300
        check("dentro dos 10 min: nada a buscar", od.needs() == [] and not od.refresh() and len(net.calls) == n)
        clock.t += 400
        check("depois de 10 min: 4 seções + lote de partidas (constantes não)", len(od.needs()) == 5)
        od.refresh()
        mt = [c for c in net.calls if c.startswith("/matches/")]
        check("2º ciclo lê as 2 que faltam; depois nunca mais", mt[3:] == ["/matches/9000000002", "/matches/9000000001"] and len(mt) == 5, str(mt))
        r2 = od.lobby()["recent"]
        check("agora as 5 partidas têm itens", all("items" in m for m in r2))
        clock.t += 700
        od.refresh()
        check("partida já lida nunca é lida de novo", len([c for c in net.calls if c.startswith("/matches/")]) == 5)
        clock.t += 700
        # reabre do disco = mesma cópia, sem rede
        od2, net2, _ = mk(d, clock=clock)
        check("reabre do disco (itens guardados para sempre)", od2.lobby()["recent"][0]["hero"] == "Invoker" and "items" in od2.lobby()["recent"][0] and net2.calls == [])
        # offline: erro de rede mantém a cópia e marca estale
        net.fail = "rede"
        check("erro: sem mudança, guarda o tipo", not od.refresh() and od.error == "rede")
        check("cópia antiga continua, marcada stale", od.lobby()["recent"][0]["hero"] == "Invoker" and od.lobby()["stale"] is True)
        check("recuo de 60 s depois do erro", od.needs() == [] and od.status()["blocked_s"] > 0)
        calls = len(net.calls)
        od.refresh()
        check("em recuo não chama a rede", len(net.calls) == calls)
        clock.t += 61
        net.fail = "limite"
        od.refresh()
        check("429 = recuo de 5 min", od.error == "limite" and 290 < od.status()["blocked_s"] <= 300)
        clock.t += 301
        net.fail = None
        check("volta a funcionar e limpa o erro", od.refresh() and od.error is None and od.lobby()["stale"] is False)
        # steam id trocado esquece a conta anterior
        who = ["1"]
        od3 = dota_od.OpenDota(Path(d, "dota-od.json"), lambda: who[0], net, clock)
        who[0] = ""
        check("sem Steam ID: sem lobby e status no_id; constantes ficam", od3.lobby() is None and od3.status()["state"] == "no_id" and od3.hero_name(74) == "Invoker")
        check("sem Steam ID só busca constantes vencidas (nenhuma)", od3.needs() == [])
    # partida que o OpenDota não tem (404): conta a tentativa, as outras seguem e não há recuo
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        net.gone = {9000000004}
        od.refresh()
        r = od.lobby()["recent"]
        check("404 numa partida: sem itens nela, as outras vêm, sem recuo geral", "items" not in r[1] and "items" in r[0] and "items" in r[2] and od.status()["blocked_s"] == 0 and od.error is None)
        for _ in range(4):
            clock.t += 700
            od.refresh()
        check("404: desiste depois de 3 tentativas", len([c for c in net.calls if c == "/matches/9000000004"]) == dota_od.MATCH_TRIES)
    # outra conta esquece os itens guardados
    with tempfile.TemporaryDirectory() as d:
        who = ["1"]
        net, clock = Net(), Clock()
        od = dota_od.OpenDota(Path(d, "dota-od.json"), lambda: who[0], net, clock)
        od.refresh()
        who[0] = "2"
        od._sync_account()
        check("trocar o Steam ID apaga as partidas guardadas", od.data["matches"] == {} and od.lobby() is None)
    # limite diário
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        od.data["calls"] = {"day": od._day(), "n": dota_od.DAILY_MAX}
        check("cota diária cheia: não chama", not od.refresh() and net.calls == [] and od.error == "cota")
        clock.t += 86400 * 2
        check("dia seguinte zera", od.calls_today() == 0)
    # arquivo corrompido
    with tempfile.TemporaryDirectory() as d:
        Path(d, "dota-od.json").write_text("{nao", encoding="utf-8")
        od, net, clock = mk(d)
        check("corrompido vira .bad e recomeça", any(f.name.startswith("dota-od.json.bad-") for f in Path(d).iterdir()) and od.lobby() is None)
    # resposta ruim
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        net.recent = {"x": 1}
        od.refresh()
        check("recentMatches em formato errado = erro, sem lixo no cache", od.error == "formato" and "recent" not in od.data["sec"])
    # perfil privado / vazio
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        net.recent = []
        od.data["sec"]["profile"] = {"at": int(clock.t), "d": {"name": None, "rank_tier": None, "leaderboard": None, "has_profile": False}}
        od.refresh()
        check("sem perfil público nem partidas = empty", od.status()["state"] == "empty")
    # partida observada no pós-jogo
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        od.refresh()
        net.recent = [dict(m) for m in fx("recent")]
        net.recent.insert(0, {"match_id": 9000000006, "player_slot": 1, "radiant_win": False, "hero_id": 74, "start_time": T0 + 2500, "duration": 2100,
                              "kills": 8, "deaths": 4, "assists": 11, "gold_per_min": 600, "xp_per_min": 700, "hero_damage": 31000, "last_hits": 250})
        od.watch_match(9000000006)
        check("watch: ainda sem resultado oficial", od.official is None and od.watch is not None)
        clock.t += 31
        check("watch pede só recentMatches (dentro do TTL das outras)", od.needs() == [("sec", "recent")])
        od.refresh()
        check("achou a partida na leitura de 30 s", od.official is not None and od.official["match_id"] == 9000000006)
        check("resultado oficial: derrota (slot 1 radiante, dire ganhou)", od.official["win"] is False and (od.official["k"], od.official["d"], od.official["a"]) == (8, 4, 11)
              and od.official["gpm"] == 600 and od.official["source"] == "opendota")
        check("achou = para de observar", od.watch is None)
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        od.refresh()
        od.watch_match(9000000099)
        counts = []
        for _ in range(30):
            clock.t += 31
            n = len(net.calls)
            od.refresh()
            counts.append(len(net.calls) - n)
            if od.watch is None:
                break
        total = sum(1 for c in net.calls if c.endswith("recentMatches"))
        check("não achou: para em 10 min / 20 chamadas", od.watch is None and od.official is None and total <= 1 + dota_od.WATCH_MAX_CALLS, f"{total} leituras de recent")
    # manual
    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        od.refresh()
        ok, wait = od.refresh_manual()
        check("manual: primeira vez vale", ok and wait == 0)
        ok, wait = od.refresh_manual()
        check("manual: 2ª em menos de 60 s é recusada", not ok and wait > 0)
        clock.t += 61
        check("manual: depois de 60 s vale", od.refresh_manual()[1] == 0)


main()
print("\nFALHARAM:", fails) if fails else print("\nTudo certo.")
sys.exit(1 if fails else 0)
