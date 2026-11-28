"""Tela Dota na partida: próximos itens, farm (percentis) e meta de last hits (dota_od.OpenDota.coach + dota.compose).
Fixtures falsas tests/fixtures/od-coach-*.json (o benchmark é uma resposta pública real do herói 74); sem rede.
Rodar: py -3 tests/test_dota_coach.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "windows" / "server"))
import dota  # noqa: E402
import dota_od  # noqa: E402

FIX = ROOT / "fixtures"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


def fx(n):
    return json.loads((FIX / f"od-{n}.json").read_text(encoding="utf-8"))


class Net:
    def __init__(self):
        self.calls, self.fail = [], None

    def __call__(self, url):
        path = url[len(dota_od.BASE):]
        self.calls.append(path)
        if self.fail:
            raise dota_od.OdError(self.fail)
        if path == "/constants/items":
            return fx("coach-items")
        if path == "/constants/heroes":
            return {"74": {"id": 74, "localized_name": "Invoker", "img": None, "icon": None}}
        if path == "/heroes/74/itemPopularity":
            return fx("coach-pop")
        if path == "/benchmarks?hero_id=74":
            return fx("coach-bench")
        raise AssertionError(path)


class Clock:
    t = 1795200000.0

    def __call__(self):
        return self.t


def mk(d, steam=""):
    net, clock = Net(), Clock()
    return dota_od.OpenDota(Path(d, "dota-od.json"), lambda: steam, net, clock), net, clock


def live(clock_s=700, gold=1000, gpm=500, xpm=600, lh=60, items=()):
    return {"clock_s": clock_s, "gold": gold, "gpm": gpm, "xpm": xpm, "last_hits": lh,
            "items_detail": [{"item": "x", "item_id": i, "slot": n} for n, i in enumerate(items)]}


def main():
    pop = dota_od.trim_pop(fx("coach-pop"))
    check("trim_pop: 4 fases ordenadas por vezes", list(pop) == ["start", "early", "mid", "late"] and pop["mid"][0] == [1, 60] and pop["early"][0] == [63, 50])
    bn = dota_od.trim_bench(fx("coach-bench"))
    check("trim_bench: gpm/xpm/lh com 11 pontos crescentes", set(bn) == {"gpm", "xpm", "lh"} and len(bn["gpm"]) == 11 and bn["gpm"][4] == [0.5, 461])
    for bad in ({}, {"result": {}}, {"result": {"gold_per_min": [{"percentile": 0.5, "value": True}]}}):
        try:
            dota_od.trim_bench(bad)
            check("trim_bench recusa formato errado", False)
        except dota_od.OdError:
            check("trim_bench recusa formato errado", True)
    try:
        dota_od.trim_pop({"start_game_items": {}})
        check("trim_pop recusa fase faltando", False)
    except dota_od.OdError:
        check("trim_pop recusa fase faltando", True)

    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        check("sem herói da partida: nada a ler de heróis (e sem Steam ID só as constantes)", od.needs(True) == [("consts", "heroes"), ("consts", "items")])
        od.want_hero(74)
        check("com herói: itemPopularity e benchmarks entram, sem precisar de Steam ID", od.needs(True)[2:] == [("pop", "74"), ("bench", "74")])
        check("sem aparelho nada é lido", od.needs(False) == [])
        check("coach sem cópia = None", od.coach(74, live()) is None)
        check("refresh lê constantes + 2 por herói, só GET", od.refresh() and sorted(net.calls) == sorted(["/constants/heroes", "/constants/items", "/heroes/74/itemPopularity", "/benchmarks?hero_id=74"]), str(net.calls))
        check("segunda vez dentro do TTL: nada vence", od.needs(True) == [] and not od.due(True))
        clock.t += dota_od.HERO_TTL_S + 1
        check("depois de 24 h só os dados do herói vencem", od.needs(True) == [("pop", "74"), ("bench", "74")])
        od.want_hero(None)
        check("herói fora da partida: não relê", od.needs(True) == [])
        od.want_hero(True)
        check("want_hero recusa bool", od.want is None)

        # ---- próximos itens
        od.want_hero(74)
        c = od.coach(74, live(clock_s=700, gold=1000))
        it = c["items"]
        check("700 s = fase mid (corte 10–25 min)", it["phase"] == "mid")
        check("até 4 itens, mais comprados primeiro, com custo e quanto falta",
              [(r["id"], r["name"], r["cost"], r["missing"]) for r in it["list"]] == [(1, "Blink Dagger", 2250, 1250), (116, "Black King Bar", 3975, 2975), (100, "Eul", 2625, 1625), (147, "Manta Style", 4600, 3600)],
              str(it["list"]))
        check("ouro de sobra = 0 faltando", od.coach(74, live(gold=9999))["items"]["list"][0]["missing"] == 0)
        check("sem ouro conhecido = missing None (nada inventado)", od.coach(74, live(gold=None))["items"]["list"][0]["missing"] is None)
        # compra exata
        c = od.coach(74, live(items=(1,)))["items"]["list"]
        check("item que já tenho (por id) não aparece", 1 not in [r["id"] for r in c])
        # componentes: tenho o Power Treads -> Boots/Gloves/Belt não são "falta"
        c = od.coach(74, live(clock_s=300, items=(63,)))["items"]
        check("early: tenho Power Treads, então Boots (componente) e o próprio PT não aparecem", c["phase"] == "early" and 29 not in [r["id"] for r in c["list"]] and 63 not in [r["id"] for r in c["list"]], str(c["list"]))
        check("early sem itens: Power Treads vem primeiro", od.coach(74, live(clock_s=300))["items"]["list"][0]["id"] == 63)
        # BKB tem ogre_axe + mithril + recipe
        c = od.coach(74, live(clock_s=700, items=(116,)))["items"]["list"]
        ids = [r["id"] for r in c]
        check("tenho BKB: ele, o Ogre Axe e o Mithril Hammer não aparecem como falta", not ({116, 68, 34} & set(ids)), str(ids))
        check("recipe_ nunca aparece", 120 not in [r["id"] for r in od.coach(74, live(clock_s=300))["items"]["list"]])
        check("fase seguinte completa quando faltam itens (late)", [r["phase"] for r in od.coach(74, live(clock_s=2000))["items"]["list"]][0] == "late")
        check("item que a fase atual não tem continua na próxima fase, sem repetir", len({r["id"] for r in od.coach(74, live(clock_s=700, items=(1, 116, 100)))["items"]["list"]}) == len(od.coach(74, live(clock_s=700, items=(1, 116, 100)))["items"]["list"]))
        check("item sem item_id (nome só) nunca é adivinhado", od.coach(74, {**live(), "items_detail": [{"item": "Blink Dagger", "item_id": None, "slot": 0}]})["items"]["list"][0]["id"] == 1)
        check("pré-horn (clock <= 0) = start", od.coach(74, live(clock_s=-30))["items"]["phase"] == "start")

        # ---- farm
        check("antes de 5 min = cedo demais, sem medidores", od.coach(74, live(clock_s=240))["farm"] == {"early": True, "from": 300})
        f = od.coach(74, live(clock_s=600, gpm=470, xpm=700, lh=30))["farm"]
        m = {x["k"]: x for x in f["meters"]}
        check("GPM 470 (>= p50=461, < p60=498) = melhor que 50%, amarelo", m["gpm"]["pct"] == 50 and m["gpm"]["tone"] == "mid", str(m["gpm"]))
        check("LH/min = last hits ÷ minutos (3,0) alcança p30 (2,95) = 30%, amarelo", m["lh"]["v"] == 3.0 and m["lh"]["pct"] == 30 and m["lh"]["tone"] == "mid", str(m["lh"]))
        check("valor abaixo do p10 = 0%, vermelho", od.coach(74, live(clock_s=600, gpm=100))["farm"]["meters"][0]["pct"] == 0 and od.coach(74, live(clock_s=600, gpm=100))["farm"]["meters"][0]["tone"] == "lo")
        check("exatamente no ponto vale o ponto (>=)", od.coach(74, live(clock_s=600, gpm=498))["farm"]["meters"][0]["pct"] == 60)
        g = {x["pct"]: x["tone"] for x in (od.coach(74, live(clock_s=600, gpm=v))["farm"]["meters"][0] for v in (297, 348, 388, 424, 461, 498, 536, 580, 634, 680, 769))}
        check("tons: <30 vermelho; 30–60 amarelo; >60 verde", g == {10: "lo", 20: "lo", 30: "mid", 40: "mid", 50: "mid", 60: "mid", 70: "ok", 80: "ok", 90: "ok", 95: "ok", 99: "ok"}, str(g))
        check("campo sem valor não vira medidor", [x["k"] for x in od.coach(74, live(clock_s=600, xpm=None))["farm"]["meters"]] == ["gpm", "lh"])

        # ---- meta
        gl = od.coach(74, live(clock_s=600, lh=30))["goal"]
        check("meta: mediana (p50) em LH/min, quanto precisaria agora e progresso, sem valor aos 10 min", gl["median_lpm"] == 4.19 and gl["need"] == 42 and gl["have"] == 30 and gl["pct"] == 72 and gl["my_lpm"] == 3.0, str(gl))
        check("meta passa de 100% = limita em 100", od.coach(74, live(clock_s=600, lh=400))["goal"]["pct"] == 100)
        check("sem last hits = sem meta", od.coach(74, live(clock_s=600, lh=None))["goal"] is None)
        check("antes de 5 min não há meta", od.coach(74, live(clock_s=200))["goal"] is None)

        # ---- compose
        game = {"v": 1, "phase": "game", "lobby": None, "hero": {"hero_id": 74, "hero": "Invoker", "mine": None}, "live": live(700), "result": None}
        out = dota.compose(None, False, game, od, od, True, 1795100000)
        check("compose põe live.coach na partida", out["live"].get("coach") and out["live"]["coach"]["items"]["phase"] == "mid")
        check("compose não mexe nos outros campos de live", out["live"]["gpm"] == 500)
        menu = {**game, "phase": "menu", "live": None, "hero": None}
        check("fora da partida não há coach", "live" in dota.compose(None, False, menu, od, od, True, 1795100000) and not (dota.compose(None, False, menu, od, od, True, 1795100000)["live"] or {}).get("coach"))
        pre = {**game, "phase": "pregame"}
        check("pré-jogo também não mostra o bloco", "coach" not in (dota.compose(None, False, pre, od, od, True, 1795100000)["live"] or {}))
        out2 = dota.compose(None, False, game, None, None, True, 1795100000)
        check("sem OpenDota (None) = sem coach e sem erro", "coach" not in out2["live"])
        ssn = json.dumps(out)
        check("nada de segredo no estado", "token" not in ssn.lower() and "steam" not in ssn.lower())

        # cópia sem componentes (cache antigo) é relida uma vez
        od.data["consts"]["items"]["d"] = {k: {kk: vv for kk, vv in v.items() if kk != "c"} for k, v in od.data["consts"]["items"]["d"].items()}
        check("catálogo antigo sem componentes é relido", ("consts", "items") in od.needs(True))
        net.calls.clear()
        od.refresh()
        check("relê o catálogo (e, como o TTL do herói já tinha vencido, os dados do herói)", net.calls[0] == "/constants/items" and len(net.calls) == 3, str(net.calls))

        # erro de rede mantém a última cópia
        clock.t += dota_od.HERO_TTL_S + 1
        od.want_hero(74)
        net.fail = "rede"
        od.refresh()
        check("rede caiu: coach continua com a última cópia", od.coach(74, live(700))["items"] is not None and od.error == "rede")

    with tempfile.TemporaryDirectory() as d:
        od, net, clock = mk(d)
        od.want_hero(74)
        od.refresh()
        od2 = dota_od.OpenDota(Path(d, "dota-od.json"), lambda: "", Net(), clock)
        check("cache em disco reaberto responde sem rede", od2.coach(74, live(700))["items"]["phase"] == "mid")
        for h in range(100, 100 + dota_od.HERO_KEEP + 5):
            od2.data["pop"][str(h)] = {"at": h, "d": {}}
        od2.want_hero(74)
        clock.t += dota_od.HERO_TTL_S + 1
        od2.refresh()
        check("no máximo HERO_KEEP heróis no disco", len(od2.data["pop"]) <= dota_od.HERO_KEEP)

    print("\nFALHARAM:", fails) if fails else print("\nTudo certo.")
    return 1 if fails else 0


sys.exit(main())
