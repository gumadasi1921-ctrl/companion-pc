"""Ruído branco, rosa e marrom gerado no PC (sem baixar nada, sem internet, sem biblioteca extra).

Som: WAV mono 44,1 kHz de 19 s gerado em Python puro (seed fixa) na primeira vez e guardado ao lado do config.json;
a ponta é mesclada com o começo (o laço não estala) e o pico é baixo (-18 dBFS). Toca em um PROCESSO FILHO
(noise_player.py: winsound em laço, ~0,2% de CPU) na saída padrão do Windows, igual ao resto do PC. Volume próprio =
volume da SESSÃO de áudio do filho (pycaw, o mesmo mecanismo do Mixer), sem reiniciar o som.

Modo foco (config "noise": {focus_on, kind, volume}): sync() liga ao COMEÇAR o foco, desliga na pausa/fim e volta no
próximo foco. Só desliga o que a automação ligou (`by` = "auto"); o que você ligou à mão fica. Com mídia tocando no
começo do foco NÃO liga: fica `blocked` até você escolher "Ligar mesmo assim" (force()). Nada daqui vai ao log além
do tipo de evento.
"""
import array
import logging
import os
import random
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger("companion")

KINDS = ("white", "pink", "brown")
NAMES = {"white": "branco", "pink": "rosa", "brown": "marrom"}
DEFAULT_CFG = {"focus_on": False, "kind": "brown", "volume": 40}
SR, SECS, FADE_S = 44100, 20, 1
PEAK = 0.125            # -18 dBFS: mesmo a 100% do volume da sessão não é uma rajada
PIDS = set()            # pids dos filhos; o Mixer usa para mostrar "Ruído" em vez de pythonw


def clean_cfg(v, cur=None):
    """Aceita só as chaves conhecidas (parcial); devolve o config completo e válido ou ValueError."""
    out = dict(DEFAULT_CFG)
    if isinstance(cur, dict):
        for k in DEFAULT_CFG:
            if k in cur:
                out[k] = cur[k]
    if not isinstance(v, dict):
        raise ValueError("ruído: ajustes inválidos")
    for k, x in v.items():
        if k == "focus_on":
            if not isinstance(x, bool):
                raise ValueError("ruído: ligar no foco deve ser sim ou não")
            out[k] = x
        elif k == "kind":
            if x not in KINDS:
                raise ValueError("ruído: tipo deve ser branco, rosa ou marrom")
            out[k] = x
        elif k == "volume":
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 100:
                raise ValueError("ruído: volume de 0 a 100")
            out[k] = int(round(x))
        else:
            raise ValueError("ruído: ajuste desconhecido")
    return out


def samples(kind, secs=SECS):
    """int16 mono em laço sem emenda audível. Determinístico (seed por tipo)."""
    n, r = SR * secs, random.Random("ruido-" + kind)
    g = r.gauss
    if kind == "white":
        x = [g(0, 1) for _ in range(n)]
    elif kind == "pink":   # Paul Kellet (filtro de 1/f barato)
        b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
        x = []
        for _ in range(n):
            w = g(0, 1)
            b0 = 0.99886 * b0 + w * 0.0555179
            b1 = 0.99332 * b1 + w * 0.0750759
            b2 = 0.96900 * b2 + w * 0.1538520
            b3 = 0.86650 * b3 + w * 0.3104856
            b4 = 0.55000 * b4 + w * 0.5329522
            b5 = -0.7616 * b5 - w * 0.0168980
            x.append(b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362)
            b6 = w * 0.115926
    elif kind == "brown":  # integrador com vazamento (sem deriva de DC)
        y, x = 0.0, []
        for _ in range(n):
            y = 0.998 * y + g(0, 1) * 0.1
            x.append(y)
    else:
        raise ValueError("tipo inválido")
    m = sum(x) / n
    x = [v - m for v in x]
    f = SR * FADE_S
    for i in range(f):   # o fim se mistura com o começo: o laço fecha sem salto
        t = i / f
        x[i] = x[i] * t + x[n - f + i] * (1 - t)
    x = x[: n - f]
    k = PEAK * 32767 / max(abs(v) for v in x)
    return array.array("h", [int(v * k) for v in x])


