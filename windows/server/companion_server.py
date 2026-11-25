"""Companion PC - painel de dados pessoais e atalhos do PC, pela rede local.

Serve o app web (PWA) + WebSocket na porta "web" (8770) - acessível de qualquer
aparelho da rede local ou pelo cabo USB do celular (adb reverse, automático).
Exige token e executa apenas ações de uma lista fechada (ACTIONS). Atalhos de
programas vêm do config.json.

Uso:  pythonw companion_server.py      (ícone na bandeja, sem janela)
      py -3 companion_server.py --console  (no terminal, sem ícone)
"""
import os
import sys
import time

if sys.stderr is None:
    # pythonw não tem console: um erro antes do log começar (import, config) sumiria sem rastro.
    # Vai para companion-server-erro.log, com data e hora.
    try:
        sys.stderr = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "companion-server-erro.log"),
                          "a", encoding="utf-8", buffering=1)

        def _hook(t, v, tb, _orig=sys.excepthook):
            print(time.strftime("%Y-%m-%d %H:%M:%S"), "erro não tratado:", file=sys.stderr)
            _orig(t, v, tb)
            # pacote faltando (ex.: instalado em outra conta/sessão): sem console, avisa numa janela
            if issubclass(t, ImportError) and not os.environ.get("COMPANION_SEM_JANELA"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None, f"O Companion PC não abriu: falta o pacote \"{getattr(v, 'name', None) or v}\".\n\n"
                    "Reinstale num PowerShell (aberto pelo Menu Iniciar), na pasta do Companion-PC:\n"
                    "powershell -ExecutionPolicy Bypass -File windows\\install-server.ps1\n\n"
                    "Detalhes em companion-server-erro.log.", "Companion PC", 0x10)
        sys.excepthook = _hook
    except OSError:
        pass

if __name__ == "__main__" and "--supervise" in sys.argv:
    # Supervisor: processo leve que só sobe este mesmo script como filho e o religa se cair (supervisor.py)
    import logging
    import logging.handlers
    import supervisor
    _here = os.path.dirname(os.path.abspath(__file__))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.handlers.RotatingFileHandler(os.path.join(_here, "companion-supervisor.log"), maxBytes=200_000,
                                             backupCount=1, encoding="utf-8")]
        + ([logging.StreamHandler(sys.stdout)] if sys.stdout else []))
    if supervisor.acquire():
        _args = [a for a in sys.argv[1:] if a != "--supervise"]
        sys.exit(supervisor.run(os.path.abspath(__file__), _args))
    if "--console" in sys.argv:
        sys.exit(0)      # já há um supervisor: no modo console não faz nada
    sys.argv.remove("--supervise")   # atalho aberto de novo: segue o fluxo normal (mostra o painel da instância viva)

import asyncio
import ctypes
import json
import logging
import logging.handlers
import platform
import re
import secrets
import shutil
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from http import HTTPStatus
from pathlib import Path

import psutil
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.exceptions import ConnectionClosed

import alerts
import multipc
import audio_profiles
import backup
import dota
import dota_img
import extras
import icons
import games
import pair
import pc_info
import steam_dl
import watchers
import pomodoro
import tasks
import trello
import weather
import work

VERSION = "1.6"
BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"
LOG_PATH = BASE / "companion-server.log"
WEB_DIR = BASE / "web"

DEFAULT_CFG = {
    "token": "",
    "allow_power": True,
    "shutdown_delay_s": 30,
    # App do celular. host "0.0.0.0" = rede local + cabo; "127.0.0.1" = só cabo (adb).
    "web": {"host": "0.0.0.0", "port": 8770, "adb_reverse": True, "adb_path": ""},
    "upload_dir": "%USERPROFILE%\\Downloads\\Companion PC",  # arquivos enviados pelo celular
    "downloads_dir": "%USERPROFILE%\\Downloads",               # avisa no celular quando um download termina
    # Teclas que o servidor aperta para o Discord. Configure as MESMAS como
    # atalhos globais no Discord (Configurações > Atalhos de teclado).
    "discord": {"mute_keys": "ctrl+shift+f9", "deafen_keys": "ctrl+shift+f10"},
    "games_ignore": games.DEFAULT_IGNORE,
    # Ponte do DotaCopilot (mesmo PC, só leitura); auto_open = o app vai para a tela Dota ao abrir o jogo.
    "dota": dict(dota.DEFAULT_CFG),
    # Previsão (Open-Meteo): só a cidade que o usuário escolhe no app (place = {name, admin, country, lat, lon} ou null).
    "weather": dict(weather.DEFAULT_CFG),
    "game_folders": [],
    "shortcuts": [
        {"id": "explorer", "name": "Explorador de Arquivos", "open": "explorer.exe"},
        {"id": "downloads", "name": "Pasta Downloads", "open": "%USERPROFILE%\\Downloads"},
        {"id": "calc", "name": "Calculadora", "open": "calc.exe"},
        {"id": "notepad", "name": "Bloco de Notas", "open": "notepad.exe"},
        {"id": "taskmgr", "name": "Gerenciador de Tarefas", "open": "taskmgr.exe"},
        {"id": "settings", "name": "Configurações do Windows", "open": "ms-settings:"},
        {"id": "youtube", "name": "YouTube", "open": "https://www.youtube.com"},
    ],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    + ([logging.StreamHandler(sys.stdout)] if sys.stdout else []),  # pythonw: sem stdout
)
log = logging.getLogger("companion")
logging.getLogger("websockets").setLevel(logging.WARNING)  # sem "connection open/closed" a cada pedido


class ConfigError(Exception):
    pass


def load_config():
    cfg = dict(DEFAULT_CFG)
    if CFG_PATH.exists():
        # config.json quebrado: só avisa. Nunca regravar (o token e os atalhos do usuário estão nele).
        try:
            user = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{CFG_PATH} tem um erro: {e.msg} (linha {e.lineno}, coluna {e.colno})") from None
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(f"não consegui ler {CFG_PATH}: {e}") from None
        if not isinstance(user, dict):
            raise ConfigError(f"{CFG_PATH} precisa ser um objeto JSON ({{ ... }})")
        cfg.update(user)
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(24)
        CFG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("token novo gerado em %s", CFG_PATH)
    elif not CFG_PATH.exists():
        CFG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    if not isinstance(cfg.get("panel_token"), str) or len(cfg["panel_token"]) < 20:
        # credencial só do painel do PC (janela local): grava só esta chave, o resto do arquivo fica como está
        cfg["panel_token"] = secrets.token_urlsafe(24)
        raw = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        raw["panel_token"] = cfg["panel_token"]
        tmp = CFG_PATH.with_name("config.json.tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CFG_PATH)
        log.info("panel_token novo gerado")
    if not multipc.valid_pc_id(cfg.get("pc_id")):
        # id deste PC para o app com vários PCs: grava só esta chave, o resto do arquivo fica como está
        cfg["pc_id"] = multipc.new_pc_id()
        raw = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        raw["pc_id"] = cfg["pc_id"]
        tmp = CFG_PATH.with_name("config.json.tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CFG_PATH)
        log.info("pc_id novo gerado")
    if not isinstance(cfg.get("dota"), dict) or cfg["dota"] is DEFAULT_CFG["dota"]:
        # grava só esta chave (o resto do arquivo fica como está), para a opção aparecer no config.json
        raw = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw.get("dota"), dict):
            raw["dota"] = dict(dota.DEFAULT_CFG)
            tmp = CFG_PATH.with_name("config.json.tmp")
            tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CFG_PATH)
        cfg["dota"] = raw["dota"]
    # previsão: completa com os padrões (cfg["weather"] é o mesmo dict que o Weather usa) e grava a chave se faltar
    user_w = json.loads(CFG_PATH.read_text(encoding="utf-8")).get("weather")
    cfg["weather"] = {**weather.DEFAULT_CFG, **(user_w if isinstance(user_w, dict) else {})}
    if not isinstance(user_w, dict):
        raw = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        raw["weather"] = dict(weather.DEFAULT_CFG)
        tmp = CFG_PATH.with_name("config.json.tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CFG_PATH)
    return cfg


def config_fatal(msg):
    """Sem config não dá para rodar: registra no log, avisa na tela (pythonw não tem console) e sai."""
    log.error("%s - corrija o arquivo e abra o Companion PC de novo (o arquivo não foi alterado)", msg)
    if "--console" not in sys.argv:
        ctypes.windll.user32.MessageBoxW(
            None, f"{msg}\n\nCorrija o arquivo e abra o Companion PC de novo.\nO arquivo não foi alterado.",
            "Companion PC", 0x10)  # MB_ICONERROR
    sys.exit(1)


try:
    CFG = load_config()
except ConfigError as e:
    config_fatal(str(e))
WEB = {**DEFAULT_CFG["web"], **(CFG.get("web") or {})}

# --------------------------------------------------------------------------
# Win32: teclado, mouse, energia
# --------------------------------------------------------------------------
user32 = ctypes.windll.user32
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2
VK = {
    "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "win": 0x5B, "alt": 0x12, "ctrl": 0x11, "shift": 0x10,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},  # VK das letras = ASCII maiúsculo
    **{str(d): 0x30 + d for d in range(10)},
    "insert": 0x2D, "printscreen": 0x2C, "menu": 0x5D, "plus": 0xBB, "minus": 0xBD,
    "comma": 0xBC, "period": 0xBE,
    "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
    "vol_mute": 0xAD, "vol_down": 0xAE, "vol_up": 0xAF,
}
EXTENDED = {"delete", "home", "end", "pageup", "pagedown", "up", "down", "left", "right", "win",
            "insert", "printscreen", "menu",
            "play_pause", "next", "prev", "stop", "vol_mute", "vol_down", "vol_up"}
# Pontuação: o VK muda com o layout do teclado (ABNT2, US...). Resolve no layout atual
# com VkKeyScanW e só registra a tecla se ela sair sem modificador (sem Shift/AltGr).
VK_EXTRA_CHARS = {"colchete_esq": "[", "colchete_dir": "]", "barra_inv": "\\", "ponto_virgula": ";"}


def _vk_extra():
    out = {}
    for name, ch in VK_EXTRA_CHARS.items():
        r = ctypes.c_short(user32.VkKeyScanW(ord(ch))).value  # SHORT: byte baixo = VK, alto = modificadores
        if r == -1 or (r >> 8) & 0xFF:
            log.info("tecla %s (%s) não registrada: neste layout precisa de modificador ou não existe", name, ch)
            continue
        out[name] = r & 0xFF
    return out


VK.update(_vk_extra())
MODIFIERS = {"alt", "ctrl", "shift", "win"}
held_modifiers: set[str] = set()


def _key(name, up=False):
    flags = (KEYEVENTF_EXTENDEDKEY if name in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
    # Scan code junto com o VK: apps que leem o teclado em baixo nível (atalhos
    # globais do Discord) ignoram eventos com scan code 0.
    scan = user32.MapVirtualKeyW(VK[name], 0) & 0xFF  # MAPVK_VK_TO_VSC
    user32.keybd_event(VK[name], scan, flags, 0)


def valid_combo(combo: str):
    keys = [k.strip().lower() for k in str(combo).split("+") if k.strip()]
    if not keys or any(k not in VK for k in keys):
        raise ValueError(f"tecla não permitida: {combo}")
    return keys


def press_combo(combo: str):
    keys = valid_combo(combo)
    # Pequenas pausas: o Discord não reconhece um atalho pressionado e solto
    # no mesmo instante.
    for k in keys:
        _key(k)
        time.sleep(0.02)
    time.sleep(0.05)
    for k in reversed(keys):
        _key(k, up=True)
        time.sleep(0.01)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        # MOUSEINPUT é o maior membro da união: reserva o tamanho certo
        _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
KEYEVENTF_UNICODE = 0x4


def type_text(text: str):
    """Digita texto qualquer (acentos, emoji) como eventos Unicode.
    "\\n" vira Enter e "\\b" vira Backspace."""
    inputs = []

    def flush():
        if inputs:
            _send(inputs)
            inputs.clear()

    for ch in str(text)[:1000].replace("\r\n", "\n"):
        if ch in "\n\b":
            flush()
            press_combo("enter" if ch == "\n" else "backspace")
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):  # emoji = par substituto (2 unidades)
            unit = int.from_bytes(data[i:i + 2], "little")
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = _INPUT(type=1)  # INPUT_KEYBOARD
                inp.ki = _KEYBDINPUT(0, unit, flags, 0, 0)
                inputs.append(inp)
    flush()


def _send(inputs):
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def modifier(name, down):
    """Segura/solta Alt, Ctrl, Shift ou Win (ex.: Alt segurado para Alt+Tab)."""
    if name not in MODIFIERS:
        raise ValueError("só modificadores podem ser segurados")
    _key(name, up=not down)
    (held_modifiers.add if down else held_modifiers.discard)(name)


def release_modifiers():
    for k in list(held_modifiers):
        _key(k, up=True)
    held_modifiers.clear()


MOUSE = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}


