"""Testes da previsão do tempo (weather.py) com fixtures JSON reais do Open-Meteo, sem rede.
Rodar: py -3 tests/test_weather.py"""
import json
import logging
import re
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "server"))
import weather  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
FORECAST = json.loads((FIX / "weather-forecast.json").read_text(encoding="utf-8"))
GEOCODE = json.loads((FIX / "weather-geocode.json").read_text(encoding="utf-8"))
ICONS = (ROOT / "windows" / "server" / "web" / "icons" / "lucide.svg").read_text(encoding="utf-8")
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


# ---- tabela WMO (dia/noite)
check("0 de dia = sol", weather.describe(0, 1) == ("Céu limpo", "sun"))
check("0 de noite = lua", weather.describe(0, 0) == ("Céu limpo", "moon"))
check("2 de dia/noite", weather.describe(2, 1)[1] == "cloud-sun" and weather.describe(2, 0)[1] == "cloud-moon")
check("3 nublado igual de dia e de noite", weather.describe(3, 1) == weather.describe(3, 0) == ("Nublado", "cloud"))
check("chuva, neve, trovoada", weather.describe(63)[1] == "cloud-rain" and weather.describe(73)[1] == "cloud-snow" and weather.describe(95)[1] == "cloud-lightning")
check("garoa e neblina", weather.describe(53)[1] == "cloud-drizzle" and weather.describe(45)[1] == "cloud-fog")
check("código desconhecido = nada inventado", weather.describe(42) == (None, None) and weather.describe(None) == (None, None) and weather.describe("3") == (None, None)
      and weather.describe(True) == (None, None))
missing = [ic for _, d, n in weather.WMO.values() for ic in (d, n) if f'id="ic-{ic}"' not in ICONS]
check("todos os ícones da tabela existem no lucide.svg", not missing, str(sorted(set(missing))))
check("tabela só com textos em português preenchidos", all(t and isinstance(t, str) for t, _, _ in weather.WMO.values()))

# ---- parse_forecast com a resposta real
f = weather.parse_forecast(FORECAST)
c = f["current"]
check("current: temperatura, sensação, umidade, vento, código", all(k in c for k in ("temp", "feels", "humidity", "wind", "code", "is_day", "time", "text", "icon")), str(c))
check("current bate com a fixture", c["temp"] == FORECAST["current"]["temperature_2m"] and c["feels"] == FORECAST["current"]["apparent_temperature"]
      and c["humidity"] == FORECAST["current"]["relative_humidity_2m"] and c["time"] == FORECAST["current"]["time"])
check("current: texto e ícone do código", (c["text"], c["icon"]) == weather.describe(FORECAST["current"]["weather_code"], FORECAST["current"]["is_day"]))
check("24 horas", len(f["hourly"]) == 24 and f["hourly"][0]["t"] == FORECAST["hourly"]["time"][0])
check("hora: temperatura, chance de chuva, ícone", all({"t", "temp", "pop", "icon"} <= set(h) for h in f["hourly"]))
check("hora de noite usa lua", any(h.get("is_day") == 0 and h["icon"] in ("moon", "cloud-moon") for h in f["hourly"] if h["icon"] in ("sun", "moon", "cloud-sun", "cloud-moon")))
check("7 dias", len(f["daily"]) == 7)
d0 = f["daily"][0]
check("dia: máx, mín, chuva, nascer e pôr do sol", d0["max"] == FORECAST["daily"]["temperature_2m_max"][0] and d0["min"] == FORECAST["daily"]["temperature_2m_min"][0]
      and d0["pop"] == FORECAST["daily"]["precipitation_probability_max"][0] and d0["sunrise"] == FORECAST["daily"]["sunrise"][0]
      and d0["sunset"] == FORECAST["daily"]["sunset"][0], str(d0))
check("unidades", f["units"] == {"temp": "°C", "wind": "km/h"})
json.dumps(f)

# campos ausentes ficam de fora; lixo não vira dado
partial = {"current": {"time": "2026-01-01T10:00", "temperature_2m": 20.5, "weather_code": 999}, "hourly": {"time": ["2026-01-01T10:00"]},
           "daily": {"time": ["2026-01-01"], "temperature_2m_max": [30.0]}}
p = weather.parse_forecast(partial)
check("ausente = escondido (sem sensação, umidade, vento)", p["current"] == {"time": "2026-01-01T10:00", "temp": 20.5, "is_day": 1, "code": 999}, str(p["current"]))
check("código desconhecido sem texto/ícone", "text" not in p["current"] and "icon" not in p["current"])
check("hora/dia parciais", p["hourly"] == [{"t": "2026-01-01T10:00"}] and p["daily"] == [{"d": "2026-01-01", "max": 30.0}])
check("valores não numéricos descartados", "temp" not in weather.parse_forecast({"current": {"time": "x", "temperature_2m": "20", "relative_humidity_2m": True}})["current"])
for bad in (None, [], "x", {}, {"error": True, "reason": "x"}):
    try:
        weather.parse_forecast(bad)
        check("resposta inválida levanta erro: " + repr(bad)[:20], False)
    except ValueError:
        check("resposta inválida levanta erro: " + repr(bad)[:20], True)
