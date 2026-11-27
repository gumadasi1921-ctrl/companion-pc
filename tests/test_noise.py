"""Ruído (noise.py): validação, geração do WAV, laço e a lógica do modo foco. Filho falso, sem som, sem Windows.
Rodar: py -3 tests/test_noise.py"""
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import noise  # noqa: E402


class FakeProc:
    n = 1000

    def __init__(self):
        FakeProc.n += 1
        self.pid, self.dead, self.killed = FakeProc.n, False, False

    def poll(self):
        return 1 if self.dead else None

    def kill(self):
        self.dead = self.killed = True

    def wait(self, timeout=None):
        return 0


class Cfg(unittest.TestCase):
    def test_padrao_e_parcial(self):
        self.assertEqual(noise.clean_cfg({}), {"focus_on": False, "kind": "brown", "volume": 40})
        self.assertEqual(noise.clean_cfg({"volume": 70}, {"focus_on": True, "kind": "pink"}),
                         {"focus_on": True, "kind": "pink", "volume": 70})

    def test_invalidos(self):
        for bad in ({"kind": "azul"}, {"volume": 101}, {"volume": -1}, {"volume": True}, {"focus_on": 1}, {"x": 1}, "brown"):
            with self.assertRaises(ValueError):
                noise.clean_cfg(bad)


class Wav(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = {k: noise.samples(k, 3) for k in noise.KINDS}   # 3 s bastam para as propriedades

    def test_pico_baixo_sem_dc_e_laco_sem_salto(self):
        for k, a in self.s.items():
            self.assertLessEqual(max(abs(v) for v in a), noise.PEAK * 32767 + 1, k)
            self.assertLess(abs(sum(a) / len(a)), 0.02 * 32767, k)   # sem deslocamento de DC
            rms = (sum(v * v for v in a) / len(a)) ** 0.5
            self.assertLess(abs(a[0] - a[-1]), 4 * rms, k)            # a emenda do laço não é um salto

    def test_deterministico(self):
        self.assertEqual(noise.samples("pink", 2), noise.samples("pink", 2))

    def test_espectro_marrom_mais_grave_que_branco(self):
        def diff_energy(a):   # energia da diferença entre amostras vizinhas: alta = mais agudo
            return sum((a[i + 1] - a[i]) ** 2 for i in range(len(a) - 1)) / sum(v * v for v in a)
        e = {k: diff_energy(a) for k, a in self.s.items()}
        self.assertGreater(e["white"], e["pink"])
        self.assertGreater(e["pink"], e["brown"])

    def test_wav_gravado_uma_vez(self):
        orig = noise.SECS
        noise.SECS = 3
        try:
            with tempfile.TemporaryDirectory() as t:
                p = noise.ensure_wav(t, "white")
                with wave.open(str(p)) as w:
                    self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, noise.SR))
                m = p.stat().st_mtime_ns
                self.assertEqual(noise.ensure_wav(t, "white"), p)
                self.assertEqual(p.stat().st_mtime_ns, m)   # já existia: não regera
        finally:
            noise.SECS = orig