def _u32(v):
    return int(v) & 0xFFFFFFFF


def mouse_move(dx, dy):
    dx, dy = max(-400, min(400, int(dx))), max(-400, min(400, int(dy)))
    user32.mouse_event(0x1, _u32(dx), _u32(dy), 0, 0)


def mouse_button(button="left", state="click"):
    down, up = MOUSE[button]
    if state in ("click", "down"):
        user32.mouse_event(down, 0, 0, 0, 0)
    if state in ("click", "up"):
        user32.mouse_event(up, 0, 0, 0, 0)


def mouse_scroll(dy=0, dx=0):
    if dy:
        user32.mouse_event(0x800, 0, 0, _u32(max(-1200, min(1200, int(dy)))), 0)
    if dx:
        user32.mouse_event(0x1000, 0, 0, _u32(max(-1200, min(1200, int(dx)))), 0)


def foreground_title():
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def foreground_app():
    """(nome do exe em minúsculas, caminho, pid) do programa em foco."""
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    try:
        p = psutil.Process(pid.value)
        return p.name().lower(), p.exe(), pid.value
    except (psutil.Error, OSError):
        return "", None, 0


def power(action):
    if not CFG.get("allow_power"):
        raise PermissionError("ações de energia desativadas no config.json")
    delay = str(int(CFG.get("shutdown_delay_s", 30)))

    def run(*args):
        subprocess.run(["shutdown", *args], creationflags=pc_info.NO_WINDOW, capture_output=True)

    if action == "lock":
        user32.LockWorkStation()
    elif action == "monitor_off":
        user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)
    elif action == "sleep":
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    elif action == "shutdown":
        run("/s", "/t", delay, "/c", "Desligamento pedido pelo Companion PC")
    elif action == "restart":
        run("/r", "/t", delay, "/c", "Reinício pedido pelo Companion PC")
    elif action == "cancel":
        run("/a")
    else:
        raise ValueError(f"ação de energia inválida: {action}")


def shortcut_icon(sc):
    """ID do ícone de um programa do deck: só quando o atalho abre um .exe/.lnk conhecido (caminho absoluto ou nome
    resolvido pelo PATH, como o Windows faria). URL, pasta, app da Loja e o resto ficam sem ícone."""
    try:
        if sc.get("appx"):
            return None
        t = os.path.expandvars(str(sc.get("open") or ""))
        if not t.lower().endswith((".exe", ".lnk")):
            return None
        path = t if os.path.isabs(t) else (shutil.which(t) if not os.path.dirname(t) else None)
        return ICONS.register(path) if path else None
    except Exception:  # noqa: BLE001
        return None


def launch(shortcut_id):
    sc = next((s for s in CFG["shortcuts"] if s.get("id") == shortcut_id), None)
    if not sc:
        raise ValueError("atalho não configurado")
    target = os.path.expandvars(sc["open"])
    if sc.get("appx"):  # app da Microsoft Store (WhatsApp, Spotify, Terminal...)
        os.startfile("shell:AppsFolder\\" + sc["appx"])
        return sc["name"]
    kw = {}
    if sc.get("args"):
        kw["arguments"] = sc["args"]
    if target.lower().endswith(".exe") and os.path.isabs(target):
        kw["cwd"] = os.path.dirname(target)  # OBS e outros só abrem a partir da própria pasta
    os.startfile(target, **kw)
    return sc["name"]


# --------------------------------------------------------------------------
# Estatísticas
# --------------------------------------------------------------------------
class Stats:
    def __init__(self):
        psutil.cpu_percent(None)
        self.cores = psutil.cpu_count() or 1
        self.last_net = psutil.net_io_counters()
        self.last_t = time.monotonic()
        self.pcache: dict[int, psutil.Process] = {}  # cache próprio (não usa o do process_iter)
        self.slow: set[int] = set()  # processos protegidos: psutil cai num caminho lento (~5 ms cada)
        self._top_procs()

    def _app(self):
        name, exe, self._fg_pid = foreground_app()
        game = False
        if exe and name not in ("explorer.exe", "applicationframehost.exe"):
            try:
                game = bool(MON.catalog.match(exe))
            except Exception:  # noqa: BLE001
                game = False
        self.fg_pid = self._fg_pid if game else 0
        return {"app": name, "app_game": game}

    def _top_procs(self):
        procs, pids = [], set(psutil.pids())
        for pid in list(self.pcache):
            if pid not in pids:
                del self.pcache[pid]
        self.slow &= pids
        for pid in pids:
            if pid == 0 or pid in self.slow:
                continue
            t0 = time.perf_counter()
            p = self.pcache.get(pid)
            try:
                if p is None:
                    p = self.pcache[pid] = psutil.Process(pid)
                    p.cached_name = p.name()  # nome não muda; ler uma vez só
                with p.oneshot():
                    cpu = p.cpu_percent(None) / self.cores
                    procs.append((cpu, p.memory_info().rss, p.cached_name, pid))
                # só descarta processos lentos E ociosos: com a CPU alta qualquer leitura passa de 3 ms
                # e os processos pesados (os de "Mais pesados") eram descartados para sempre
                if time.perf_counter() - t0 > 0.003 and cpu < 1 and pid in self.pcache and hasattr(p, "seen"):
                    self.slow.add(pid)
                p.seen = True
            except psutil.Error:
                self.pcache.pop(pid, None)
        procs.sort(reverse=True)
        return procs

    def _proc_icon(self, pid):
        """ID do ícone do processo (o caminho do exe fica aqui; lido uma vez por processo, só para os que aparecem na lista)."""
        p = self.pcache.get(pid)
        if p is None:
            return None
        if not hasattr(p, "cached_exe"):
            try:
                p.cached_exe = p.exe()
            except (psutil.Error, OSError):
                p.cached_exe = None
        return ICONS.register(p.cached_exe) if p.cached_exe else None

    def collect(self):
        now = time.monotonic()
        net = psutil.net_io_counters()
        dt = max(now - self.last_t, 0.001)
        up = (net.bytes_sent - self.last_net.bytes_sent) / dt
        down = (net.bytes_recv - self.last_net.bytes_recv) / dt
        self.last_net, self.last_t = net, now

        vm = psutil.virtual_memory()
        disks = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({"name": part.mountpoint.rstrip("\\"), "used": u.used, "total": u.total, "pct": u.percent})

        procs = self._top_procs()

        bat = psutil.sensors_battery()
        freq = psutil.cpu_freq()
        return {
            "time": time.strftime("%H:%M:%S"),
            "date": time.strftime("%d/%m/%Y"),
            "uptime_s": int(time.time() - psutil.boot_time()),
            "cpu": psutil.cpu_percent(None),
            "cpu_mhz": int(freq.current) if freq else 0,
            "ram_used": vm.used, "ram_total": vm.total, "ram_pct": vm.percent,
            "disks": disks,
            "net_up": up, "net_down": down,
            "battery": {"pct": bat.percent, "plugged": bat.power_plugged} if bat else None,
            "window": foreground_title(),
            **self._app(),
            "gpu": pc_info.gpu(),
            "procs": [{"name": n, "cpu": round(c, 1), "mem": m, **({"icon": i} if i else {})}
                      for c, m, n, pid in procs[:5] for i in [self._proc_icon(pid)]],
            "sensors": pc_info.sensors(),
        }


# --------------------------------------------------------------------------
# Ações permitidas
# --------------------------------------------------------------------------
ACTIONS = {
    "media.vol_up": lambda p: press_combo("vol_up"),
    "media.vol_down": lambda p: press_combo("vol_down"),
    "key.press": lambda p: press_combo(p["keys"]),
    "key.text": lambda p: type_text(p.get("text", "")),
    "key.down": lambda p: modifier(p["key"], down=True),
    "key.up": lambda p: modifier(p["key"], down=False),
    "mouse.move": lambda p: mouse_move(p.get("dx", 0), p.get("dy", 0)),
    "mouse.button": lambda p: mouse_button(p.get("button", "left"), p.get("state", "click")),
    "mouse.scroll": lambda p: mouse_scroll(p.get("dy", 0), p.get("dx", 0)),
    "app.launch": lambda p: launch(p["id"]),
    "power": lambda p: power(p["action"]),
    "discord.mute": lambda p: press_combo(CFG["discord"]["mute_keys"]),
    "discord.deafen": lambda p: press_combo(CFG["discord"]["deafen_keys"]),
    "discord.open": lambda p: open_discord(),
    # celular: deck, área de transferência, arquivos
    "deck.get": lambda p: deck_payload(),
    "deck.save": lambda p: {**deck_payload(DECK.save(p["deck"], valid_combo, shortcut_map()))},
    "deck.reset": lambda p: deck_payload(DECK.reset()),
    "clip.set": lambda p: clip_set(p),
    "file.begin": lambda p: UPLOADS.begin(p["name"], p["size"], p.get("_ws")),
    "file.chunk": lambda p: UPLOADS.chunk(p["id"], p["data"]),
    "file.end": lambda p: UPLOADS.end(p["id"]),
    "file.cancel": lambda p: UPLOADS.cancel(p["id"]),
    "file.open_folder": lambda p: UPLOADS.open_folder(),
    # tempo de trabalho (work.py): roda na fila de disco, como todo o resto do WORK
    "work.get": lambda p: _wi(WORK.get()),
    "work.week": lambda p: _wi(WORK.week(p.get("offset", 0))),
    "work.csv": lambda p: work_csv(p.get("offset", 0)),
    "work.month": lambda p: _wi(WORK.month(p.get("ym"), notes=False)),   # celular: só os totais, sem notas
    "trello.get": lambda p: trello_get(p),
    "trello.open": lambda p: trello_open(p),
    # tarefas (tasks.py): o celular só vê e marca/desmarca
    "tasks.get": lambda p: TASKS.get(),
    "tasks.toggle": lambda p: TASKS.toggle(p["id"]),
    "export.link": lambda p: export_link(p),
    "export.reveal": lambda p: export_reveal(p),
}


def trello_state():
    """Na fila de disco (o histórico de trabalho só é lido ali): estado do Trello + tempo dos jobs ligados aos cards."""
    return TRELLO.state(times=WORK.link_times())


def work_link_set(client, job, card, _name=""):
    """Liga o job ao card; o nome do card vem do cache do Trello (nunca do painel), e o card precisa estar nele."""
    if not TRELLO.has_card(card):
        raise ValueError("esse card não está entre os seus cards do Trello")
    return WORK.link_set(client, job, card, TRELLO.card_name(card))


def trello_get(p):
    """Estado do Trello para o celular (ao abrir Tarefas busca de novo, no máximo 1x por minuto)."""
    if p.get("refresh"):
        TRELLO.refresh_manual()
    return trello_state()


def trello_open(p):
    """Abre o card no navegador do PC: só shortLink que está no cache dos meus cards (nunca URL livre)."""
    short = p.get("short")
    if not TRELLO.has_card(short):
        raise ValueError("card desconhecido")
    os.startfile("https://trello.com/c/" + short)
    return {"ok": True}


