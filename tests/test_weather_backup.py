"""Reserva do clima (wttr.in) em weather.py, com fixture real, sem rede.
Rodar: py -3 tests/test_weather_backup.py"""
import json
import logging
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "windows" / "server"))
import weather  # noqa: E402

WTTR = json.loads((ROOT / "tests" / "fixtures" / "wttr-j1.json").read_text(encoding="utf-8"))
OM = json.loads((ROOT / "tests" / "fixtures" / "weather-forecast.json").read_text(encoding="utf-8"))
PLACE = {"name": "Cidade X", "lat": -23.55, "lon": -46.63}


def boom(*a):
    raise urllib.error.URLError("sem rede")


class Parse(unittest.TestCase):
    def test_metric(self):
        d = weather.parse_wttr(WTTR, "metric")
        self.assertEqual(d["units"], {"temp": "°C", "wind": "km/h"})
        c = WTTR["current_condition"][0]
        self.assertEqual(d["current"]["temp"], float(c["temp_C"]))
        self.assertEqual(d["current"]["humidity"], int(c["humidity"]))
        self.assertEqual(d["hourly"], [])                      # reserva não inventa a previsão hora a hora
        self.assertLessEqual(len(d["daily"]), 3)
        self.assertEqual(d["daily"][0]["max"], float(WTTR["weather"][0]["maxtempC"]))
        self.assertRegex(d["daily"][0]["sunrise"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d$")

    def test_imperial(self):
        d = weather.parse_wttr(WTTR, "imperial")
        self.assertEqual(d["units"], {"temp": "°F", "wind": "mph"})
        self.assertEqual(d["current"]["temp"], float(WTTR["current_condition"][0]["temp_F"]))

    def test_no_invented_fields(self):
        d = weather.parse_wttr(WTTR)
        self.assertNotIn("time", d["current"])                 # o wttr.in não dá a hora local atual
        self.assertNotIn("is_day", d["current"])
        raw = json.loads(json.dumps(WTTR))
        raw["current_condition"][0].update(weatherCode="317", temp_C="x")   # sleet: sem equivalente; número ilegível
        c = weather.parse_wttr(raw)["current"]
        self.assertNotIn("text", c)
        self.assertNotIn("icon", c)
        self.assertNotIn("temp", c)

    def test_icon_only_when_day_night_agree(self):
        self.assertEqual(weather._wttr_text_icon(113), ("Céu limpo", None))      # sol de dia, lua de noite: sem saber, sem ícone
        self.assertEqual(weather._wttr_text_icon(119), ("Nublado", "cloud"))
        self.assertEqual(weather._wttr_text_icon("abc"), (None, None))
        self.assertEqual(weather._wttr_text_icon(999), (None, None))

    def test_invalid(self):
        for bad in (None, {}, {"current_condition": [], "weather": []}, {"current_condition": [{}]}):
            with self.assertRaises(ValueError):
                weather.parse_wttr(bad)

    def test_same_shape_as_open_meteo(self):
        a, b = weather.parse_forecast(OM), weather.parse_wttr(WTTR)
        self.assertEqual(set(b), set(a))
        self.assertTrue(set(b["current"]) <= set(a["current"]))
        self.assertTrue(set(b["daily"][0]) <= set(a["daily"][0]))


class Fallback(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def mk(self, primary, backup):
        return weather.Weather(self.base, {"place": dict(PLACE), "units": "metric"}, forecast_fn=primary, backup_fn=backup)

    def test_backup_used_when_primary_fails(self):
        w = self.mk(boom, lambda p, u: WTTR)
        self.assertTrue(w.refresh())
        st = w.state()
        self.assertFalse(st["stale"])
        self.assertEqual(st["hourly"], [])
        self.assertTrue(w.last_ok)

    def test_primary_wins(self):
        calls = []
        w = self.mk(lambda p, u: OM, lambda p, u: calls.append(1))
        w.refresh()
        self.assertEqual(calls, [])
        self.assertTrue(w.state()["hourly"])

    def test_both_fail_keeps_last_and_marks_stale(self):
        w = self.mk(lambda p, u: OM, boom)
        w.refresh()
        w.forecast_fn = boom
        w.refresh()
        st = w.state()
        self.assertTrue(st["stale"])
        self.assertTrue(st["hourly"])                          # última leitura boa, marcada como desatualizada

    def test_both_fail_never_loaded_is_pending_not_fake(self):
        w = self.mk(boom, boom)
        w.refresh()
        st = w.state()
        self.assertTrue(st.get("pending"))
        self.assertNotIn("current", st)

    def test_garbage_backup_is_failure(self):
        w = self.mk(boom, lambda p, u: {"x": 1})
        w.refresh()
        self.assertTrue(w.state().get("pending"))

    def test_no_backup_when_forecast_injected(self):
        self.assertIsNone(weather.Weather(self.base, {"place": dict(PLACE)}, forecast_fn=boom).backup_fn)
        self.assertIs(weather.Weather(self.base, {"place": dict(PLACE)}).backup_fn, weather.fetch_wttr)

    def test_log_has_no_place_data(self):
        lines = []

        class H(logging.Handler):
            def emit(self, r):
                lines.append(r.getMessage())
        h = H()
        lg = logging.getLogger("companion.weather")
        old = lg.level
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        try:
            self.mk(boom, lambda p, u: WTTR).refresh()
        finally:
            lg.removeHandler(h)
            lg.setLevel(old)
        txt = " ".join(lines)
        self.assertIn("reserva", txt)
        for secret in ("Cidade X", "23.55", "46.63"):
            self.assertNotIn(secret, txt)


if __name__ == "__main__":
    unittest.main()
