"""Agenda (agenda.py): parser .ics, recorrência, EXDATE, exceções (RECURRENCE-ID), fuso/horário de verão, links seguros,
estados e aviso. Sem rede (fetch falso). Rodar: py -3 tests/test_agenda.py"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import agenda  # noqa: E402

UTC = timezone.utc
SP, NY = ZoneInfo("America/Sao_Paulo"), ZoneInfo("America/New_York")


def ics(*events, name="Minha agenda"):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:" + name + "\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def ev(*lines):
    return "BEGIN:VEVENT\r\n" + "".join(l + "\r\n" for l in lines) + "END:VEVENT\r\n"


def ep(dt):
    return int(dt.timestamp())


def inst(text, now, tz=UTC):
    evs, _, sk = agenda.parse_ics(text, tz)
    res, sk2 = agenda.expand(evs, now)
    return res, sk + sk2


NOW = ep(datetime(2026, 11, 25, 9, 0, tzinfo=SP))


class ParserTests(unittest.TestCase):
    def test_evento_simples_com_tzid_e_utc(self):
        t = ics(ev("UID:a", "SUMMARY:Alinhamento", "DTSTART;TZID=America/Sao_Paulo:20261125T100000", "DTEND;TZID=America/Sao_Paulo:20261125T110000"),
                ev("UID:b", "SUMMARY:Em UTC", "DTSTART:20261125T180000Z", "DURATION:PT30M"))
        r, sk = inst(t, NOW)
        self.assertEqual([(x["title"], x["start"], x["end"]) for x in r],
                         [("Alinhamento", ep(datetime(2026, 11, 25, 10, 0, tzinfo=SP)), ep(datetime(2026, 11, 25, 11, 0, tzinfo=SP))),
                          ("Em UTC", ep(datetime(2026, 11, 25, 18, 0, tzinfo=UTC)), ep(datetime(2026, 11, 25, 18, 30, tzinfo=UTC)))])
        self.assertEqual(sk, 0)

    def test_dia_inteiro_cancelado_e_passado_ficam_de_fora(self):
        t = ics(ev("UID:a", "SUMMARY:Feriado", "DTSTART;VALUE=DATE:20261125", "DTEND;VALUE=DATE:20261126"),
                ev("UID:b", "SUMMARY:Cancelada", "STATUS:CANCELLED", "DTSTART:20261125T180000Z", "DTEND:20261125T190000Z"),
                ev("UID:c", "SUMMARY:Ontem", "DTSTART:20261124T180000Z", "DTEND:20261124T190000Z"))
        self.assertEqual(inst(t, NOW)[0], [])

    def test_em_andamento_aparece(self):
        t = ics(ev("UID:a", "SUMMARY:Agora", "DTSTART:20261125T115000Z", "DTEND:20261125T130000Z"))
        r, _ = inst(t, ep(datetime(2026, 11, 25, 12, 0, tzinfo=UTC)))
        self.assertEqual([x["title"] for x in r], ["Agora"])

    def test_dobra_de_linha_e_escapes(self):
        t = ics(ev("UID:a", "SUMMARY:Reuni\\, com a equi", " pe\\nfim", "DTSTART:20261125T180000Z", "DTEND:20261125T190000Z"))
        self.assertEqual(inst(t, NOW)[0][0]["title"], "Reuni, com a equipe\nfim")

    def test_sem_titulo(self):
        t = ics(ev("UID:a", "DTSTART:20261125T180000Z", "DTEND:20261125T190000Z"))
        self.assertEqual(inst(t, NOW)[0][0]["title"], "(sem título)")

    def test_horario_flutuante_usa_o_fuso_do_pc(self):
        t = ics(ev("UID:a", "SUMMARY:x", "DTSTART:20261125T150000", "DTEND:20261125T160000"))
        self.assertEqual(inst(t, NOW, tz=SP)[0][0]["start"], ep(datetime(2026, 11, 25, 15, 0, tzinfo=SP)))

    def test_fuso_desconhecido_nao_e_adivinhado(self):
        t = ics(ev("UID:a", "SUMMARY:x", "DTSTART;TZID=W. Europe Standard Time:20261125T150000", "DTEND;TZID=W. Europe Standard Time:20261125T160000"))
        r, sk = inst(t, NOW)
        self.assertEqual((r, sk), ([], 1))

    def test_arquivo_que_nao_e_ics(self):
        with self.assertRaises(agenda.FeedError) as c:
            agenda.parse_ics("<html>login</html>")
        self.assertEqual(c.exception.kind, "bad_feed")

    def test_nome_do_calendario(self):
        self.assertEqual(agenda.parse_ics(ics(name="Trabalho"))[1], "Trabalho")


class RecurrenceTests(unittest.TestCase):
    def test_diaria_ate_count(self):
        t = ics(ev("UID:r", "SUMMARY:Daily", "DTSTART;TZID=America/Sao_Paulo:20261123T100000", "DTEND;TZID=America/Sao_Paulo:20261123T103000", "RRULE:FREQ=DAILY;COUNT=5"))
        r, _ = inst(t, NOW)   # 23,24,25,26,27: a de hoje às 10h e as de 26 e 27
        self.assertEqual([x["start"] for x in r], [ep(datetime(2026, 11, d, 10, 0, tzinfo=SP)) for d in (25, 26, 27)])

    def test_semanal_byday(self):
        t = ics(ev("UID:r", "SUMMARY:Seg e qua", "DTSTART;TZID=America/Sao_Paulo:20261102T140000", "DTEND;TZID=America/Sao_Paulo:20261102T150000", "RRULE:FREQ=WEEKLY;BYDAY=MO,WE"))
        r, _ = inst(t, NOW)
        days = [datetime.fromtimestamp(x["start"], SP).strftime("%a %d") for x in r if x["start"] < NOW + 7 * 86400]
        self.assertEqual(days, ["Wed 25", "Mon 30"])

    def test_exdate_remove_uma_ocorrencia(self):
        t = ics(ev("UID:r", "SUMMARY:x", "DTSTART;TZID=America/Sao_Paulo:20261123T100000", "DTEND;TZID=America/Sao_Paulo:20261123T103000",
                   "RRULE:FREQ=DAILY;COUNT=5", "EXDATE;TZID=America/Sao_Paulo:20261126T100000"))
        r, _ = inst(t, NOW)
        self.assertEqual([datetime.fromtimestamp(x["start"], SP).day for x in r], [25, 27])

    def test_excecao_move_uma_ocorrencia(self):
        t = ics(ev("UID:r", "SUMMARY:Daily", "DTSTART;TZID=America/Sao_Paulo:20261123T100000", "DTEND;TZID=America/Sao_Paulo:20261123T103000", "RRULE:FREQ=DAILY;COUNT=5"),
                ev("UID:r", "SUMMARY:Daily (movida)", "RECURRENCE-ID;TZID=America/Sao_Paulo:20261126T100000",
                   "DTSTART;TZID=America/Sao_Paulo:20261126T160000", "DTEND;TZID=America/Sao_Paulo:20261126T163000"))
        r, _ = inst(t, NOW)
        self.assertEqual([(x["title"], datetime.fromtimestamp(x["start"], SP).strftime("%d %H")) for x in r],
                         [("Daily", "25 10"), ("Daily (movida)", "26 16"), ("Daily", "27 10")])

    def test_excecao_cancelada(self):
        t = ics(ev("UID:r", "SUMMARY:Daily", "DTSTART;TZID=America/Sao_Paulo:20261123T100000", "DTEND;TZID=America/Sao_Paulo:20261123T103000", "RRULE:FREQ=DAILY;COUNT=5"),
                ev("UID:r", "STATUS:CANCELLED", "RECURRENCE-ID;TZID=America/Sao_Paulo:20261126T100000", "DTSTART;TZID=America/Sao_Paulo:20261126T100000"))
        r, _ = inst(t, NOW)
        self.assertEqual([datetime.fromtimestamp(x["start"], SP).day for x in r], [25, 27])

    def test_until_em_utc(self):
        t = ics(ev("UID:r", "SUMMARY:x", "DTSTART;TZID=America/Sao_Paulo:20261123T100000", "DTEND;TZID=America/Sao_Paulo:20261123T103000",
                   "RRULE:FREQ=DAILY;UNTIL=20261126T235959Z"))
        r, _ = inst(t, NOW)
        self.assertEqual([datetime.fromtimestamp(x["start"], SP).day for x in r], [25, 26])

    def test_horario_de_verao_mantem_a_hora_local(self):
        # segunda 09:00 em Nova York atravessando a virada de 8/mar/2026: o UTC muda (14h -> 13h), a hora local não
        t = ics(ev("UID:r", "SUMMARY:x", "DTSTART;TZID=America/New_York:20260302T090000", "DTEND;TZID=America/New_York:20260302T100000", "RRULE:FREQ=WEEKLY;BYDAY=MO"))
        evs, _, _ = agenda.parse_ics(t, UTC)
        r, _ = agenda.expand(evs, ep(datetime(2026, 3, 1, 0, 0, tzinfo=UTC)), horizon_days=14)
        self.assertEqual([datetime.fromtimestamp(x["start"], UTC).strftime("%m-%d %H") for x in r], ["03-02 14", "03-09 13"])
        self.assertEqual({datetime.fromtimestamp(x["start"], NY).hour for x in r}, {9})

    def test_regra_nao_suportada_e_contada(self):
        t = ics(ev("UID:r", "SUMMARY:x", "DTSTART:20261125T180000Z", "DTEND:20261125T190000Z", "RRULE:FREQ=MINUTELY"))
        self.assertEqual(inst(t, NOW), ([], 1))

    def test_regra_quebrada_nao_derruba_as_outras(self):
        t = ics(ev("UID:r", "SUMMARY:ruim", "DTSTART;TZID=America/Sao_Paulo:20261125T100000", "DTEND;TZID=America/Sao_Paulo:20261125T110000", "RRULE:FREQ=DAILY;UNTIL=20261130"),
                ev("UID:o", "SUMMARY:boa", "DTSTART:20261125T180000Z", "DTEND:20261125T190000Z"))
        r, sk = inst(t, NOW)
        self.assertEqual(([x["title"] for x in r], sk), (["boa"], 1))


class LinkTests(unittest.TestCase):
    def test_safe_link(self):
        ok = ["https://meet.google.com/abc-defg-hij", "https://us02web.zoom.us/j/123?pwd=x", "https://teams.microsoft.com/l/meetup-join/x"]
        bad = ["http://meet.google.com/x", "https://evil.com/meet.google.com", "https://meet.google.com.evil.com/x", "https://user@meet.google.com/x",
               "javascript:alert(1)", "file:///c:/windows", "https://zoom.us/j/1 2", "", None, "https://notzoom.us/x"]
        self.assertEqual([agenda.safe_link(u) for u in ok], ok)
        self.assertEqual([agenda.safe_link(u) for u in bad], [None] * len(bad))

    def test_link_vem_so_dos_campos_certos(self):
        t = ics(ev("UID:a", "SUMMARY:x", "DTSTART:20261125T180000Z", "DTEND:20261125T190000Z", "DESCRIPTION:Entre em https://meet.google.com/zzz-zzzz-zzz"),
                ev("UID:b", "SUMMARY:y", "DTSTART:20261125T190000Z", "DTEND:20261125T200000Z", "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij"),
                ev("UID:c", "SUMMARY:z", "DTSTART:20261125T200000Z", "DTEND:20261125T210000Z", "LOCATION:https://zoom.us/j/99"),
                ev("UID:d", "SUMMARY:w", "DTSTART:20261125T210000Z", "DTEND:20261125T220000Z", "LOCATION:Sala 3, https://zoom.us/j/99"))
        self.assertEqual([x["link"] for x in inst(t, NOW)[0]], [None, "https://meet.google.com/abc-defg-hij", "https://zoom.us/j/99", None])


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = Path(self._d.name)
        self.clk = Clock(NOW)
        self.cfg = {"url": "https://calendar.google.com/x/basic.ics", "on": True, "notify_min": 5}
        self.feed = ics(ev("UID:a", "SUMMARY:Reunião secreta", "DTSTART;TZID=America/Sao_Paulo:20261125T100000", "DTEND;TZID=America/Sao_Paulo:20261125T110000",
                           "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij"),
                        ev("UID:b", "SUMMARY:Depois", "DTSTART;TZID=America/Sao_Paulo:20261125T150000", "DTEND;TZID=America/Sao_Paulo:20261125T160000"))
        self.fetch_calls = []
        self.fail = None

    def tearDown(self):
        self._d.cleanup()

    def fetch(self, url):
        self.fetch_calls.append(url)
        if self.fail:
            raise agenda.FeedError(self.fail)
        return self.feed

    def mk(self):
        return agenda.Agenda(self.base, lambda: self.cfg, fetch=self.fetch, clock=self.clk, local_tz=SP)

    def test_desligado_ou_sem_url_nao_busca_nem_inventa(self):
        for c in ({"url": "", "on": True}, {"url": "https://x.example/a.ics", "on": False}, {}):
            self.cfg = c
            a = self.mk()
            a.refresh()
            self.assertEqual((self.fetch_calls, a.state()["next"], a.state()["state"]), ([], None, "off"))

    def test_proxima_reuniao_e_lista(self):
        a = self.mk()
        a.refresh()
        s = a.state()
        self.assertEqual((s["state"], s["next"]["title"], s["next"]["join"], s["next"]["now"], len(s["upcoming"])), ("ok", "Reunião secreta", True, False, 2))
        self.assertEqual(s["next"]["start"] - NOW, 3600)

    def test_em_andamento_e_depois_de_acabar(self):
        a = self.mk()
        a.refresh()
        self.clk.t = ep(datetime(2026, 11, 25, 10, 30, tzinfo=SP))
        self.assertTrue(a.state()["next"]["now"])
        self.clk.t = ep(datetime(2026, 11, 25, 11, 1, tzinfo=SP))
        self.assertEqual(a.state()["next"]["title"], "Depois")
        self.clk.t = ep(datetime(2026, 11, 25, 16, 1, tzinfo=SP))
        self.assertIsNone(a.state()["next"])   # sem reuniões: nunca inventa

    def test_estado_nao_expoe_url_nem_link(self):
        a = self.mk()
        a.refresh()
        blob = json.dumps(a.state()) + json.dumps(a.status())
        self.assertNotIn("calendar.google.com", blob)
        self.assertNotIn("meet.google.com", blob)

    def test_join_so_pelo_id_do_cache(self):
        a = self.mk()
        a.refresh()
        eid = a.state()["next"]["id"]
        self.assertEqual(a.link_for(eid), "https://meet.google.com/abc-defg-hij")
        self.assertIsNone(a.link_for("naoexiste"))

    def test_erro_de_rede_mantem_a_ultima_leitura_e_tenta_em_1_min(self):
        a = self.mk()
        a.refresh()
        self.fail = "net_error"
        self.clk.t += 301
        self.assertTrue(a.due(True))
        a.refresh()
        self.assertEqual((a.state()["state"], a.state()["next"]["title"]), ("net_error", "Reunião secreta"))
        self.clk.t += 30
        self.assertFalse(a.due(True))
        self.clk.t += 31
        self.assertTrue(a.due(True))

    def test_url_ruim_mostra_erro_e_nao_mostra_dado_velho_como_ok(self):
        a = self.mk()
        self.fail = "bad_url"
        a.refresh()
        self.assertEqual(a.state()["state"], "bad_url")

    def test_so_busca_com_aparelho_e_a_cada_5_min(self):
        a = self.mk()
        self.assertFalse(a.due(False))
        self.assertTrue(a.due(True))
        a.refresh()
        self.clk.t += 299
        self.assertFalse(a.due(True))
        self.clk.t += 2
        self.assertTrue(a.due(True))

    def test_manual_respeita_1_minuto(self):
        a = self.mk()
        a.refresh()
        a.refresh_manual()
        self.assertEqual(len(self.fetch_calls), 1)
        self.clk.t += 61
        a.refresh_manual()
        self.assertEqual(len(self.fetch_calls), 2)

    def test_aviso_x_minutos_antes_uma_vez(self):
        a = self.mk()
        a.refresh()
        self.assertEqual(a.soon(), [])   # falta 1 h
        self.clk.t = ep(datetime(2026, 11, 25, 9, 55, tzinfo=SP))
        ev1 = a.soon()
        self.assertEqual([(e["title"], e["minutes"], e["join"]) for e in ev1], [("Reunião secreta", 5, True)])
        self.assertEqual(a.soon(), [])   # não repete
        self.cfg["notify_min"] = 10
        self.clk.t = ep(datetime(2026, 11, 25, 14, 50, tzinfo=SP))
        self.assertEqual([e["title"] for e in a.soon()], ["Depois"])

    def test_nao_avisa_reuniao_que_ja_comecou(self):
        a = self.mk()
        a.refresh()
        self.clk.t = ep(datetime(2026, 11, 25, 10, 1, tzinfo=SP))
        self.assertEqual(a.soon(), [])

    def test_persistencia_depois_de_reiniciar(self):
        a = self.mk()
        a.refresh()
        b = self.mk()
        self.assertEqual(b.state()["next"]["title"], "Reunião secreta")

    def test_texto_nunca_vai_ao_log(self):
        with self.assertNoLogs("companion.agenda", level="DEBUG") if False else self.assertLogs("companion.agenda", level="INFO") as cm:
            self.mk().refresh()
        self.assertNotIn("secreta", "".join(cm.output))
        self.assertNotIn("calendar.google.com", "".join(cm.output))

    def test_validacoes(self):
        self.assertEqual(agenda.valid_notify(10), 10)
        for bad in (0, 3, True, "5"):
            with self.assertRaises(ValueError):
                agenda.valid_notify(bad)
        for bad in ("http://x.example/a.ics", "ftp://x", "https://", "https://a b", 5):
            with self.assertRaises(ValueError):
                agenda.valid_url(bad)
        self.assertEqual(agenda.valid_url(" https://calendar.google.com/calendar/ical/x/private-abc/basic.ics "), "https://calendar.google.com/calendar/ical/x/private-abc/basic.ics")


if __name__ == "__main__":
    unittest.main()