def trello_test(p):
    """Botão "Testar" do painel: usa a key/token digitados ou, em branco, os salvos. Devolve só o nome do usuário."""
    cur = CFG.get("trello") if isinstance(CFG.get("trello"), dict) else {}
    key = trello.valid_cred(p["key"], "API key") if isinstance(p.get("key"), str) and p["key"].strip() else cur.get("key", "")
    token = trello.valid_cred(p["token"], "token") if isinstance(p.get("token"), str) and p["token"].strip() else cur.get("token", "")
    if not key or not token:
        raise ValueError("Informe a API key e o token")
    try:
        return {"name": TRELLO.test(key, token)}
    except trello.CredError:
        raise ValueError("Credencial inválida (confira a key e o token de leitura)") from None
    except trello.NetError:
        raise ValueError("Não consegui falar com o Trello agora") from None


def work_month_csv(ym):
    name, data = WORK.csv_month(ym)
    return {"name": name, "url": extras.dl_register(name, data=data, ctype="text/csv; charset=utf-8")}


def work_csv(offset):
    """CSV da semana por um link temporário /dl/ (o mesmo do "Pegar o que está copiado")."""
    name, data = WORK.csv(offset)
    return {"name": name, "url": extras.dl_register(name, data=data, ctype="text/csv; charset=utf-8")}


# --------------------------------------------------------------------------
# config.json editado pelo painel do PC: um conjunto fechado de opções
# --------------------------------------------------------------------------
CONFIG_EDITABLE = {"upload_dir", "downloads_dir", "allow_power", "shutdown_delay_s", "notif_ignore", "shortcuts",
                   "idle_min", "export_watch", "misplaced_min", "backup_dir", "trello", "pc_name", "allowed_origins"}
CONFIG_RESTART = {"upload_dir", "downloads_dir"}  # lidas só na abertura do servidor
SHORTCUT_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_cfg_lock = threading.Lock()


def _text(v, what, max_len, allow_empty=False):
    if not isinstance(v, str) or _CTRL.search(v) or len(v) > max_len or (not v.strip() and not allow_empty):
        raise ValueError(f"{what} inválido")
    return v.strip()


def clean_config_value(key, v):
    """Validação das opções editáveis (a mesma para qualquer tela)."""
    if key in ("upload_dir", "downloads_dir"):
        v = _text(v, "caminho da pasta", 260)
        if not (re.match(r"^[A-Za-z]:\\", v) or v.startswith("%USERPROFILE%")):
            raise ValueError("a pasta precisa ser um caminho completo")
        return v
    if key == "allow_power":
        if not isinstance(v, bool):
            raise ValueError("allow_power precisa ser sim/não")
        return v
    if key == "shutdown_delay_s":
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 600:
            raise ValueError("atraso de desligar: 0 a 600 segundos")
        return v
    if key == "notif_ignore":
        if not isinstance(v, list) or len(v) > 50:
            raise ValueError("até 50 apps ignorados")
        return [_text(a, "nome de app", 80) for a in v]
    if key == "shortcuts":
        if not isinstance(v, list) or len(v) > 50:
            raise ValueError("até 50 programas")
        out, ids = [], set()
        for s in v:
            if not isinstance(s, dict):
                raise ValueError("programa inválido")
            sid = s.get("id")
            if not isinstance(sid, str) or not SHORTCUT_ID.match(sid) or sid in ids:
                raise ValueError("id de programa inválido ou repetido")
            ids.add(sid)
            c = {"id": sid, "name": _text(s.get("name"), "nome do programa", 60),
                 "open": _text(s.get("open"), "caminho do programa", 300)}
            for opt in ("args", "appx"):
                if s.get(opt):
                    c[opt] = _text(s[opt], opt, 300)
            out.append(c)
        return out
    if key == "pc_name":
        return multipc.clean_pc_name(v)
    if key == "allowed_origins":
        return multipc.clean_origins(v)
    if key == "idle_min":
        return work.valid_idle_min(v)
    if key == "misplaced_min":
        return alerts.valid_misplaced_min(v)
    if key == "trello":   # key/token em branco = mantém os já salvos; nunca voltam ao painel nem ao celular
        if not isinstance(v, dict) or set(v) - {"key", "token", "on"} or not isinstance(v.get("on"), bool):
            raise ValueError("Trello: formato inválido")
        cur = CFG.get("trello") if isinstance(CFG.get("trello"), dict) else {}
        out = {"on": v["on"]}
        for k, label in (("key", "API key"), ("token", "token")):
            new = v.get(k)
            out[k] = trello.valid_cred(new, label) if isinstance(new, str) and new.strip() else cur.get(k, "")
        if v["on"] and not (out["key"] and out["token"]):
            raise ValueError("Informe a API key e o token para ligar o Trello")
        return out
    if key == "backup_dir":   # "" = sem backup
        if v == "":
            return ""
        v = _text(v, "caminho da pasta", 260)
        if not re.match(r"^[A-Za-z]:\\", v):
            raise ValueError("a pasta precisa ser um caminho completo")
        return v
    if key == "export_watch":
        if not isinstance(v, dict) or set(v) - {"folders", "on"} or not isinstance(v.get("on"), bool):
            raise ValueError("exportação: formato inválido")
        fl = v.get("folders")
        if not isinstance(fl, list) or len(fl) > extras.EXPORT_MAX_FOLDERS:
            raise ValueError(f"no máximo {extras.EXPORT_MAX_FOLDERS} pastas de exportação")
        out = []
        for f in fl:
            f = _text(f, "caminho da pasta", 260)
            if not re.match(r"^[A-Za-z]:\\", f):
                raise ValueError("a pasta precisa ser um caminho completo")
            if f.casefold() in {o.casefold() for o in out}:
                raise ValueError("essa pasta já está na lista")
            out.append(f)
        return {"folders": out, "on": v["on"]}
    raise ValueError(f"opção não editável: {key}")