def ensure_wav(base, kind):
    """Caminho do WAV do tipo (gera uma vez). Gravação atômica."""
    p = Path(base) / f"noise-{kind}.wav"
    if p.exists() and p.stat().st_size > 100_000:
        return p
    a = samples(kind)
    tmp = p.with_name(p.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(a.tobytes())
    tmp.replace(p)
    return p


def _spawn(wav):
    """Sobe o filho (sem janela). Devolve o Popen."""
    player = str(Path(__file__).with_name("noise_player.py"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen([sys.executable, player, str(wav), str(os.getpid())], creationflags=flags,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _set_session_volume(pid, level, tries=25, wait=0.12):
    """Volume (0..1) da sessão de áudio do filho; espera ela aparecer (a sessão só existe depois de o som começar)."""
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
    except Exception:  # noqa: BLE001
        return False
    try:
        comtypes.CoInitialize()
    except Exception:  # noqa: BLE001
        pass
    try:
        for _ in range(tries):
            try:
                for s in AudioUtilities.GetAllSessions():
                    if s.ProcessId == pid:
                        s.SimpleAudioVolume.SetMasterVolume(level, None)
                        return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(wait)
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass
    return False


class Noise:
    """Estado do ruído. spawn/set_volume/ensure injetáveis (testes); o resto é lógica pura. Usado por uma thread de
    cada vez (fila de disco) — sem locks."""

    def __init__(self, base, get_cfg=None, spawn=None, set_volume=None, ensure=None):
        self.base = Path(base)
        self.get_cfg = get_cfg or (lambda: dict(DEFAULT_CFG))
        self._spawn = spawn or _spawn
        self._vol = set_volume or (lambda pid, lv: threading.Thread(target=_set_session_volume, args=(pid, lv), daemon=True).start())
        self._ensure = ensure or ensure_wav
        self.proc, self.kind, self.by, self.blocked = None, None, None, False
        self._armed = False   # "foco rodando e ajuste ligado" na última leitura (para pegar só a subida e a descida)

    # -- estado -----------------------------------------------------------------
    def cfg(self):
        try:
            return clean_cfg({}, self.get_cfg())
        except ValueError:
            return dict(DEFAULT_CFG)

    @property
    def on(self):
        return self.proc is not None and self.proc.poll() is None

    def reap(self):
        """True se o filho morreu sozinho desde a última vez (o estado volta para desligado)."""
        if self.proc is not None and self.proc.poll() is not None:
            PIDS.discard(self.proc.pid)
            self.proc, self.kind, self.by = None, None, None
            return True
        return False

    def state(self):
        self.reap()
        c = self.cfg()
        k = self.kind if self.on else None
        return {"on": self.on, "kind": k, "name": ("Ruído " + NAMES[k]) if k else None, "by": self.by if self.on else None,
                "volume": c["volume"], "blocked": bool(self.blocked and not self.on), "cfg": c}

    # -- comandos ---------------------------------------------------------------
    def start(self, kind=None, by="manual"):
        c = self.cfg()
        kind = kind or c["kind"]
        if kind not in KINDS or by not in ("manual", "auto"):
            raise ValueError("ruído: tipo inválido")
        self.reap()
        self.blocked = False
        if self.on and self.kind == kind:   # já tocando este tipo: o pedido manual passa a valer (a automação não desliga)
            if by == "manual":
                self.by = "manual"
            return self.state()
        self._stop_proc()
        wav = self._ensure(self.base, kind)
        self.proc = self._spawn(wav)
        PIDS.add(self.proc.pid)
        self.kind, self.by = kind, by
        self._vol(self.proc.pid, c["volume"] / 100)
        log.info("ruído ligado (%s)", by)
        return self.state()

    def _stop_proc(self):
        if self.proc is not None:
            pid = self.proc.pid
            try:
                if self.proc.poll() is None:
                    self.proc.kill()
                    self.proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
            PIDS.discard(pid)
        self.proc, self.kind, self.by = None, None, None

    def stop(self):
        was = self.on
        self._stop_proc()
        self.blocked = False
        if was:
            log.info("ruído desligado")
        return self.state()

    def apply_volume(self):
        if self.on:
            self._vol(self.proc.pid, self.cfg()["volume"] / 100)

    def force(self):
        """"Ligar mesmo assim": só vale se o foco bloqueou o ruído por haver mídia tocando."""
        if not self.blocked:
            raise ValueError("nada para ligar agora")
        return self.start(by="auto")

    # -- modo foco --------------------------------------------------------------
    def would_start(self, focusing):
        """True quando esta leitura é a SUBIDA (foco rodando + ajuste ligado) e o ruído não está tocando: quem chama
        confere se há mídia tocando antes de chamar sync()."""
        return bool(focusing and self.cfg()["focus_on"] and not self._armed and not self.on)

    def sync(self, focusing, media_playing=False):
        """focusing = Pomodoro em foco E rodando. Devolve True se o estado visível mudou."""
        self.reap()
        armed = bool(focusing and self.cfg()["focus_on"])
        rising, falling = armed and not self._armed, self._armed and not armed
        self._armed = armed
        before = (self.on, self.kind, self.blocked)
        if falling:
            self.blocked = False
            if self.on and self.by == "auto":
                self.stop()
        elif rising and not self.on:
            if media_playing:
                self.blocked = True
            else:
                self.start(by="auto")
        return before != (self.on, self.kind, self.blocked)