big = {"current": {"time": "t"}, "hourly": {"time": [f"2026-01-01T{i % 24:02d}:00" for i in range(60)]}, "daily": {"time": [f"d{i}" for i in range(14)]}}
pb = weather.parse_forecast(big)
check("limita a 24 horas e 7 dias", len(pb["hourly"]) == 24 and len(pb["daily"]) == 7)

# ---- geocodificação
r = weather.parse_search(GEOCODE)
check("busca: até 5 resultados com nome, estado, país e coordenadas", 1 <= len(r) <= 5 and all({"name", "lat", "lon"} <= set(x) for x in r))
check("busca: São Paulo (Brasil) primeiro", r[0]["name"] == "São Paulo" and r[0].get("admin") == "São Paulo" and r[0].get("country") == "Brasil", str(r[0]))
check("busca: sem campos extras (população, cep...)", set(r[0]) <= {"name", "admin", "country", "lat", "lon"})
check("busca sem resultados = lista vazia", weather.parse_search({"generationtime_ms": 1.2}) == [] and weather.parse_search(None) == [])
many = {"results": [{"name": f"C{i}", "latitude": 1.0, "longitude": 2.0} for i in range(9)] + [{"name": "sem coord"}]}
check("busca limita a 5 e descarta item torto", len(weather.parse_search(many)) == 5 and len(weather.parse_search({"results": [{"name": "x"}]})) == 0)

# ---- cidade escolhida
ok_place = weather.clean_place(r[0])
check("clean_place aceita o resultado da busca", ok_place["name"] == "São Paulo" and abs(ok_place["lat"] + 23.5475) < 1e-6)
check("clean_place None = sem cidade", weather.clean_place(None) is None)
for bad in ({}, {"name": "x"}, {"name": "x", "lat": 99, "lon": 0}, {"name": "x", "lat": 0, "lon": 181}, {"name": "", "lat": 0, "lon": 0},
            {"name": "x", "lat": "1", "lon": 0}, {"name": "x" * 81, "lat": 0, "lon": 0}, "São Paulo", {"name": "x", "lat": True, "lon": 0}):
    try:
        weather.clean_place(bad)
        check("clean_place recusa " + repr(bad)[:40], False)
    except ValueError:
        check("clean_place recusa " + repr(bad)[:40], True)

# ---- classe Weather (sem rede)
catcher = LogCatcher()
logging.getLogger("companion.weather").addHandler(catcher)
logging.getLogger("companion.weather").setLevel(logging.INFO)
calls = {"forecast": 0, "search": 0}
net = {"down": False}


def fake_forecast(place, units):
    calls["forecast"] += 1
    if net["down"]:
        raise urllib.error.URLError("sem rede")
    return FORECAST