def config_save(changes):
    """Único caminho de gravação do config.json depois da abertura. Roda na fila de disco, com lock.
    Lê o arquivo do disco e troca só as chaves pedidas: token, web, shortcuts não editados etc. ficam iguais.
    Antes de gravar: config.json.bak-<data-hora>. Gravação atômica (.tmp + replace)."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("nada para salvar")
    clean = {k: clean_config_value(k, v) for k, v in changes.items()}
    with _cfg_lock:
        cur = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        changed = sorted(k for k, v in clean.items() if cur.get(k) != v)
        old_trello = dict(cur.get("trello") or {})
        if not changed:
            return {"changed": [], "restart": False}
        shutil.copy2(CFG_PATH, CFG_PATH.with_name("config.json.bak-" + time.strftime("%Y%m%d-%H%M%S")))
        cur.update({k: clean[k] for k in changed})
        tmp = CFG_PATH.with_name("config.json.tmp")
        tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CFG_PATH)
        CFG.update({k: clean[k] for k in changed})
    if "idle_min" in changed:
        WORK.set_idle_min(clean["idle_min"])
    if "trello" in changed and any(old_trello.get(k) != clean["trello"].get(k) for k in ("key", "token")):
        TRELLO.reset()     # credencial nova: tenta de novo e esquece os dados da conta anterior
    if set(changed) & {"shortcuts", "allow_power", "pc_name"} and LOOP[0]:
        asyncio.run_coroutine_threadsafe(push_welcome(), LOOP[0])
    log.info("config.json: alterado pelo painel (%s)", ", ".join(changed))
    return {"changed": changed, "restart": bool(set(changed) & CONFIG_RESTART)}


def config_get():
    """O que a aba Configurações mostra: só as opções editáveis (nunca o token) e a rede, só leitura."""
    # opção ausente no config.json (ou null / tipo errado): a tela mostra o padrão em vez de quebrar
    fallback = {"upload_dir": DEFAULT_CFG["upload_dir"], "downloads_dir": DEFAULT_CFG["downloads_dir"],
                "allow_power": DEFAULT_CFG["allow_power"], "shutdown_delay_s": DEFAULT_CFG["shutdown_delay_s"],
                "notif_ignore": [], "shortcuts": [], "idle_min": 5,
                "export_watch": {"folders": [], "on": True}, "misplaced_min": alerts.DEFAULT_MISPLACED_MIN, "backup_dir": "", "trello": {}, "pc_name": "", "allowed_origins": []}
    out = {}
    for k in CONFIG_EDITABLE:
        v = CFG.get(k)
        ok = isinstance(v, type(fallback[k])) and not (isinstance(v, bool) and not isinstance(fallback[k], bool))
        out[k] = json.loads(json.dumps(v if ok else fallback[k]))
    out["idle_min"] = round(WORK.idle_s / 60)
    out["backup"] = BACKUP.status()
    out["trello"] = TRELLO.status()    # só "tem key/token?" e o estado: as credenciais nunca saem do config.json
    out["net"] = {"ip": lan_ip(), "port": WEB["port"],
                  "mode": "só cabo (127.0.0.1)" if WEB["host"] in ("127.0.0.1", "localhost") else "rede local + cabo"}
    return out


def io_call(fn, *args, timeout=15):
    """Painel do PC (thread do tkinter): roda na fila de disco, a mesma das ações do celular, e espera."""
    return IO_POOL.submit(fn, *args).result(timeout=timeout)


PAIR = pair.Pairing()

PANEL_OPS = {
    "pair_new": PAIR.new, "pair_state": PAIR.state, "pair_close": PAIR.close,   # só memória: sem fila de disco
    "work_get": lambda: _wi(io_call(WORK.get)),
    "work_week": lambda offset: _wi(io_call(WORK.week, offset)),
    "work_csv": lambda offset: io_call(WORK.csv, offset),
    "work_month": lambda ym: _wi(io_call(WORK.month, ym)),
    "work_month_csv": lambda ym: io_call(work_month_csv, ym),
    "work_note_set": lambda day, text: io_call(WORK.note_set, day, text),
    "work_links_get": lambda: io_call(WORK.links_get),
    "work_link_set": lambda client, job, card, name: io_call(work_link_set, client, job, card, name),
    "work_link_delete": lambda ids: io_call(WORK.link_delete, ids),
    "work_link_restore": lambda entries: io_call(WORK.link_restore, entries),
    "trello_cards": lambda: io_call(lambda: {"cards": TRELLO.all_cards(), "status": TRELLO.status()}),
    "work_add_root": lambda folder: io_call(WORK.add_root, folder),
    "work_remove_root": lambda folder: io_call(WORK.remove_root, folder),
    "work_reclass_preview": lambda: io_call(WORK.reclass_preview, timeout=60),  # reindexa as raízes
    "work_reclass_apply": lambda: io_call(WORK.reclass_apply),
    "work_assign_preview": lambda items, offset: io_call(WORK.assign_preview, items, offset),
    "work_assign": lambda items, client, job, remember, offset: io_call(WORK.assign, items, client, job, remember, offset),
    "work_ignore": lambda items, offset: io_call(WORK.ignore, items, offset),
    "work_rules_get": lambda: io_call(WORK.rules_get),
    "work_rule_delete": lambda ids: io_call(WORK.rule_delete, ids),
    "work_unignore": lambda ids: io_call(WORK.unignore, ids),
    "tasks_get": lambda: io_call(TASKS.get),
    "tasks_add": lambda text: io_call(TASKS.add, text),
    "tasks_edit": lambda tid, text: io_call(TASKS.edit, tid, text),
    "tasks_remove": lambda tid: io_call(TASKS.remove, tid),
    "tasks_move": lambda tid, delta: io_call(TASKS.move, tid, delta),
    "tasks_toggle": lambda tid: io_call(TASKS.toggle, tid),
    "tasks_clear_done": lambda: io_call(TASKS.clear_done),
    "config_save": lambda changes: io_call(config_save, changes),
    "config_get": lambda: io_call(config_get),
    "backup_run": lambda: io_call(do_backup, timeout=60),
    "tasks_restore": lambda tid, text, done, pos: io_call(TASKS.restore, tid, text, done, pos),
    "pomo_tip": lambda: pomo_tip(),
    "panel_url": lambda: f"http://127.0.0.1:{int(os.environ.get('COMPANION_WEB_PORT', WEB['port']))}/#panel={CFG['panel_token']}",
}

# --------------------------------------------------------------------------
# Modo painel (janela do Edge no PC): operações de gerenciamento pela WebSocket
# Só aceitas de uma conexão que veio de loopback E mandou o panel_token no hello.
# (o celular pelo cabo também chega por loopback: só o panel_token diferencia)
# --------------------------------------------------------------------------
LOOPBACK = ("127.0.0.1", "::1")
panel_clients = set()
_qr_cache = {}


def panel_qr(url):
    """QR do link do celular como data URI PNG (o link leva o token do celular: só vai ao painel autenticado)."""
    if url not in _qr_cache:
        _qr_cache.clear()
        try:
            import base64
            import io
            import qrcode
            q = qrcode.QRCode(border=2, box_size=6)
            q.add_data(url)
            buf = io.BytesIO()
            q.make_image(fill_color="black", back_color="white").get_image().save(buf, "PNG")
            _qr_cache[url] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:  # noqa: BLE001  (qrcode/PIL não instalado)
            _qr_cache[url] = None
    return _qr_cache[url]


def panel_status():
    st = status()
    links = st["phone"].get("links") or {}
    main = links.get("wifi") or links.get("cabo")
    return {"state": st["state"], "text": st["text"], "game": st["game"], "version": VERSION,
            "host": socket.gethostname(),
            "phone": {"web": st["phone"]["web"], "adb": bool(st["phone"]["adb"]), "port": st["phone"]["port"],
                      "wifi": "wifi" in links, "link": main.split("#")[0] if main else None,
                      "qr": panel_qr(links["wifi"]) if "wifi" in links else None},
            "pair": PAIR.state()}


def pick_folder():
    """Seletor de pasta nativo no PC (só a pedido do modo painel). Devolve o caminho ou None."""
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        d = filedialog.askdirectory(title="Escolher pasta", mustexist=True, parent=root)
    finally:
        root.destroy()
    return os.path.normpath(d) if d else None


def _need(p, key, typ):
    v = p.get(key)
    if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
        raise ValueError(f"parâmetro inválido: {key}")
    return v


PANEL_WS = {   # nome -> função(params dict); as de gravação rodam na fila de disco como as do painel tkinter
    "panel_status": lambda p: panel_status(),
    "pair_new": lambda p: PAIR.new() and PAIR.state(),
    "pair_state": lambda p: PAIR.state(),
    "tasks_get": lambda p: PANEL_OPS["tasks_get"](),
    "tasks_add": lambda p: PANEL_OPS["tasks_add"](_need(p, "text", str)),
    "tasks_edit": lambda p: PANEL_OPS["tasks_edit"](_need(p, "id", str), _need(p, "text", str)),
    "tasks_remove": lambda p: PANEL_OPS["tasks_remove"](_need(p, "id", str)),
    "tasks_move": lambda p: PANEL_OPS["tasks_move"](_need(p, "id", str), _need(p, "delta", int)),
    "tasks_toggle": lambda p: PANEL_OPS["tasks_toggle"](_need(p, "id", str)),
    "tasks_clear_done": lambda p: PANEL_OPS["tasks_clear_done"](),
    "tasks_restore": lambda p: PANEL_OPS["tasks_restore"](p.get("id"), p.get("text"), p.get("done"), p.get("pos")),
    "work_get": lambda p: PANEL_OPS["work_get"](),
    "work_week": lambda p: PANEL_OPS["work_week"](_need(p, "offset", int)),
    "work_csv": lambda p: io_call(work_csv, _need(p, "offset", int)),
    "work_month": lambda p: PANEL_OPS["work_month"](p.get("ym")),
    "work_month_csv": lambda p: PANEL_OPS["work_month_csv"](p.get("ym")),
    "work_note_set": lambda p: PANEL_OPS["work_note_set"](_need(p, "day", str), _need(p, "text", str)),
    "work_links_get": lambda p: PANEL_OPS["work_links_get"](),
    "work_link_set": lambda p: PANEL_OPS["work_link_set"](_need(p, "client", str), p.get("job") or "", _need(p, "card", str), ""),
    "work_link_delete": lambda p: PANEL_OPS["work_link_delete"](p.get("ids") if "ids" in p else _need(p, "id", str)),
    "work_link_restore": lambda p: PANEL_OPS["work_link_restore"](_need(p, "entries", list)),
    "trello_cards": lambda p: PANEL_OPS["trello_cards"](),
    "work_add_root": lambda p: PANEL_OPS["work_add_root"](_need(p, "folder", str)),
    "work_remove_root": lambda p: PANEL_OPS["work_remove_root"](_need(p, "folder", str)),
    "work_assign_preview": lambda p: PANEL_OPS["work_assign_preview"](_need(p, "items", list), _need(p, "offset", int)),
    "work_assign": lambda p: PANEL_OPS["work_assign"](_need(p, "items", list), _need(p, "client", str), p.get("job") or "",
                                                       p.get("remember") is not False, _need(p, "offset", int)),
    "work_ignore": lambda p: PANEL_OPS["work_ignore"](_need(p, "items", list), _need(p, "offset", int)),
    "work_rules_get": lambda p: PANEL_OPS["work_rules_get"](),
    "work_rule_delete": lambda p: PANEL_OPS["work_rule_delete"](p.get("ids") if "ids" in p else _need(p, "id", str)),
    "work_unignore": lambda p: PANEL_OPS["work_unignore"](p.get("ids") if "ids" in p else _need(p, "id", str)),
    "config_get": lambda p: PANEL_OPS["config_get"](),
    "config_save": lambda p: PANEL_OPS["config_save"](_need(p, "changes", dict)),
    "backup_run": lambda p: PANEL_OPS["backup_run"](),
    "trello_test": lambda p: trello_test(p),
    "open_focus": lambda p: os.startfile("ms-settings:quietmomentshome"),   # só abre a tela do Windows; o Foco é do usuário
}
PANEL_ASYNC = {"pick_folder": lambda p: asyncio.to_thread(pick_folder),
               **{f"pomo_{n}": (lambda p, n=n: pomo_run(n, p)) for n in
                  ("get", "start", "pause", "resume", "skip", "reset", "config")}}


async def panel_call(op, p):
    if op in PANEL_ASYNC:
        return await PANEL_ASYNC[op](p)
    if op in PANEL_WS:
        return await asyncio.to_thread(PANEL_WS[op], p)   # io_call espera a fila de disco: fora do event loop
    raise ValueError(f"operação não permitida: {op}")

# As ações de ACTIONS são síncronas e algumas demoram (pausas do teclado, área de
# transferência, disco, abrir programa): rodam fora do event loop, cada grupo numa
# fila de 1 thread. Teclado/mouse ficam todos na mesma fila, na ordem em que chegam.
INPUT_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="input")
IO_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="io")
INPUT_ACTIONS = {"media.vol_up", "media.vol_down", "key.press", "key.text", "key.down", "key.up",
                 "mouse.move", "mouse.button", "mouse.scroll", "discord.mute", "discord.deafen",
                 "clip.set"}  # clip.set pode colar (ctrl+v)


async def on_input(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(INPUT_POOL, fn, *args)


async def on_io(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(IO_POOL, fn, *args)


async def call_action(action, params):
    """Executa uma ação de ACTIONS (em thread) ou de ASYNC_ACTIONS (no loop)."""
    if action in ACTIONS:
        return await (on_input if action in INPUT_ACTIONS else on_io)(ACTIONS[action], params)
    res = ASYNC_ACTIONS[action](params)
    if asyncio.iscoroutine(res):
        res = await res
    return res


DECK = extras.Deck(BASE)
AUDIO_PROFILES = audio_profiles.AudioProfiles(BASE)
UPLOADS = extras.Uploads(CFG.get("upload_dir") or DEFAULT_CFG["upload_dir"])
DOWNLOADS = extras.FolderWatcher(lambda: [CFG.get("downloads_dir") or DEFAULT_CFG["downloads_dir"]])


def export_folders():
    """Pastas de exportação do config.json (só com "on"); a pasta Downloads fica de fora (já tem o seu aviso)."""
    ew = CFG.get("export_watch")
    if not isinstance(ew, dict) or not ew.get("on", True) or not isinstance(ew.get("folders"), list):
        return []
    dl = os.path.normcase(os.path.normpath(os.path.expandvars(CFG.get("downloads_dir") or DEFAULT_CFG["downloads_dir"])))
    return [f for f in ew["folders"] if isinstance(f, str) and os.path.normcase(os.path.normpath(os.path.expandvars(f))) != dl]


EXPORTS = extras.FolderWatcher(export_folders, exts=extras.EXPORT_EXT, depth=extras.EXPORT_DEPTH,
                               stable_s=extras.EXPORT_STABLE_S, check_open=True)
EXPORTS_RECENT: dict = {}   # id -> caminho (só memória, últimos 20): o celular pede o link pelo id, nunca manda caminho
FOCUS = alerts.FocusHold()
MISPLACED = alerts.Misplaced()


def shortcut_map():
    """Programas do config.json por id e por nome (minúsculos), para as macros."""
    m = {}
    for s in CFG["shortcuts"]:
        m[s["name"].lower()] = s["id"]
        m[s["id"].lower()] = s["id"]
    return m


def deck_payload(deck=None):
    return {"deck": deck or DECK.get(), "actions": {k: v[0] for k, v in extras.DECK_ACTIONS.items()},
            "keys": sorted(VK)}


def clip_set(p):
    extras.clip_set_text(p.get("text", ""))
    if p.get("paste"):
        time.sleep(0.05)
        press_combo("ctrl+v")


def open_discord():
    upd = os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe")
    if not os.path.exists(upd):
        raise FileNotFoundError("Discord não encontrado")
    subprocess.Popen([upd, "--processStart", "Discord.exe"], creationflags=pc_info.NO_WINDOW)


async def media_action(name, p):
    """Controla o player atual; se não houver sessão de mídia, usa a tecla de mídia."""
    if name == "seek":
        if not await pc_info.MEDIA.control("seek", float(p.get("delta", 0))):
            raise RuntimeError("nada tocando")
    elif not await pc_info.MEDIA.control(name):
        await on_input(press_combo, {"play_pause": "play_pause", "next": "next", "prev": "prev", "stop": "stop"}[name])
    await push_now()


async def volume_action(p):
    if "delta" in p:
        cur = await pc_info.volume_get() or {"level": 50}
        res = await pc_info.volume_set(level=cur["level"] + float(p["delta"]))
    elif "level" in p:
        res = await pc_info.volume_set(level=float(p["level"]))
    elif p.get("toggle_mute"):
        cur = await pc_info.volume_get() or {"muted": False}
        res = await pc_info.volume_set(muted=not cur["muted"])
    else:
        raise ValueError("parâmetro de volume inválido")
    await push_now()
    return res


ASYNC_ACTIONS = {
    "media.play_pause": lambda p: media_action("play_pause", p),
    "media.next": lambda p: media_action("next", p),
    "media.prev": lambda p: media_action("prev", p),
    "media.stop": lambda p: media_action("stop", p),
    "media.seek": lambda p: media_action("seek", p),
    "media.mute": lambda p: volume_action({"toggle_mute": True}),
    "volume.change": volume_action,
    "volume.set": volume_action,
    "mixer.get": lambda p: pc_info.mixer_get(),
    "mixer.set": lambda p: pc_info.mixer_set(str(p["key"]), p.get("level"), p.get("muted")),
    "audio.set_output": lambda p: output_action(p.get("id")),
    "audio.cycle": lambda p: output_action(cycle=True),
    "steam.get": lambda p: steam_get(),
    "audio.profiles": lambda p: AUDIO_PROFILES.list(),
    "audio.profile_save": lambda p: profile_save(p),
    "audio.profile_apply": lambda p: profile_apply(p),
    "audio.profile_delete": lambda p: AUDIO_PROFILES.delete(str(p.get("id"))),
    "deck.press": lambda p: deck_press(p),
    "clip.get": lambda p: asyncio.to_thread(extras.clip_get),
    "screen.shot": lambda p: asyncio.to_thread(extras.screen_shot, p.get("mon", 0), p.get("w", 1280),
                                               p.get("q", 60), p.get("sid")),
    "screen.click": lambda p: screen_click(p),
    "mic.set": lambda p: pc_info.mic_set(p.get("muted", "toggle")),
    "pomo.get": lambda p: pomo_run("get", p),
    "pomo.start": lambda p: pomo_run("start", p),
    "pomo.pause": lambda p: pomo_run("pause", p),
    "pomo.resume": lambda p: pomo_run("resume", p),
    "pomo.skip": lambda p: pomo_run("skip", p),
    "pomo.reset": lambda p: pomo_run("reset", p),
    "pomo.config": lambda p: pomo_run("config", p),
    "timer.start": lambda p: TIMER.start(p.get("action"), p.get("minutes")),
    "timer.cancel": lambda p: timer_cancel(),
    "games.library": lambda p: asyncio.to_thread(MON.library),
    "games.history": lambda p: asyncio.to_thread(MON.summary),
    "games.launch": lambda p: asyncio.to_thread(MON.launch, str(p.get("id"))),
    "notif.sub": lambda p: notif_sub(p),
    "dota.get": lambda p: dota_get(),
    "weather.get": lambda p: weather_get(),
    "weather.search": lambda p: weather_search(p),
    "weather.set_place": lambda p: weather_set_place(p),
    "weather.refresh": lambda p: weather_refresh(),
}


# --------------------------------------------------------------------------
# Timer de energia: "desligar/suspender daqui a X minutos"
# --------------------------------------------------------------------------
class PowerTimer:
    ACTIONS = {"shutdown": "Desligar", "sleep": "Suspender", "monitor_off": "Desligar monitor", "lock": "Bloquear"}

    def __init__(self):
        self.task, self.action, self.at = None, None, 0.0

    def state(self):
        if not self.task:
            return None
        return {"action": self.action, "label": self.ACTIONS[self.action], "left": max(0, round(self.at - time.time()))}

    async def start(self, action, minutes):
        if action not in self.ACTIONS:
            raise ValueError("ação do timer inválida")
        if not CFG.get("allow_power"):
            raise PermissionError("ações de energia desativadas no config.json")
        minutes = float(minutes)
        if not 1 <= minutes <= 720:
            raise ValueError("escolha entre 1 minuto e 12 horas")
        self.cancel()
        self.action, self.at = action, time.time() + minutes * 60
        self.task = asyncio.create_task(self._run())
        log.info("timer: %s em %d min", action, minutes)
        return self.state()

    async def _run(self):
        try:
            await asyncio.sleep(max(0, self.at - time.time()))
        except asyncio.CancelledError:
            return
        action, self.task, self.action = self.action, None, None
        log.info("timer: executando %s", action)
        try:
            await asyncio.to_thread(power, action)
        except Exception:  # noqa: BLE001
            log.exception("timer")

    def cancel(self):
        if self.task:
            self.task.cancel()
            log.info("timer cancelado")
        self.task, self.action = None, None
        return None


TIMER = PowerTimer()


async def timer_cancel():
    return TIMER.cancel()


async def output_action(dev_id=None, cycle=False):
    name = await pc_info.set_output(dev_id, cycle)
    asyncio.create_task(push_now())
    return name


async def profile_save(p):
    snap = await pc_info.profile_snapshot()
    return AUDIO_PROFILES.save(p.get("name"), snap, p.get("id"))


async def profile_apply(p):
    prof = AUDIO_PROFILES.get(str(p.get("id")))
    res = await pc_info.profile_apply(prof["data"])
    asyncio.create_task(push_now())
    return res


async def screen_click(p):
    await asyncio.to_thread(extras.screen_point, p.get("mon", 0), p["x"], p["y"])
    if p.get("button"):
        def click():
            mouse_button(p["button"], p.get("state", "click"))
            if p.get("double"):
                mouse_button(p["button"])
        await on_input(click)


async def run_deck_action(v):
    _label, action, params = extras.DECK_ACTIONS[v]
    return await call_action(action, dict(params))


async def deck_press(p):
    b = await on_io(DECK.button, p.get("page"), p.get("index"))
    t, v = b["type"], b["value"]
    if t == "keys":
        await on_input(press_combo, v)
    elif t == "text":
        await on_input(type_text, v)
    elif t == "shortcut":
        return await on_io(launch, v)
    elif t == "action":
        return await run_deck_action(v)
    elif t == "macro":
        for kind, arg in extras.parse_macro(v, valid_combo, shortcut_map()):
            if kind == "keys":
                await on_input(press_combo, arg)
            elif kind == "text":
                await on_input(type_text, arg)
            elif kind == "wait":
                await asyncio.sleep(arg / 1000)
            elif kind == "open":
                sid = shortcut_map().get(arg.lower())  # nome ou id -> id do config.json
                if not sid:
                    raise ValueError(f"atalho não configurado: {arg}")
                await on_io(launch, sid)
            elif kind == "action":
                await run_deck_action(arg)
            await asyncio.sleep(0.04)  # dá tempo de o Windows processar cada passo
    return None


# --------------------------------------------------------------------------
# Ping, notificações do Windows e FPS (watchers.py)
# --------------------------------------------------------------------------
PING = watchers.Ping()
NOTIF = watchers.WinNotifications()
FPS = watchers.Fps(CFG.get("presentmon_path", ""))
notif_subs: set = set()


def notif_sub(p):
    ws = p.get("_ws")
    if ws is None:
        raise ValueError("sem conexão")
    (notif_subs.add if p.get("on") else notif_subs.discard)(ws)
    return {"available": NOTIF.available}


async def push_notif(n):
    st = await pomo_state()
    if FOCUS.should_hold(n.get("app"), st["config"].get("focus_except")):
        FOCUS.hold(n)    # segura: nada vai ao celular (nem toast, nem som, nem vibração) até o foco acabar
        st["focus"]["held"] = FOCUS.n
        await pomo_send([json.dumps({"t": "pomo", "d": st})])
        return
    msg = json.dumps({"t": "event", "kind": "notif", **n}, ensure_ascii=False)
    for ws in list(notif_subs):
        try:
            await ws.send(msg)
        except ConnectionClosed:
            notif_subs.discard(ws)


def phone_connected():
    return any(k == "celular" for k in client_kind.values())


def fps_wanted():
    st = STATS_REF[0]
    return getattr(st, "fg_pid", 0) if st and phone_connected() else 0


STATS_REF = [None]




# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------
clients = set()
client_kind: dict = {}  # ws -> "celular"


async def web_handler(ws):
    """Conexão de um aparelho (rede local ou cabo via adb reverse): só o token."""
    raw_ip = ws.remote_address[0] if ws.remote_address else "?"
    hdr = ws.request.headers if ws.request else {}
    if not multipc.origin_ok(hdr.get("Origin"), hdr.get("Host"), CFG.get("allowed_origins")):
        log.warning("recusado: origem não permitida (%s)", raw_ip)
        await ws.close(4004, "origem")
        return
    xff = hdr.get("X-Forwarded-For")
    peer = multipc.client_ip(raw_ip, xff)
    await session(ws, "cabo USB" if peer in multipc.LOOPBACK else peer, "celular", xff)


def welcome_payload():
    return {
        "t": "welcome", "version": VERSION, "proto": multipc.PROTO, "pc_id": CFG["pc_id"], "pc_name": CFG.get("pc_name", ""), "web": web_build(), "icon_key": ICONS.key(), "host": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "shortcuts": [{k: v for k, v in (("id", s["id"]), ("name", s["name"]), ("icon", shortcut_icon(s))) if v}
                      for s in CFG["shortcuts"]],
        "power": bool(CFG.get("allow_power")),
        "features": {"media": pc_info.HAS_MEDIA, "volume": pc_info.HAS_VOLUME, "mixer": pc_info.HAS_VOLUME,
                     "screen": True, "clipboard": True, "files": True, "deck": True,
                     "mic": pc_info.HAS_VOLUME, "timer": True, "deck_auto": True, "macro": True,
                     "library": True, "ping": True, "notif": True, "fps": True, "work": True, "pomo": True, "trello": True,
                     "dota": True},
    }


async def push_welcome():
    """Programas ou energia mudaram no painel do PC: os aparelhos conectados atualizam sem reconectar."""
    msg = json.dumps(welcome_payload())
    for ws in list(clients):
        try:
            await ws.send(msg)
        except ConnectionClosed:
            clients.discard(ws)


async def panel_session(ws, peer):
    """Conexão do modo painel (já autenticada). Fica fora de `clients`: não recebe o fluxo do celular."""
    panel_clients.add(ws)
    await ws.send(json.dumps({"t": "panel_ok", "version": VERSION, "icon_key": ICONS.key()}))
    await pomo_send([json.dumps({"t": "pomo", "d": await pomo_state()})], only=ws)
    log.info("painel do PC aberto")
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict) or msg.get("t") != "pop":
                continue
            op, rid = msg.get("a"), msg.get("id")
            try:
                p = msg.get("p") or {}
                if not isinstance(p, dict):
                    raise ValueError("parâmetros inválidos")
                data = await panel_call(op, p)
                res = {"t": "res", "id": rid, "ok": True, "d": data}
                if op not in QUIET_ACTIONS:
                    log.info("painel: %s", op)
            except ConnectionClosed:
                raise
            except ValueError as e:   # validação (texto vazio, limite...): mensagem para a tela, sem dados
                res = {"t": "res", "id": rid, "ok": False, "e": str(e)}
            except Exception as e:  # noqa: BLE001
                log.warning("painel %s: falhou (%s)", op, type(e).__name__)
                res = {"t": "res", "id": rid, "ok": False, "e": f"Não deu certo ({type(e).__name__}). Veja o log."}
            if rid is not None:
                await ws.send(json.dumps(res, ensure_ascii=False))
    except ConnectionClosed:
        pass
    finally:
        panel_clients.discard(ws)
        if not panel_clients:
            PAIR.close()   # painel fechado: o código deixa de valer
        log.info("painel do PC fechado")


async def session(ws, peer, kind, xff=None):
    try:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
    except Exception:
        await ws.close(4001, "hello")
        return
    if isinstance(hello, dict) and "panel" in hello:
        # modo painel: loopback E panel_token certo. Qualquer outra coisa é recusada (o token nunca vai ao log)
        raw_ip = ws.remote_address[0] if ws.remote_address else "?"
        ok = (raw_ip in LOOPBACK and not xff and hello.get("t") == "hello"   # por proxy (X-Forwarded-For) o painel nunca abre
              and secrets.compare_digest(str(hello.get("panel", "")), CFG["panel_token"]))
        if not ok:
            log.warning("recusado: painel inválido de %s", peer)
            await ws.close(4001, "painel")
            return
        await panel_session(ws, peer)
        return
    if isinstance(hello, dict) and hello.get("t") == "pair":
        # pareamento: o código certo entrega o token e a conexão segue como um hello normal
        res = PAIR.check(peer, hello.get("code"))
        if res != "ok":
            log.warning("pareamento recusado de %s", peer)
            await ws.send(json.dumps({"t": "pair_err", "e": "muitas tentativas" if res == "blocked" else "código inválido"}))
            await ws.close(4003 if res == "blocked" else 4002, "pair")
            return
        log.info("pareamento ok de %s", peer)
        await ws.send(json.dumps({"t": "paired", "token": CFG["token"]}))
    elif not isinstance(hello, dict) or hello.get("t") != "hello" or not secrets.compare_digest(str(hello.get("token", "")), CFG["token"]):
        log.warning("recusado: token inválido de %s", peer)
        await ws.close(4001, "token")
        return

    await ws.send(json.dumps(welcome_payload()))
    clients.add(ws)
    client_kind[ws] = kind
    sent_art[ws] = None
    sent_gameart[ws] = None
    await send_games(ws, MON.state)
    await push_now(only=ws)
    await pomo_send([json.dumps({"t": "pomo", "d": await pomo_state()})], only=ws)
    await trello_send(only=ws)
    if DOTA.state is not None:
        await dota_send(DOTA.state, only=ws)
    await weather_send(WEATHER.state(), only=ws)
    if STEAMDL.polled:
        await steam_send(STEAMDL.state(), only=ws)
    log.info("conectado: %s (%s) tela %s", kind, peer, str(hello.get("screen", "?"))[:120])
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("t") == "pop":  # gerenciamento: só o modo painel (panel_token + loopback)
                log.warning("operação de painel recusada (conexão de %s sem panel_token)", peer)
                if msg.get("id") is not None:
                    await ws.send(json.dumps({"t": "res", "id": msg.get("id"), "ok": False, "e": "operação só do painel do PC"}))
                continue
            if msg.get("t") != "req":
                continue
            action, rid = msg.get("a"), msg.get("id")
            try:
                if action not in ACTIONS and action not in ASYNC_ACTIONS:
                    raise ValueError(f"ação não permitida: {action}")
                params = msg.get("p") or {}
                if action in ("notif.sub", "file.begin"):  # precisam saber qual conexão está pedindo
                    params = {**params, "_ws": ws}
                data = await call_action(action, params)
                res = {"t": "res", "id": rid, "a": action, "ok": True, "d": data if isinstance(data, (str, int, float, dict, list)) else None}
                if action not in QUIET_ACTIONS:
                    log.info("%s %s", action, msg.get("p") or "")
            except Exception as e:  # noqa: BLE001
                log.warning("falha %s: %s", action, e)
                res = {"t": "res", "id": rid, "a": action, "ok": False, "e": str(e)}
            if rid is not None:
                await ws.send(json.dumps(res))
    except ConnectionClosed:
        pass
    finally:
        clients.discard(ws)
        client_kind.pop(ws, None)
        notif_subs.discard(ws)
        sent_art.pop(ws, None)
        sent_gameart.pop(ws, None)
        await on_input(release_held)
        await on_io(UPLOADS.cancel_owner, ws)  # envio pela metade: fecha e apaga o .part
        log.info("desconectado: %s (%s)", kind, peer)


def release_held():
    release_modifiers()
    user32.mouse_event(0x4, 0, 0, 0, 0)  # solta botão esquerdo se ficou preso (drag)


sent_art: dict = {}
# não vão para o log: repetitivas ou com conteúdo pessoal (texto digitado/copiado)
QUIET_ACTIONS = {"games.history", "games.library", "notif.sub", "mouse.move", "mouse.scroll", "key.text", "clip.set", "clip.get", "file.chunk",
                 "screen.shot", "mixer.get", "audio.profiles", "audio.profile_save", "audio.profile_apply", "audio.profile_delete",  # nome do perfil
                 "steam.get",  # consultada a cada abertura da tela Jogos
                 "deck.get", "deck.save",  # deck.save: textos/macros dos botões
                 "work.get", "work.week", "work.csv", "work.month",  # nomes de cliente/job/arquivo
                 "tasks.get", "tasks.toggle",  # texto das tarefas
                 "export.link", "export.reveal",  # nome de arquivo de cliente
                 "dota.get",  # consultada a cada abertura da tela Dota
                 "weather.get", "weather.search", "weather.set_place", "weather.refresh",  # cidade escolhida / texto digitado
                 "pomo.get", "pomo.start", "pomo.pause", "pomo.resume", "pomo.skip", "pomo.reset", "pomo.config",
                 "pomo_get", "pomo_start", "pomo_pause", "pomo_resume", "pomo_skip", "pomo_reset", "pomo_config",
                 # modo painel (operações locais; mesmos motivos: texto de tarefas, clientes, pastas, código de pareamento)
                 "panel_status", "pair_new", "pair_state", "pick_folder", "tasks_get", "tasks_add", "tasks_edit",
                 "tasks_remove", "tasks_move", "tasks_toggle", "tasks_clear_done", "tasks_restore", "work_get",
                 "work_week", "work_csv", "work_month", "work_month_csv", "work_note_set", "work_add_root", "work_remove_root", "work_assign_preview", "work_assign",
                 "work_ignore", "work_rules_get", "work_rule_delete", "work_unignore", "config_get", "config_save", "open_focus", "backup_run", "trello_test",
                 "trello.get", "trello.open", "work_links_get", "work_link_set", "work_link_delete", "work_link_restore", "trello_cards"}


async def now_payload():
    media = await pc_info.MEDIA.info()
    vol = await pc_info.volume_get()
    return {"media": media, "volume": vol}


async def send_now(ws, payload):
    msg = json.dumps({"t": "now", "d": payload}, ensure_ascii=False)
    try:
        await ws.send(msg)
        art = (payload.get("media") or {}).get("art")
        if art and sent_art.get(ws) != art and pc_info.MEDIA.art_b64:
            await ws.send(json.dumps({"t": "art", "id": art, "jpg": pc_info.MEDIA.art_b64}))
            sent_art[ws] = art
    except ConnectionClosed:
        clients.discard(ws)


async def push_now(only=None):
    """Envia mídia/volume já (após uma ação), sem esperar o próximo ciclo."""
    if not clients:
        return
    await asyncio.sleep(0.25)  # dá tempo do player reagir
    payload = await now_payload()
    for ws in ([only] if only else list(clients)):
        await send_now(ws, payload)


async def stats_loop(stats: Stats):
    while True:
        try:
            if clients:
                data = await asyncio.to_thread(stats.collect)
                data["mic"] = await pc_info.mic_get()
                data["timer"] = TIMER.state()
                data["ping"] = PING.state()
                data["fps"] = FPS.state(getattr(stats, "fg_pid", 0))
                msg = json.dumps({"t": "stats", "d": data}, ensure_ascii=False)
                for ws in list(clients):
                    try:
                        await ws.send(msg)
                    except ConnectionClosed:
                        clients.discard(ws)
        except Exception:  # noqa: BLE001
            log.exception("stats_loop")
        await asyncio.sleep(1)


MON = games.GameMonitor(CFG, BASE)
WORK = work.WorkTracker(BASE, CFG.get("idle_min", 5))
BACKUP = backup.Backup(BASE)
TRELLO = trello.Trello(BASE, lambda: CFG.get("trello"))
_trello_sig = [None]


async def trello_send(only=None, force=False):
    """Empurra o estado aos celulares só quando muda."""
    msg = json.dumps({"t": "trello", "d": await on_io(trello_state)}, ensure_ascii=False)
    sig = hash(msg)
    if not only and not force and sig == _trello_sig[0]:
        return
    if not only:
        _trello_sig[0] = sig
    for ws in ([only] if only else list(clients)):
        try:
            await ws.send(msg)
        except ConnectionClosed:
            clients.discard(ws)


async def trello_loop():
    """A cada 5 min, só com aparelho conectado (parado sem aparelho)."""
    while True:
        try:
            if TRELLO.due(bool(clients)):
                await on_io(TRELLO.refresh)
            await trello_send()
            if phone_connected() and TRELLO.enabled():
                d = await on_io(TRELLO.digest)   # 1x por dia, a partir das 9h (Brasília), com o aparelho conectado
                if d:
                    log.info("aviso do Trello enviado")   # sem nomes de cards
                    await phones_send({"t": "event", "kind": "trello.digest", **d})
        except Exception as e:  # noqa: BLE001
            log.warning("trello_loop: %s", type(e).__name__)
        await asyncio.sleep(20)


def do_backup():
    """Na fila de disco: grava o histórico pendente e copia os dados pessoais para a pasta escolhida (nunca levanta erro)."""
    WORK.save()
    return BACKUP.run(CFG.get("backup_dir") or "")


async def backup_loop():
    await asyncio.sleep(30)
    while True:
        try:
            if BACKUP.due(CFG.get("backup_dir") or ""):
                st = await on_io(do_backup)
                log.info("backup diário: %s", "ok" if st.get("ok") else "falhou")
        except Exception as e:  # noqa: BLE001
            log.warning("backup_loop: %s", type(e).__name__)
        await asyncio.sleep(600)
TASKS = tasks.TaskList(BASE)
POMO = pomodoro.Pomodoro(BASE)
POMO_H = [None]     # único agendamento do fim da fase (call_later): nada de loop de 1 s
NOTIFY = [None]     # f(titulo, texto): aviso do ícone da bandeja (definido por run_with_tray)
POMO_OPS = {
    "get": lambda p: POMO.get(),
    "start": lambda p: POMO.start(p["task_id"]) if "task_id" in p else POMO.start(),
    "pause": lambda p: POMO.pause(),
    "resume": lambda p: POMO.resume(),
    "skip": lambda p: POMO.skip(),
    "reset": lambda p: POMO.reset(),
    "config": lambda p: POMO.set_config({k: v for k, v in p.items() if not k.startswith("_")}),
}
_PHASE_TIP = {"focus": "Foco", "short": "Pausa", "long": "Pausa longa"}


def pomo_tip():
    """Texto do tooltip da bandeja enquanto roda ('Foco · 12:34'); '' se parado. Só leitura, sem disco."""
    try:
        st = POMO.get()
        if not st["running"]:
            return ""
        r = int(st["remaining_s"] + 0.999)
        return f"{_PHASE_TIP.get(st['phase'], 'Foco')} · {r // 60:02d}:{r % 60:02d}"
    except Exception:  # noqa: BLE001
        return ""


async def pomo_state(state=None):
    """Estado do Pomodoro + modo foco: liga/desliga o modo foco conforme a fase e entrega o resumo quando acaba."""
    if state is None:
        state = await on_io(POMO.get)
    cfg = state["config"]
    summary = FOCUS.set_active(cfg.get("focus_mode", True) and state["phase"] == "focus" and state["running"])
    state["focus"] = {"on": FOCUS.active, "held": FOCUS.n}
    if summary:
        await focus_deliver(summary)
    return state


async def focus_deliver(summary):
    log.info("modo foco: resumo entregue")   # sem apps nem textos
    msg = json.dumps({"t": "event", "kind": "focus.summary", **summary}, ensure_ascii=False)
    for ws in list(notif_subs):
        try:
            await ws.send(msg)
        except ConnectionClosed:
            notif_subs.discard(ws)
    await pc_balloon("Modo foco", summary["text"])


async def pomo_send(msgs, only=None):
    targets = [only] if only else list(clients) + list(panel_clients)
    for ws in targets:
        try:
            for m in msgs:
                await ws.send(m)
        except ConnectionClosed:
            clients.discard(ws)
            panel_clients.discard(ws)


def pomo_reschedule():
    """Roda no event loop, depois de qualquer mudança: refaz o único agendamento do fim da fase."""
    if POMO_H[0]:
        POMO_H[0].cancel()
        POMO_H[0] = None
    d = POMO.next_due()
    if d is not None:
        POMO_H[0] = asyncio.get_running_loop().call_later(
            d + 0.05, lambda: asyncio.ensure_future(pomo_fire()))


def _pomo_alert(text):
    try:
        import winsound
        winsound.MessageBeep()
    except Exception:  # noqa: BLE001
        pass
    if NOTIFY[0]:
        try:
            NOTIFY[0]("Pomodoro", text)
        except Exception as e:  # noqa: BLE001
            log.warning("aviso do pomodoro: %s", type(e).__name__)


async def pomo_fire():
    POMO_H[0] = None
    ev = await on_io(POMO.tick)
    state = await pomo_state()
    pomo_reschedule()   # sem evento = ainda não venceu (relógio mudou): agenda de novo
    msgs = [json.dumps({"t": "pomo", "d": state})]
    if ev:
        log.info("pomodoro: %s concluído", ev["finished"])
        msgs.append(json.dumps({"t": "event", "kind": "pomo.done", **ev}))
        await asyncio.to_thread(_pomo_alert, pomodoro.message(ev))
    await pomo_send(msgs)


async def pomo_run(name, p):
    state = await pomo_state(await on_io(POMO_OPS[name], p))
    if name != "get":
        pomo_reschedule()
        await pomo_send([json.dumps({"t": "pomo", "d": state})])
    return state


def work_app_icons():
    """{app: id do ícone} dos apps de trabalho já vistos em foco (o caminho do exe nunca sai do servidor)."""
    return {app: i for app, path in list(work.APP_PATHS.items()) if (i := ICONS.register(path))}


def _wi(res):
    return {**res, "app_icons": work_app_icons()} if isinstance(res, dict) else res


def _work_step():
    return WORK.poll(), bool(WORK.roots)


def _misplaced_min():
    v = CFG.get("misplaced_min", alerts.DEFAULT_MISPLACED_MIN)
    return v if v in alerts.MISPLACED_OPTIONS else alerts.DEFAULT_MISPLACED_MIN


async def work_loop():
    """Tempo no Photoshop/Premiere/After: lê o foco a cada 2 s, na fila de disco (WORK não usa locks)."""
    while True:
        try:
            info, has_roots = await on_io(_work_step)
            a = MISPLACED.observe(info, time.time(), has_roots, _misplaced_min())
            if a:
                log.info("aviso de arquivo fora do lugar")   # sem nomes
                await phones_send({"t": "event", "kind": "alert", "sub": a["kind"], "title": a["title"],
                                   "body": a["body"], "open": "work"})
                await pc_balloon(a["title"], a["body"])
        except Exception as e:  # noqa: BLE001
            log.warning("work_loop: %s", type(e).__name__)  # sem a mensagem: pode ter nome de arquivo
        await asyncio.sleep(2)
sent_gameart: dict = {}


async def send_games(ws, state):
    try:
        await ws.send(json.dumps({"t": "games", "d": state}, ensure_ascii=False))
        art = (state.get("game") or {}).get("art")
        if art and sent_gameart.get(ws) != art and MON.art.get(art):
            await ws.send(json.dumps({"t": "gameart", "id": art, "jpg": MON.art[art]}))
            sent_gameart[ws] = art
    except ConnectionClosed:
        clients.discard(ws)


async def phones_send(obj):
    msg = json.dumps(obj, ensure_ascii=False)
    for ws in [w for w in list(clients) if client_kind.get(w) == "celular"]:
        try:
            await ws.send(msg)
        except ConnectionClosed:
            pass


async def pc_balloon(title, text):
    if NOTIFY[0]:
        try:
            await asyncio.to_thread(NOTIFY[0], title, text)
        except Exception as e:  # noqa: BLE001
            log.warning("aviso da bandeja: %s", type(e).__name__)


def _size_txt(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


async def export_done(f):
    eid = secrets.token_urlsafe(6)
    EXPORTS_RECENT[eid] = f["path"]
    while len(EXPORTS_RECENT) > 20:
        del EXPORTS_RECENT[next(iter(EXPORTS_RECENT))]
    await phones_send({"t": "event", "kind": "export", "id": eid, "name": f["name"], "size": f["size"],
                       "size_txt": _size_txt(f["size"])})
    await pc_balloon("Exportado", f"{f['name']} ({_size_txt(f['size'])})")


def export_path(p):
    path = EXPORTS_RECENT.get(str(p.get("id", "")))
    if not path or not os.path.isfile(path):
        raise ValueError("esse arquivo não está mais disponível")
    return path


def export_link(p):
    path = export_path(p)
    if os.path.getsize(path) > extras.DL_MAX:
        raise ValueError("grande demais para mandar por aqui (limite 300 MB)")
    name = os.path.basename(path)
    return {"name": name, "url": extras.dl_register(name, path=path)}


def export_reveal(p):
    path = export_path(p)
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])   # nada da pasta é lido ou alterado
    return {"ok": True}


async def downloads_loop():
    """Avisa os celulares quando aparece um arquivo novo (completo) em Downloads."""
    last_sweep = time.monotonic()
    while True:
        try:
            for f in await asyncio.to_thread(DOWNLOADS.poll):
                log.info("download concluído: %s", f["name"])
                await phones_send({"t": "event", "kind": "download", "name": f["name"], "size": f["size"]})
            for f in await asyncio.to_thread(EXPORTS.poll):
                log.info("exportação concluída")   # sem o nome: é arquivo de cliente
                await export_done(f)
        except Exception:  # noqa: BLE001
            log.exception("downloads_loop")
        if time.monotonic() - last_sweep >= 60:  # envios do celular abandonados
            last_sweep = time.monotonic()
            try:
                await on_io(UPLOADS.sweep)
            except Exception:  # noqa: BLE001
                log.exception("uploads sweep")
        await asyncio.sleep(3)


async def games_loop():
    while True:
        try:
            state = await asyncio.to_thread(MON.poll)
            for ws in list(clients):
                await send_games(ws, state)
        except Exception:  # noqa: BLE001
            log.exception("games_loop")
        await asyncio.sleep(3)


DOTA = dota.DotaBridge(CFG.get("dota"))
# proxy das imagens da ponte (/dota/img/...): cache em disco ao lado do servidor, chave de URL derivada do token
ICONS = icons.Icons(Path(__file__).resolve().with_name("icon-cache"), lambda: CFG["token"])
pc_info.ICON_REGISTER = ICONS.register
games.ICON_REGISTER = ICONS.register
IMG = dota_img.ImageProxy(Path(__file__).resolve().with_name("dota-img"), lambda: CFG["token"], lambda: DOTA.cfg.get("bridge"))


def dota_open():
    """O games.py viu o Dota 2 rodando (Steam appid 570)."""
    cur = MON.current
    return bool(cur) and (str(cur.get("appid")) == "570" or str(cur.get("name", "")).lower() == "dota 2")


def dota_proc():
    """(aberto, início da sessão em epoch s) do Dota segundo o games.py."""
    cur = MON.current
    return (dota_open(), cur.get("start") if cur and dota_open() else None)


def dota_msg(state):
    return json.dumps({"t": "dota", "d": state, "auto_open": DOTA.auto_open, "img_key": IMG.key()}, ensure_ascii=False)


async def dota_send(state, only=None):
    msg = dota_msg(state)
    for ws in ([only] if only else list(clients)):
        try:
            await ws.send(msg)
        except ConnectionClosed:
            clients.discard(ws)


async def dota_get():
    """Ação dota.get: estado atual (consulta a ponte agora, na fila de disco)."""
    state = await on_io(DOTA.poll, *dota_proc())
    return {"d": state, "auto_open": DOTA.auto_open, "img_key": IMG.key()}


async def dota_loop():
    """Consulta a ponte na fila de disco: 1×/s com o Dota aberto, a cada 10 s fechado, parado sem aparelho.
    Os aparelhos só recebem quando o estado muda."""
    sent = None
    while True:
        try:
            if not clients:
                sent = None  # quem conectar recebe o estado no session()
            elif DOTA.due(dota_open()):
                state = await on_io(DOTA.poll, *dota_proc())
                if not dota.same(state, sent):
                    await dota_send(state)
                    sent = state
        except Exception:  # noqa: BLE001
            log.exception("dota_loop")
        await asyncio.sleep(0.5)


WEATHER = weather.Weather(BASE, CFG["weather"])


def weather_persist(place):
    """Grava só weather.place no config.json (atômico, sem mexer no resto) e no CFG em memória (mesmo dict do Weather)."""
    with _cfg_lock:
        cur = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        w = cur.get("weather") if isinstance(cur.get("weather"), dict) else dict(weather.DEFAULT_CFG)
        w["place"] = place
        cur["weather"] = w
        tmp = CFG_PATH.with_name("config.json.tmp")
        tmp.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CFG_PATH)
    CFG["weather"]["place"] = place


async def weather_send(state, only=None):
    msg = json.dumps({"t": "weather", "d": state}, ensure_ascii=False)
    for ws in ([only] if only else list(clients)):
        try:
            await ws.send(msg)
        except ConnectionClosed:
            clients.discard(ws)


async def weather_get():
    return {"d": WEATHER.state()}


async def weather_search(p):
    return {"results": await on_io(WEATHER.search, p.get("q"))}


async def weather_set_place(p):
    """Escolhe a cidade (um resultado de weather.search): grava no config.json, busca a previsão na hora e avisa todos."""
    place = weather.clean_place(p.get("place"))

    def work():
        weather_persist(place)
        WEATHER.set_place(place)
        WEATHER.refresh()
        return WEATHER.state()
    state = await on_io(work)
    await weather_send(state)
    return {"d": state}


async def weather_refresh():
    changed, wait = await on_io(WEATHER.refresh_manual)
    state = WEATHER.state()
    if changed:
        await weather_send(state)
    return {"d": state, "wait_s": wait}


async def weather_loop():
    """Atualiza a cada 30 min, só com cidade escolhida e aparelho conectado (parado sem aparelho)."""
    while True:
        try:
            if WEATHER.due(bool(clients)) and await on_io(WEATHER.refresh):
                await weather_send(WEATHER.state())
        except Exception:  # noqa: BLE001
            log.exception("weather_loop")
        await asyncio.sleep(20)


STEAMDL = steam_dl.SteamDownloads(icon_fn=lambda appid: MON.catalog.steam_icon(appid))


async def steam_send(state, only=None):
    msg = json.dumps({"t": "steam", "d": state}, ensure_ascii=False)
    for ws in ([only] if only else list(clients)):
        try:
            await ws.send(msg)
        except ConnectionClosed:
            clients.discard(ws)


async def steam_get():
    if not STEAMDL.polled:
        await on_io(STEAMDL.poll)
    return {"d": STEAMDL.state()}


async def steam_loop():
    """Lê os manifestos da Steam a cada 5 s com download em andamento e a cada 15 s sem; parado sem aparelho."""
    while True:
        wait = 15
        try:
            if clients:
                changed, done = await on_io(STEAMDL.poll)
                if changed:
                    await steam_send(STEAMDL.state())
                for d in done:
                    name = d.get("name") or "Jogo da Steam"
                    await phones_send({"t": "event", "kind": "steam.done", "appid": d["appid"], "name": name})
                    await pc_balloon("Download da Steam concluído", name)
                if STEAMDL.items:
                    wait = 5
        except Exception:  # noqa: BLE001
            log.exception("steam_loop")
        await asyncio.sleep(wait)


async def now_loop():
    while True:
        try:
            if clients:
                payload = await now_payload()
                for ws in list(clients):
                    await send_now(ws, payload)
        except Exception:  # noqa: BLE001
            log.exception("now_loop")
        await asyncio.sleep(1)


NET = {"web": False, "adb": []}


def lan_ip():
    """IP do PC na rede local (não envia nada: UDP connect só escolhe a rota)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def phone_links():
    """Links para abrir o app no celular (o token vai no #, que não é enviado na rede)."""
    port, tok = WEB["port"], CFG["token"]
    links = {"cabo": f"http://localhost:{port}/#t={tok}"}
    ip = lan_ip()
    if ip and WEB["host"] not in ("127.0.0.1", "localhost"):
        links["wifi"] = f"http://{ip}:{port}/#t={tok}"
    return links


