"""Previsão do tempo (Open-Meteo, sem chave): geocodificação por nome e previsão de 24 h / 7 dias.

Roda na fila de disco do servidor (IO_POOL, uma thread): urllib da biblioteca padrão, timeout de 5 s.
A cidade é só a que o usuário escolheu (config.json "weather": {"place": {...} ou null, "units": "metric"});
sem cidade nada é buscado, e nunca há localização automática. A última resposta fica em weather.json
(gravação atômica; arquivo corrompido vira weather.json.bad-<data-hora>). Sem internet: mantém a última e
marca "desatualizado" (o aparelho mostra desde quando). No log só "previsão atualizada" / "previsão: erro":
nunca a cidade, as coordenadas nem a resposta.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("companion.weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 5
MAX_BYTES = 512 * 1024
REFRESH_EVERY_S = 30 * 60      # com aparelho conectado
RETRY_AFTER_ERROR_S = 5 * 60   # sem internet: tenta de novo mais cedo, mantendo a última resposta
MANUAL_MIN_S = 60              # weather.refresh: no máximo 1 a cada 60 s
DEFAULT_CFG = {"place": None, "units": "metric"}
UNITS = ("metric", "imperial")

# Tabela única dos códigos WMO (weather_code do Open-Meteo): código -> (texto, ícone do dia, ícone da noite).
# Os ícones são nomes do Lucide (web/icons/lucide.svg, símbolo "ic-<nome>").
WMO = {
    0: ("Céu limpo", "sun", "moon"),
    1: ("Quase limpo", "cloud-sun", "cloud-moon"),
    2: ("Parcialmente nublado", "cloud-sun", "cloud-moon"),
    3: ("Nublado", "cloud", "cloud"),
    45: ("Neblina", "cloud-fog", "cloud-fog"),
    48: ("Neblina com geada", "cloud-fog", "cloud-fog"),
    51: ("Garoa fraca", "cloud-drizzle", "cloud-drizzle"),
    53: ("Garoa", "cloud-drizzle", "cloud-drizzle"),
    55: ("Garoa forte", "cloud-drizzle", "cloud-drizzle"),
    56: ("Garoa congelante", "cloud-drizzle", "cloud-drizzle"),
    57: ("Garoa congelante forte", "cloud-drizzle", "cloud-drizzle"),
    61: ("Chuva fraca", "cloud-rain", "cloud-rain"),
    63: ("Chuva", "cloud-rain", "cloud-rain"),
    65: ("Chuva forte", "cloud-rain", "cloud-rain"),
    66: ("Chuva congelante", "cloud-rain", "cloud-rain"),
    67: ("Chuva congelante forte", "cloud-rain", "cloud-rain"),
    71: ("Neve fraca", "cloud-snow", "cloud-snow"),
    73: ("Neve", "cloud-snow", "cloud-snow"),
    75: ("Neve forte", "cloud-snow", "cloud-snow"),
    77: ("Grãos de neve", "cloud-snow", "cloud-snow"),
    80: ("Pancadas de chuva fracas", "cloud-rain", "cloud-rain"),
    81: ("Pancadas de chuva", "cloud-rain", "cloud-rain"),
    82: ("Pancadas de chuva fortes", "cloud-rain", "cloud-rain"),
    85: ("Pancadas de neve", "cloud-snow", "cloud-snow"),
    86: ("Pancadas de neve fortes", "cloud-snow", "cloud-snow"),
    95: ("Trovoada", "cloud-lightning", "cloud-lightning"),
    96: ("Trovoada com granizo", "cloud-lightning", "cloud-lightning"),
    99: ("Trovoada com granizo forte", "cloud-lightning", "cloud-lightning"),
}


def describe(code, is_day=1):
    """(texto, ícone) do código WMO, de dia ou de noite; (None, None) se o código é desconhecido (nada inventado)."""
    if isinstance(code, bool) or not isinstance(code, (int, float)) or int(code) != code or int(code) not in WMO:
        return None, None
    text, day, night = WMO[int(code)]
    return text, (day if is_day else night)


def _num(v, nd=1):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return round(float(v), nd) if nd else int(round(v))


def _put(d, **kw):
    """Só os campos que existem: dado ausente fica de fora (o app esconde, nunca inventa)."""
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


def _col(block, name, i):
    arr = block.get(name) if isinstance(block, dict) else None
    return arr[i] if isinstance(arr, list) and i < len(arr) else None


def parse_forecast(raw):
    """Resposta do Open-Meteo -> estado compacto {current, hourly (até 24), daily (até 7), units}.
    Levanta ValueError se não for uma previsão (sem 'current' nem 'daily')."""
    if not isinstance(raw, dict) or not isinstance(raw.get("current"), dict) and not isinstance(raw.get("daily"), dict):
        raise ValueError("resposta de previsão inválida")
    cu = raw.get("current_units") if isinstance(raw.get("current_units"), dict) else {}
    out = {"units": {"temp": cu.get("temperature_2m") if isinstance(cu.get("temperature_2m"), str) else "°C",
                     "wind": cu.get("wind_speed_10m") if isinstance(cu.get("wind_speed_10m"), str) else "km/h"}}
    c = raw.get("current")
    if isinstance(c, dict) and isinstance(c.get("time"), str):
        day = c.get("is_day", 1)
        text, icon = describe(c.get("weather_code"), 0 if day == 0 else 1)
        out["current"] = _put({"time": c["time"]}, temp=_num(c.get("temperature_2m")), feels=_num(c.get("apparent_temperature")),
                              humidity=_num(c.get("relative_humidity_2m"), 0), wind=_num(c.get("wind_speed_10m")),
                              code=_num(c.get("weather_code"), 0), is_day=1 if day != 0 else 0, text=text, icon=icon)
    h = raw.get("hourly")
    hours = []
    if isinstance(h, dict) and isinstance(h.get("time"), list):
        for i, t in enumerate(h["time"][:24]):
            if not isinstance(t, str):
                continue
            day = _col(h, "is_day", i)
            text, icon = describe(_col(h, "weather_code", i), 0 if day == 0 else 1)
            hours.append(_put({"t": t}, temp=_num(_col(h, "temperature_2m", i)), pop=_num(_col(h, "precipitation_probability", i), 0),
                              is_day=None if day is None else (0 if day == 0 else 1), text=text, icon=icon))
    out["hourly"] = hours
    d = raw.get("daily")
    days = []
    if isinstance(d, dict) and isinstance(d.get("time"), list):
        for i, t in enumerate(d["time"][:7]):
            if not isinstance(t, str):
                continue
            text, icon = describe(_col(d, "weather_code", i), 1)
            sr, ss = _col(d, "sunrise", i), _col(d, "sunset", i)
            days.append(_put({"d": t}, max=_num(_col(d, "temperature_2m_max", i)), min=_num(_col(d, "temperature_2m_min", i)),
                             pop=_num(_col(d, "precipitation_probability_max", i), 0),
                             sunrise=sr if isinstance(sr, str) else None, sunset=ss if isinstance(ss, str) else None, text=text, icon=icon))
    out["daily"] = days
    return out


def parse_search(raw):
    """Resposta da geocodificação -> até 5 {name, admin, country, lat, lon}. Sem resultados = lista vazia."""
    res = raw.get("results") if isinstance(raw, dict) else None
    out = []
    for r in res if isinstance(res, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("name"), str):
            continue
        lat, lon = r.get("latitude"), r.get("longitude")
        if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        out.append(_put({"name": r["name"], "lat": round(float(lat), 4), "lon": round(float(lon), 4)},
                        admin=r.get("admin1") if isinstance(r.get("admin1"), str) else None,
                        country=r.get("country") if isinstance(r.get("country"), str) else None))
        if len(out) == 5:
            break
    return out


def clean_place(p):
    """Valida a cidade escolhida (um resultado de weather.search). None = sem cidade. ValueError se inválida."""
    if p is None:
        return None
    if not isinstance(p, dict):
        raise ValueError("cidade inválida")
    name = p.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("cidade inválida")
    out = {"name": name.strip()}
    for k in ("admin", "country"):
        v = p.get(k)
        if v is not None:
            if not isinstance(v, str) or len(v) > 80:
                raise ValueError("cidade inválida")
            if v.strip():
                out[k] = v.strip()
    for k, lim in (("lat", 90), ("lon", 180)):
        v = p.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -lim <= v <= lim:
            raise ValueError("cidade inválida")
        out[k] = round(float(v), 4)
    return out


def _http_json(url):
    """GET JSON com timeout curto (urllib padrão, sem proxy próprio nem tentativa de contorno)."""
    req = urllib.request.Request(url, headers={"User-Agent": "companion-pc", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        body = r.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("resposta grande demais")
    return json.loads(body.decode("utf-8"))


def fetch_search(q):
    qs = urllib.parse.urlencode({"name": q, "count": 5, "language": "pt", "format": "json"})
    return _http_json(f"{GEOCODE_URL}?{qs}")


def fetch_forecast(place, units="metric"):
    qs = {"latitude": place["lat"], "longitude": place["lon"],
          "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
          "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
          "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
          "timezone": "auto", "forecast_days": 7, "forecast_hours": 24}
    if units == "imperial":
        qs.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
    return _http_json(f"{FORECAST_URL}?{urllib.parse.urlencode(qs)}")


class Weather:
    """Estado da previsão. Tudo roda na fila de disco (uma thread): sem locks. Os relógios e os acessos à rede
    são injetáveis (testes sem rede)."""

    def __init__(self, base, cfg=None, clock=time.time, search_fn=None, forecast_fn=None):
        self.path = Path(base) / "weather.json"
        self.cfg = cfg if isinstance(cfg, dict) else dict(DEFAULT_CFG)
        self.clock = clock
        self.search_fn = search_fn or fetch_search
        self.forecast_fn = forecast_fn or fetch_forecast
        self.data = None            # parse_forecast() da última resposta boa
        self.updated_at = None      # epoch dessa resposta
        self.stale = False          # a última tentativa falhou: a previsão é a anterior
        self.last_try = 0.0         # epoch da última tentativa (boa ou não)
        self.last_ok = False
        self.last_manual = 0.0
        self._load()

    # --- configuração
    @property
    def place(self):
        try:
            return clean_place(self.cfg.get("place"))
        except ValueError:
            return None

    @property
    def units(self):
        return self.cfg.get("units") if self.cfg.get("units") in UNITS else "metric"

    # --- disco
    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not (isinstance(raw, dict) and raw.get("v") == 1 and isinstance(raw.get("data"), dict)
                    and isinstance(raw.get("updated_at"), (int, float)) and isinstance(raw.get("place"), dict)):
                raise ValueError("formato")
            if not isinstance(raw["data"].get("hourly"), list) or not isinstance(raw["data"].get("daily"), list):
                raise ValueError("formato")
        except (OSError, ValueError, UnicodeDecodeError) as e:
            bad = self.path.with_name("weather.json.bad-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock())))
            try:
                os.replace(self.path, bad)
                log.warning("weather.json inválido (%s): renomeado para %s, começando sem previsão", type(e).__name__, bad.name)
            except OSError as e2:
                log.warning("weather.json inválido (%s) e não consegui renomear: %s", type(e).__name__, type(e2).__name__)
            return
        if raw["place"] != self.place or raw.get("units") != self.units:
            return  # outra cidade/unidade no config.json: a previsão guardada não vale
        self.data, self.updated_at = raw["data"], float(raw["updated_at"])

    def _save(self):
        tmp = self.path.with_name("weather.json.tmp")
        tmp.write_text(json.dumps({"v": 1, "place": self.place, "units": self.units, "updated_at": self.updated_at, "data": self.data},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- estado para os aparelhos
    def state(self):
        """O que vai para o aparelho: {place, units, updated_at, stale, current, hourly, daily, ...}. Sem cidade = só place None."""
        p = self.place
        if not p:
            return {"place": None}
        out = {"place": {k: p[k] for k in ("name", "admin", "country") if k in p}, "units": self.units}
        if self.data:
            out.update(self.data)
            out["updated_at"] = self.updated_at
            out["stale"] = bool(self.stale)
        else:
            out["pending"] = True   # cidade escolhida, ainda sem previsão (primeira busca ou sem internet)
            out["stale"] = bool(self.stale)
        return out

    def due(self, has_clients):
        """Hora de buscar? Só com cidade e aparelho conectado; a cada 30 min (5 min depois de uma falha)."""
        if not has_clients or not self.place:
            return False
        if self.last_try == 0:
            return True
        return self.clock() - self.last_try >= (REFRESH_EVERY_S if self.last_ok else RETRY_AFTER_ERROR_S)

    # --- ações
    def refresh(self):
        """Busca agora. True = o estado mudou (vale mandar aos aparelhos)."""
        p = self.place
        if not p:
            return False
        before = self.state()
        self.last_try = self.clock()
        try:
            data = parse_forecast(self.forecast_fn(p, self.units))
        except Exception as e:  # noqa: BLE001  rede, JSON, formato: o motivo detalhado não vai para o log
            self.last_ok, self.stale = False, True
            log.warning("previsão: erro (%s)", type(e).__name__)
            return self.state() != before
        self.data, self.updated_at, self.last_ok, self.stale = data, self.clock(), True, False
        try:
            self._save()
        except OSError as e:
            log.warning("previsão: não consegui gravar weather.json (%s)", type(e).__name__)
        log.info("previsão atualizada")
        return True

    def refresh_manual(self):
        """weather.refresh: no máximo 1 a cada 60 s. Devolve (mudou, espera_s): espera_s > 0 = recusado por limite."""
        last = max(self.last_manual, self.last_try)   # qualquer busca recente (automática, ao escolher a cidade ou manual) conta
        wait = MANUAL_MIN_S - (self.clock() - last)
        if last and wait > 0:
            return False, int(wait + 0.999)
        self.last_manual = self.clock()
        return self.refresh(), 0

    def search(self, q):
        if not isinstance(q, str) or not q.strip():
            raise ValueError("digite o nome da cidade")
        q = q.strip()
        if len(q) > 80:
            raise ValueError("nome grande demais")
        try:
            return parse_search(self.search_fn(q))
        except (urllib.error.URLError, OSError, TimeoutError):
            log.warning("busca de cidade: erro de rede")
            raise ValueError("Sem conexão com o serviço de previsão") from None
        except Exception as e:  # noqa: BLE001
            log.warning("busca de cidade: erro (%s)", type(e).__name__)
            raise ValueError("Não consegui buscar a cidade agora") from None

    def set_place(self, place):
        """Troca a cidade em memória (o servidor grava no config.json) e descarta a previsão da cidade anterior."""
        place = clean_place(place)
        self.cfg["place"] = place
        self.data = self.updated_at = None
        self.stale, self.last_try, self.last_ok = False, 0.0, False
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
        return place