class Focus(unittest.TestCase):
    def setUp(self):
        self.cfg = {"focus_on": True, "kind": "brown", "volume": 40}
        self.procs, self.vols = [], []

        def spawn(wav):
            p = FakeProc()
            self.procs.append(p)
            return p
        self.n = noise.Noise(".", lambda: self.cfg, spawn=spawn, set_volume=lambda pid, lv: self.vols.append((pid, lv)),
                             ensure=lambda base, kind: Path(f"{kind}.wav"))

    def tearDown(self):
        noise.PIDS.clear()

    def test_foco_pausa_foco(self):
        self.assertTrue(self.n.sync(True))
        self.assertTrue(self.n.on)
        self.assertEqual((self.n.kind, self.n.by), ("brown", "auto"))
        self.assertEqual(self.vols[-1][1], 0.4)                 # volume próprio (40%) aplicado ao filho
        self.assertFalse(self.n.sync(True))                     # mesma leitura de novo: nada muda
        self.assertTrue(self.n.sync(False))                     # pausa: para
        self.assertFalse(self.n.on)
        self.assertTrue(self.procs[0].killed)
        self.assertTrue(self.n.sync(True))                      # próximo foco: volta
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(self.n.state()["name"], "Ruído marrom")

    def test_ajuste_desligado_nao_liga(self):
        self.cfg["focus_on"] = False
        self.assertFalse(self.n.sync(True))
        self.assertFalse(self.n.on)

    def test_midia_tocando_bloqueia_e_force_liga(self):
        self.assertTrue(self.n.sync(True, media_playing=True))
        self.assertFalse(self.n.on)
        st = self.n.state()
        self.assertTrue(st["blocked"])
        self.assertEqual(self.procs, [])                        # nada foi trocado sem avisar
        self.assertFalse(self.n.sync(True, media_playing=True))  # continua pendente, sem repetir
        self.n.force()
        self.assertTrue(self.n.on)
        self.assertEqual(self.n.by, "auto")
        self.assertFalse(self.n.state()["blocked"])
        self.n.sync(False)                                      # a pausa desliga o que o "ligar mesmo assim" ligou
        self.assertFalse(self.n.on)

    def test_force_sem_bloqueio_recusa(self):
        with self.assertRaises(ValueError):
            self.n.force()

    def test_bloqueio_some_ao_sair_do_foco(self):
        self.n.sync(True, media_playing=True)
        self.n.sync(False)
        self.assertFalse(self.n.state()["blocked"])
        self.n.sync(True, media_playing=False)                  # próximo foco sem mídia: liga
        self.assertTrue(self.n.on)

    def test_ligado_a_mao_nao_e_desligado_pela_pausa(self):
        self.n.start("pink", "manual")
        self.n.sync(True)                                       # foco começa com ruído manual tocando: não troca
        self.assertEqual((self.n.kind, self.n.by, len(self.procs)), ("pink", "manual", 1))
        self.n.sync(False)
        self.assertTrue(self.n.on)

    def test_manual_assume_o_que_a_automacao_ligou(self):
        self.n.sync(True)
        self.n.start("brown", "manual")                         # você tocou em "ligar" no mesmo tipo: agora é seu
        self.n.sync(False)
        self.assertTrue(self.n.on)

    def test_parar_no_meio_do_foco_so_volta_no_proximo(self):
        self.n.sync(True)
        self.n.stop()
        self.assertFalse(self.n.sync(True))
        self.assertFalse(self.n.on)
        self.n.sync(False)
        self.n.sync(True)
        self.assertTrue(self.n.on)

    def test_ligar_o_ajuste_no_meio_do_foco(self):
        self.cfg["focus_on"] = False
        self.n.sync(True)
        self.assertFalse(self.n.on)
        self.cfg["focus_on"] = True
        self.assertTrue(self.n.would_start(True))
        self.n.sync(True)
        self.assertTrue(self.n.on)
        self.cfg["focus_on"] = False                            # desligar o ajuste no meio: para o automático
        self.n.sync(True)
        self.assertFalse(self.n.on)

    def test_would_start_so_na_subida(self):
        self.assertTrue(self.n.would_start(True))
        self.n.sync(True)
        self.assertFalse(self.n.would_start(True))
        self.assertFalse(self.n.would_start(False))

    def test_filho_que_morre_volta_para_desligado(self):
        self.n.start("white", "manual")
        self.procs[0].dead = True
        self.assertTrue(self.n.reap())
        self.assertFalse(self.n.state()["on"])
        self.assertNotIn(self.procs[0].pid, noise.PIDS)

    def test_trocar_de_tipo_mata_o_antigo_e_pids_para_o_mixer(self):
        self.n.start("white", "manual")
        a = self.procs[0]
        self.n.start("pink", "manual")
        self.assertTrue(a.killed)
        self.assertEqual(noise.PIDS, {self.procs[1].pid})
        self.n.stop()
        self.assertEqual(noise.PIDS, set())

    def test_estado_sem_nome_de_arquivo(self):
        self.n.start("brown", "manual")
        st = self.n.state()
        self.assertEqual(set(st), {"on", "kind", "name", "by", "volume", "blocked", "cfg"})
        self.assertNotIn(".wav", str(st))


if __name__ == "__main__":
    unittest.main()