def status():
    """Estado para o ícone da bandeja/painel."""
    kinds = [client_kind.get(ws) for ws in list(clients)]
    game = (MON.state.get("game") or {}).get("name")
    n = kinds.count("celular")
    parts = ["aparelho conectado" if n == 1 else f"{n} aparelhos conectados"] if n else []
    phone = {"web": NET["web"], "adb": list(NET["adb"]), "port": WEB["port"],
             "links": phone_links() if NET["web"] else {}}
    if parts:
        return {"state": "ok", "text": " + ".join(parts), "game": game, "phone": phone}
    text = "Aguardando conexão na rede local" if NET["web"] else "Porta do app indisponível (veja o log)"
    return {"state": "wait" if NET["web"] else "off", "text": text, "game": game, "phone": phone}


# --------------------------------------------------------------------------
# Celular: app web + adb reverse
# --------------------------------------------------------------------------
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".webmanifest": "application/manifest+json", ".png": "image/png", ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
        ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


def web_build():
    """Muda quando algum arquivo do app web muda: o celular recarrega a página sozinho."""
    try:
        return int(max(f.stat().st_mtime for f in WEB_DIR.rglob("*") if f.is_file()))
    except (OSError, ValueError):
        return 0


def serve_static(connection, request):
    """Requisições HTTP comuns recebem os arquivos de web/; WebSocket segue normal."""
    if "websocket" in request.headers.get("Upgrade", "").lower():
        return None
    path = request.path.split("?", 1)[0].split("#", 1)[0]
    if path.startswith("/dl/"):  # arquivo do PC liberado por clip.get (link temporário)
        got = extras.dl_fetch(path)
        if not got:
            return connection.respond(HTTPStatus.NOT_FOUND, "link expirado\n")
        name, body, ctype = got
        from urllib.parse import quote
        headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body))),
                           ("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}"),
                           ("Cache-Control", "no-store")])
        return Response(200, "OK", headers, body)
    name = "index.html" if path in ("/", "") else path.lstrip("/")
    f = (WEB_DIR / name).resolve()
    if WEB_DIR.resolve() not in f.parents or not f.is_file() or f.suffix not in MIME:
        return connection.respond(HTTPStatus.NOT_FOUND, "não encontrado\n")
    body = f.read_bytes()
    headers = Headers([("Content-Type", MIME[f.suffix]), ("Content-Length", str(len(body))),
                       ("Cache-Control", "no-cache"), ("X-Content-Type-Options", "nosniff")])
    return Response(200, "OK", headers, body)


