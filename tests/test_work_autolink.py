"""Ligação automática job ↔ card e pergunta do Lume (autolink.py). Rodar: py -3 tests/test_work_autolink.py"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import autolink  # noqa: E402
import work  # noqa: E402

DAYS = {"2026-11-25": {"Top Market\tA4\tPhotoshop\ta.psd": 60.0, "Top Market\tFlyer\tPhotoshop\tf.psd": 60.0, "Beta\tMarca\tIllustrator\tb.ai": 60.0}}


def card(short, name, cat="running"):
    return {"short": short, "name": name, "cat": cat}


def info(client="Top Market", job="A4", file="a.psd"):
    return {"app": "Photoshop", "file": file, "client": client, "job": job, "identified": True, "counting": True}


class Base(unittest.TestCase):
    cards = []

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.t = work.WorkTracker(Path(self._d.name))
        self.t.days = {k: dict(v) for k, v in DAYS.items()}
        self.now = 1000.0
        self.a = autolink.AutoLink(self.t, lambda: self.cards, clock=lambda: self.now)

    def tearDown(self):
        self._d.cleanup()

    def run_for(self, secs, inf=None, step=2):
        out = []
        end = self.now + secs
        while self.now < end:
            self.now += step
            r = self.a.observe(inf or info(), self.now)
            if r:
                out.append(r)
        return out


class Monday(Base):
    """Itens do monday entram na mesma regra (short "m:<id>"); a ligação vai para work.mlinks."""

    def test_exact_item_links_into_mlinks_as_auto(self):
        self.cards = [card("m:123456", "Top Market - A4")]
        r = self.a.observe(info(), self.now)
        self.assertEqual((r["kind"], r["src"]), ("autolink", "md"))
        self.assertEqual([(x["client"], x["job"], x["item"], x.get("auto")) for x in self.t.mlinks], [("Top Market", "A4", "123456", True)])
        self.assertEqual(self.t.links, [])
        self.assertIsNone(self.a.observe(info(), self.now + 2))

    def test_trello_and_monday_same_name_is_ambiguous_so_it_asks(self):
        self.cards = [card("AAAAAAA1", "Top Market - A4"), card("m:9", "Top Market - A4")]
        out = self.run_for(130)
        self.assertEqual([x["kind"] for x in out], ["ask"])
        self.assertEqual(len(self.a.public()["cards"]), 2)

    def test_pick_item_and_undo_auto(self):
        self.cards = [card("m:77", "Top Market - Flyer"), card("m:78", "Top Market - Outro")]
        out = self.run_for(130)
        q = self.a.pending["id"]
        self.a.pick(q, "m:77", lambda s: True)
        self.assertEqual(self.t.mlinks[0]["item"], "77")
        self.cards = [card("m:55", "Top Market - Flyer")]
        r = self.a.observe(info(job="Flyer", file="f.psd"), self.now + 2)
        self.assertEqual(r["src"], "md")
        self.a.undo(r["id"])
        self.assertEqual([x["item"] for x in self.t.mlinks], ["77"])
        self.assertEqual(len(self.t.nolink), 1)                       # desfeito: não religa sozinho


class Exact(Base):
    def test_one_exact_card_links_at_once_and_is_marked_auto(self):
        self.cards = [card("AAAAAAA1", "TOP MARKET - A4"), card("BBBBBBB2", "Top Market - Flyer")]
        r = self.a.observe(info(), self.now)
        self.assertEqual(r["kind"], "autolink")
        self.assertEqual([(x["client"], x["job"], x["card"], x.get("auto")) for x in self.t.links], [("Top Market", "A4", "AAAAAAA1", True)])
        self.assertIsNone(self.a.observe(info(), self.now + 2))       # já ligado: nada mais acontece

    def test_accents_and_spaces_only_equality(self):
        self.cards = [card("AAAAAAA1", "top  market -  a4")]
        self.assertEqual(self.a.observe(info(), self.now)["kind"], "autolink")

    def test_todo_card_counts_but_done_cards_do_not(self):
        self.cards = [card("AAAAAAA1", "Top Market - A4", "todo")]
        self.assertEqual(self.a.observe(info(), self.now)["kind"], "autolink")
        self.t.links.clear()
        self.cards = [card("AAAAAAA1", "Top Market - A4", "approved")]
        self.assertIsNone(self.a.observe(info(), self.now + 2))

    def test_two_exact_cards_do_not_link_and_go_to_the_question(self):
        self.cards = [card("AAAAAAA1", "Top Market - A4"), card("CCCCCCC3", "Top Market - A4", "todo")]
        out = self.run_for(130)
        self.assertEqual(self.t.links, [])
        self.assertEqual([r["kind"] for r in out], ["ask"])
        self.assertEqual([c["short"] for c in out[0]["cards"]], ["AAAAAAA1", "CCCCCCC3"])

    def test_exact_card_already_linked_to_another_job_is_not_stolen(self):
        self.cards = [card("AAAAAAA1", "Top Market - A4")]
        self.t.link_set("Top Market", "Flyer", "AAAAAAA1", "x")
        self.assertIsNone(self.a.observe(info(), self.now))
        self.assertEqual([x["job"] for x in self.t.links], ["Flyer"])

    def test_job_already_linked_never_changes(self):
        self.t.link_set("Top Market", "A4", "ZZZZZZZ9", "velho")
        self.cards = [card("AAAAAAA1", "Top Market - A4")]
        self.assertEqual(self.run_for(130), [])
        self.assertEqual([x["card"] for x in self.t.links], ["ZZZZZZZ9"])

    def test_undo_removes_only_auto_links(self):
        self.cards = [card("AAAAAAA1", "Top Market - A4")]
        r = self.a.observe(info(), self.now)
        manual = self.t.link_set("Beta", "Marca", "BBBBBBB2", "m")["link"]
        with self.assertRaises(ValueError):
            self.a.undo(manual["id"])
        self.assertEqual(len(self.a.undo(r["id"])["removed"]), 1)
        self.assertEqual([x["card"] for x in self.t.links], ["BBBBBBB2"])
        self.assertEqual(self.a.observe(info(), self.now + 4), None)    # Desfazer não religa sozinho
        self.assertEqual(len(self.t.links), 1)
        self.assertEqual([x["job"] for x in self.t.nolink_get()], ["A4"])

    def test_restoring_an_undone_auto_link_lifts_the_block(self):
        self.cards = [card("AAAAAAA1", "Top Market - A4")]
        r = self.a.observe(info(), self.now)
        gone = self.a.undo(r["id"])["removed"]
        self.t.link_restore(gone)
        self.assertEqual(self.t.nolink_get(), [])
        self.assertEqual([x["card"] for x in self.t.links], ["AAAAAAA1"])


class Ask(Base):
    cards = [card("AAAAAAA1", "Top Market - Capa"), card("BBBBBBB2", "Top Market - Cartaz", "todo"),
             card("CCCCCCC3", "Top Market - Banner"), card("DDDDDDD4", "Top Market - Selo", "todo"),
             card("EEEEEEE5", "Beta - Marca")]

    def test_only_client_matches_asks_after_2_minutes_with_max_3_running_first(self):
        self.assertEqual(self.run_for(110), [])
        out = self.run_for(20)
        self.assertEqual([r["kind"] for r in out], ["ask"])
        p = out[0]
        self.assertEqual([c["short"] for c in p["cards"]], ["AAAAAAA1", "CCCCCCC3", "BBBBBBB2"])   # Em execução antes de A fazer, máx. 3
        self.assertEqual((p["client"], p["file"]), ("Top Market", "a.psd"))
        self.assertNotIn("job", p)
        self.assertEqual(self.t.links, [])

    def test_changing_job_resets_the_count_and_clears_the_question(self):
        self.run_for(130)
        out = self.run_for(2, info(job="Flyer", file="f.psd"))
        self.assertEqual([r["kind"] for r in out], ["clear"])
        self.assertIsNone(self.a.pending)

    def test_idle_time_does_not_count(self):
        idle = dict(info(), counting=False)
        self.assertEqual(self.run_for(300, idle), [])

    def test_unidentified_or_no_trello_cards_never_ask(self):
        self.assertEqual(self.run_for(300, dict(info(), identified=False)), [])
        self.cards = []
        self.assertEqual(self.run_for(300), [])

    def test_no_candidate_of_the_client_no_question(self):
        self.cards = [card("EEEEEEE5", "Beta - Marca")]
        self.assertEqual(self.run_for(300), [])

    def test_pick_links_through_server_and_validates_ids(self):
        q = self.run_for(130)[0]
        with self.assertRaises(ValueError):
            self.a.pick("outro", "AAAAAAA1", lambda s: True)
        with self.assertRaises(ValueError):
            self.a.pick(q["id"], "EEEEEEE5", lambda s: True)          # fora dos candidatos
        self.a.pick(q["id"], "BBBBBBB2", lambda s: True)
        self.assertEqual([(x["job"], x["card"], x.get("auto")) for x in self.t.links], [("A4", "BBBBBBB2", None)])
        self.assertIsNone(self.a.pending)
        with self.assertRaises(ValueError):
            self.a.pick(q["id"], "BBBBBBB2", lambda s: True)           # já respondida

    def test_nenhum_is_permanent_and_only_the_panel_undoes_it(self):
        q = self.run_for(130)[0]
        self.a.none(q["id"])
        self.assertEqual(self.run_for(3000), [])
        self.assertEqual(len(self.t.nolink_get()), 1)
        a2 = autolink.AutoLink(work.WorkTracker(Path(self._d.name)), lambda: self.cards, clock=lambda: self.now)   # reinício: continua valendo
        self.a = a2
        self.assertEqual(self.run_for(300), [])
        self.t.nolink_delete(self.t.nolink_get()[0]["id"])             # "Perguntar de novo" no painel
        self.a = autolink.AutoLink(self.t, lambda: self.cards, clock=lambda: self.now)
        self.assertEqual([r["kind"] for r in self.run_for(130)], ["ask"])

    def test_depois_asks_again_after_30_minutes_and_is_not_listed_in_the_panel(self):
        q = self.run_for(130)[0]
        self.a.later(q["id"], self.now)
        self.assertEqual(self.t.nolink_get(), [])
        self.assertEqual(self.run_for(29 * 60), [])
        out = self.run_for(3 * 60 + 130)
        self.assertEqual([r["kind"] for r in out], ["ask"])


if __name__ == "__main__":
    unittest.main()