def fake_search(q):
    calls["search"] += 1
    if net["down"]:
        raise urllib.error.URLError("sem rede")
    return GEOCODE


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    clock = Clock()
    cfg = {"place": None, "units": "metric"}
    w = weather.Weather(base, cfg, clock=clock, search_fn=fake_search, forecast_fn=fake_forecast)
    check("sem cidade: estado só com place None", w.state() == {"place": None})
    check("sem cidade: nada é buscado", not w.due(True) and w.refresh() is False and calls["forecast"] == 0)
    check("sem aparelho: não atualiza", not w.due(False))
    place = weather.clean_place(r[0])
    w.set_place(place)
    check("cidade escolhida: aguardando a primeira busca", w.state().get("pending") is True and w.state()["place"]["name"] == "São Paulo" and "lat" not in w.state()["place"])
    check("com cidade e aparelho: vence na hora", w.due(True) and not w.due(False))
    check("refresh traz a previsão", w.refresh() is True and calls["forecast"] == 1)
    st = w.state()
    check("estado completo para o aparelho", st["updated_at"] == clock.t and st["stale"] is False and len(st["hourly"]) == 24 and len(st["daily"]) == 7 and st["current"]["temp"] == c["temp"])
    check("weather.json gravado", (base / "weather.json").exists() and not (base / "weather.json.tmp").exists())
    check("sem vencer antes de 30 min", not w.due(True))
    clock.t += 29 * 60
    check("29 min: ainda não", not w.due(True))
    clock.t += 90
    check("30 min: vence", w.due(True))
    # sem internet: mantém a última e marca desatualizado
    net["down"] = True
    good_at = st["updated_at"]
    check("erro de rede muda o estado (stale)", w.refresh() is True)
    st2 = w.state()
    check("erro: mantém a previsão e o horário da última boa", st2["stale"] is True and st2["updated_at"] == good_at and st2["current"] == st["current"])
    check("erro: tenta de novo em 5 min (não em 30)", not w.due(True))
    clock.t += 5 * 60
    check("erro: 5 min depois vence", w.due(True))
    check("erro repetido não muda o estado", w.refresh() is False)
    net["down"] = False
    clock.t += 5 * 60
    check("voltou a internet: atualiza e limpa o stale", w.refresh() is True and w.state()["stale"] is False and w.state()["updated_at"] == clock.t)
    # limite do refresh manual
    (base / "x").mkdir()
    w2 = weather.Weather(base / "x", {"place": place, "units": "metric"}, clock=clock, search_fn=fake_search, forecast_fn=fake_forecast)
    n0 = calls["forecast"]
    ch, wait = w2.refresh_manual()
    check("manual: primeira vez busca", ch is True and wait == 0 and calls["forecast"] == n0 + 1)
    clock.t += 10
    ch, wait = w2.refresh_manual()
    check("manual: 10 s depois é recusado (espera ~50 s)", ch is False and 49 <= wait <= 50 and calls["forecast"] == n0 + 1, f"wait={wait}")
    clock.t += 49
    check("manual: 59 s ainda recusado", w2.refresh_manual()[1] > 0)
    clock.t += 2
    ch, wait = w2.refresh_manual()
    check("manual: depois de 60 s busca de novo", wait == 0 and calls["forecast"] == n0 + 2)
    # recarregar do disco
    w3 = weather.Weather(base, cfg, clock=clock, search_fn=fake_search, forecast_fn=fake_forecast)
    check("reiniciar: última previsão volta do disco", w3.state().get("current") == w.state()["current"] and w3.updated_at == w.updated_at)
    cfg4 = {"place": {**place, "name": "Outra", "lat": 1.0, "lon": 1.0}, "units": "metric"}
    w4 = weather.Weather(base, cfg4, clock=clock, search_fn=fake_search, forecast_fn=fake_forecast)
    check("outra cidade no config: previsão guardada é descartada", w4.state().get("pending") is True)
    w5 = weather.Weather(base, {"place": place, "units": "imperial"}, clock=clock, search_fn=fake_search, forecast_fn=fake_forecast)
    check("outra unidade: previsão guardada é descartada", w5.state().get("pending") is True)
    # weather.json corrompido
    (base / "weather.json").write_text("{isso não é json", encoding="utf-8")
    w6 = weather.Weather(base, cfg, clock=clock, search_fn=fake_search, forecast_fn=fake_forecast)
    bad = list(base.glob("weather.json.bad-*"))
    check("corrompido: vira .bad-<data-hora> e começa sem previsão", len(bad) == 1 and not (base / "weather.json").exists() and w6.state().get("pending") is True)
    (base / "weather.json").write_text(json.dumps({"v": 1, "place": place, "updated_at": 1, "data": {"hourly": "x"}}), encoding="utf-8")
    w7 = weather.Weather(base, cfg, clock=clock, search_fn=fake_search, forecast_fn=fake_forecast)
    check("formato errado também é renomeado", len(list(base.glob("weather.json.bad-*"))) >= 1 and w7.data is None)
    # busca
    check("busca devolve resultados", len(w.search("São Paulo")) >= 1 and calls["search"] == 1)
    for q in ("", "   ", None, 5, "x" * 81):
        try:
            w.search(q)
            check("busca recusa " + repr(q)[:12], False)
        except ValueError:
            check("busca recusa " + repr(q)[:12], True)
    net["down"] = True
    try:
        w.search("Rio")
        check("busca sem rede levanta erro claro", False)
    except ValueError as e:
        check("busca sem rede levanta erro claro", "Sem conexão" in str(e), str(e))
    # trocar de cidade apaga a previsão da anterior
    net["down"] = False
    w.set_place(None)
    check("trocar para sem cidade: apaga weather.json e o estado", w.state() == {"place": None} and not (base / "weather.json").exists())

# ---- privacidade do log
log_text = "\n".join(catcher.lines)
check("log tem só 'previsão atualizada/erro'", bool(catcher.lines) and all(l.startswith(("previsão", "busca de cidade", "weather.json")) for l in catcher.lines), log_text[:200])
check("log sem cidade nem coordenadas", "São Paulo" not in log_text and "23.5" not in log_text and "46.6" not in log_text and not re.search(r"-?\d+\.\d{3,}", log_text))

print("\n%s" % ("TUDO OK" if not fails else "FALHAS: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