async def http_entry(connection, request):
    """Entrada HTTP: imagens do Dota (proxy com cache, na fila de disco) ou os arquivos do app.
    CORS só para as origens de allowed_origins (o app, instalado em outro endereço, lê imagens e links deste PC)."""
    ws_up = "websocket" in request.headers.get("Upgrade", "").lower()
    cors = multipc.cors_headers(request.headers.get("Origin"), request.headers.get("Host"), CFG.get("allowed_origins"))
    for prefix, handler in (("/dota/img/", IMG.handle), ("/icon/", ICONS.handle)):
        if request.path.startswith(prefix) and not ws_up:
            status, ctype, body, extra = await on_io(handler, request.path)
            return Response(status, HTTPStatus(status).phrase,
                            Headers([("Content-Type", ctype), ("Content-Length", str(len(body))), *extra.items(), *cors.items()]), body)
    resp = serve_static(connection, request)
    if resp is not None and cors and request.path.startswith("/dl/"):
        for k, v in cors.items():
            resp.headers[k] = v
    return resp


async def web_main():
    host, port = WEB["host"], int(os.environ.get("COMPANION_WEB_PORT", WEB["port"]))
    while True:
        try:
            async with serve(web_handler, host, port, process_request=http_entry,
                             ping_interval=5, ping_timeout=10, max_size=4 * 2**20,
                             max_queue=64) as server:
                NET["web"] = True
                for name, url in phone_links().items():
                    log.info("celular (%s): %s", name, url.split("#")[0])
                await server.serve_forever()
        except OSError as e:
            NET["web"] = False
            log.warning("app do celular: não consegui escutar em %s:%s: %s", host, port, e)
            await asyncio.sleep(10)


def find_adb():
    cands = [WEB.get("adb_path") or "", "adb",
             os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
             os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\adb.exe"),
             os.path.expandvars(r"%USERPROFILE%\companion-pc\platform-tools\adb.exe")]
    import glob
    import shutil
    cands += glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe"))
    for c in cands:
        if c and (shutil.which(c) or os.path.isfile(c)):
            return shutil.which(c) or c
    return None


def adb(path, *args, timeout=8):
    return subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=pc_info.NO_WINDOW)


def adb_open_on_phone(serial=None):
    """Abre o app no celular conectado pelo cabo (já com o token)."""
    path = find_adb()
    if not path:
        raise RuntimeError("adb não encontrado (rode windows\\setup-network.ps1)")
    serials = [serial] if serial else list(NET["adb"])
    if not serials:
        raise RuntimeError("nenhum celular com depuração USB conectado")
    for sr in serials:
        adb(path, "-s", sr, "shell", "am", "start", "-a", "android.intent.action.VIEW",
            "-d", f"'{phone_links()['cabo']}'")


async def adb_loop():
    """Com o celular no cabo (depuração USB), mapeia localhost:8770 do celular para o PC."""
    if not WEB.get("adb_reverse", True):
        return
    port = int(os.environ.get("COMPANION_WEB_PORT", WEB["port"]))
    path, warned = None, False
    while True:
        try:
            path = path or await asyncio.to_thread(find_adb)
            if not path:
                if not warned:
                    log.info("adb não encontrado: cabo no celular desativado (Wi-Fi continua funcionando)")
                    warned = True
                await asyncio.sleep(30)
                continue
            out = (await asyncio.to_thread(adb, path, "devices")).stdout
            ready = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("\tdevice")]
            for ln in out.splitlines()[1:]:
                if "unauthorized" in ln and ln.split()[0] not in NET.setdefault("unauth", set()):
                    NET["unauth"].add(ln.split()[0])
                    log.info("celular %s: aceite a 'depuração USB' na tela do celular", ln.split()[0])
            for sr in ready:
                if sr not in NET["adb"]:
                    r = await asyncio.to_thread(adb, path, "-s", sr, "reverse", f"tcp:{port}", f"tcp:{port}")
                    if r.returncode == 0:
                        NET["adb"].append(sr)
                        log.info("celular %s no cabo: abra http://localhost:%s", sr, port)
                    else:
                        log.warning("adb reverse falhou (%s): %s", sr, (r.stderr or r.stdout).strip())
            for sr in list(NET["adb"]):
                if sr not in ready:
                    NET["adb"].remove(sr)
                    log.info("celular %s desconectado do cabo", sr)
        except Exception as e:  # noqa: BLE001
            log.warning("adb: %s", e)
            path = None
        await asyncio.sleep(4)


async def web_watch():
    """Página do app mudou no PC: avisa os aparelhos conectados (eles recarregam)."""
    last = web_build()
    while True:
        await asyncio.sleep(3)
        cur = await asyncio.to_thread(web_build)
        if cur != last:
            last = cur
            log.info("app web atualizado - recarregando os aparelhos")
            for ws in list(clients):
                try:
                    await ws.send(json.dumps({"t": "welcome", "web": cur}))
                except Exception:  # noqa: BLE001
                    pass


LOOP = [None]  # event loop do servidor (o painel do PC agenda push_welcome nele)


STARTED_AT = time.time()
ALIVE_EVERY_S = int(os.environ.get("COMPANION_ALIVE_S", 600))   # a variável existe só para o teste


async def alive_loop():
    """Linha 'estou vivo' periódica no log: só tempo ligado e contagens (nada pessoal)."""
    while True:
        await asyncio.sleep(ALIVE_EVERY_S)
        up = int(time.time() - STARTED_AT)
        log.info("vivo: ligado há %dh%02dmin, %d celular(es), %d painel(is)",
                 up // 3600, up % 3600 // 60, len(clients), len(panel_clients))


def spawn(name, factory):
    """Roda uma tarefa de fundo e a religa se ela estourar (espera 5 s, depois 30 s); o log traz só o tipo do erro."""
    async def runner():
        fails = 0
        while True:
            t0 = time.monotonic()
            try:
                await factory()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                fails = 1 if time.monotonic() - t0 > 300 else fails + 1
                wait = 5 if fails <= 3 else 30
                log.error("tarefa '%s' parou (%s); religando em %ds", name, type(e).__name__, wait)
                await asyncio.sleep(wait)
    return asyncio.create_task(runner(), name=name)


async def main():
    LOOP[0] = asyncio.get_running_loop()
    log.info("Companion PC %s", VERSION)
    if POMO.restored_expired:
        log.info("pomodoro: a fase venceu com o servidor desligado (concluída sem aviso)")
    await pomo_state()   # modo foco já ligado se o servidor reabriu no meio de um Foco
    pomo_reschedule()
    pc_info.MEDIA = pc_info.Media()
    n = await on_io(UPLOADS.clean_stale_parts)
    log.info("uploads: %d arquivo(s) .part de envios anteriores apagado(s)", n)
    st = STATS_REF[0] = Stats()
    spawn("stats", lambda: stats_loop(st))
    spawn("ping", lambda: PING.loop(lambda: bool(clients)))
    spawn("notif", lambda: NOTIF.loop(lambda: bool(notif_subs), push_notif,
                                      lambda: {a.lower() for a in CFG.get("notif_ignore", [])}))
    await asyncio.to_thread(watchers.kill_orphans)
    spawn("fps", lambda: FPS.loop(fps_wanted))
    spawn("now", now_loop)
    spawn("games", games_loop)
    spawn("dota", dota_loop)
    spawn("weather", weather_loop)
    spawn("steam", steam_loop)
    spawn("work", work_loop)
    spawn("backup", backup_loop)
    spawn("trello", trello_loop)
    spawn("adb", adb_loop)
    spawn("downloads", downloads_loop)
    spawn("web_watch", web_watch)
    spawn("alive", alive_loop)
    await web_main()


def stop_server():
    release_modifiers()
    try:  # grava o tempo de trabalho que ainda não foi para o disco
        IO_POOL.submit(WORK.save).result(timeout=3)
    except Exception:  # noqa: BLE001
        pass
    if CFG.get("backup_dir"):   # backup ao fechar o servidor (erro nunca trava o fechamento)
        try:
            IO_POOL.submit(do_backup).result(timeout=8)
        except Exception:  # noqa: BLE001
            pass


def run_with_tray():
    import threading
    import tray

    if not tray.acquire_single_instance(wait_s=15 if "--restart" in sys.argv else 0):
        if tray.signal_running_instance(wait_s=5):  # já está rodando e mostrou o painel
            return
        if not tray.ask_kill_stuck():  # não respondeu em 5 s: o usuário decide
            return
        n = tray.kill_running_instances()
        log.warning("instância que não respondia encerrada pelo atalho (%d processo(s))", n)
        if not tray.acquire_single_instance(wait_s=10):
            ctypes.windll.user32.MessageBoxW(None, "Não consegui encerrar a instância travada. Reinicie o PC.",
                                             "Companion PC", 0x10)
            return
    try:
        import pystray  # noqa: F401
    except ImportError:
        log.warning("pystray não instalado - rodando sem ícone na bandeja")
        asyncio.run(main())
        return

    def server_thread():
        crashes = []
        while True:
            try:
                asyncio.run(main())
                return
            except Exception:  # noqa: BLE001
                log.exception("servidor parou")
            now = time.monotonic()
            crashes = [t for t in crashes if now - t < 300] + [now]
            if len(crashes) >= 5:
                log.error("servidor parou %d vezes em 5 min; desisti de religar (o supervisor, se houver, assume)", len(crashes))
                os._exit(1)   # sai com erro: o supervisor religa o processo inteiro; sem supervisor a bandeja some
            wait = (1, 2, 5, 15)[len(crashes) - 1]
            log.warning("religando o servidor em %ds", wait)
            time.sleep(wait)
    threading.Thread(target=server_thread, daemon=True, name="server").start()
    app = tray.TrayApp(status, LOG_PATH, BASE, VERSION, stop_server, open_on_phone=adb_open_on_phone, ops=PANEL_OPS)
    NOTIFY[0] = app.notify
    app.run()


if __name__ == "__main__":
    try:
        if "--console" in sys.argv:
            asyncio.run(main())
        else:
            run_with_tray()
    except KeyboardInterrupt:
        pass
