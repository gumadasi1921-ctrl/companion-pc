"""Recursos do app do celular: botões personalizados (deck), área de transferência,
envio/recebimento de arquivos, tela do PC e aviso de downloads concluídos.
"""
import base64
import ctypes
import hashlib
import io
import json
import logging
import os
import re
import secrets
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("companion")
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# --------------------------------------------------------------------------
# Deck: páginas de botões que o usuário monta (salvo em deck.json)
# --------------------------------------------------------------------------
# ação do deck -> (rótulo, ação do servidor, parâmetros)
DECK_ACTIONS = {
    "media.play_pause": ("Tocar/pausar", "media.play_pause", {}),
    "media.next": ("Próxima faixa", "media.next", {}),
    "media.prev": ("Faixa anterior", "media.prev", {}),
    "media.mute": ("Mudo (PC)", "media.mute", {}),
    "volume.up": ("Volume +5", "volume.change", {"delta": 5}),
    "volume.down": ("Volume −5", "volume.change", {"delta": -5}),
    "audio.cycle": ("Trocar saída de áudio", "audio.cycle", {}),
    "discord.mute": ("Discord: mutar", "discord.mute", {}),
    "discord.deafen": ("Discord: ensurdecer", "discord.deafen", {}),
    "discord.open": ("Abrir Discord", "discord.open", {}),
    "power.lock": ("Bloquear o PC", "power", {"action": "lock"}),
    "power.monitor_off": ("Desligar monitor", "power", {"action": "monitor_off"}),
    "mic.toggle": ("Microfone do PC: mudo", "mic.set", {"muted": "toggle"}),
    "timer.cancel": ("Cancelar timer de energia", "timer.cancel", {}),
}


def _k(label, keys, icon):
    return _b(label, "keys", keys, icon)


def _a(label, action, icon):
    return _b(label, "action", action, icon)


def _b(label, type_, value, icon="", color="", confirm=False):
    b = {"label": label, "type": type_, "value": value}
    if icon:
        b["icon"] = icon
    if color:
        b["color"] = color
    if confirm:
        b["confirm"] = True
    return b


DEFAULT_DECK = {"pages": [
    {"name": "Programas", "auto": "shortcuts"},
    {"name": "Janelas", "buttons": [
        _b("Área de trabalho", "keys", "win+d", "DSK"),
        _b("Visão de tarefas", "keys", "win+tab", "TSK"),
        _b("Captura de tela", "keys", "win+shift+s", "PRT"),
        _b("Explorador", "keys", "win+e", "EXP"),
        _b("Gerenciador de Tarefas", "keys", "ctrl+shift+esc", "GER"),
        _b("Fechar janela", "keys", "alt+f4", "X", "", True),
        _b("Maximizar", "keys", "win+up", "[^]"),
        _b("Minimizar", "keys", "win+down", "[_]"),
        _b("Janela p/ outro monitor", "keys", "win+shift+right", "<->"),
        _b("Bloquear o PC", "action", "power.lock", "LCK"),
    ]},
    {"name": "Navegador", "apps": ["opera.exe", "msedge.exe", "chrome.exe", "firefox.exe", "brave.exe"], "buttons": [
        _b("Nova aba", "keys", "ctrl+t", "+"),
        _b("Fechar aba", "keys", "ctrl+w", "x"),
        _b("Reabrir aba", "keys", "ctrl+shift+t", "<+"),
        _b("Próxima aba", "keys", "ctrl+tab", "->"),
        _b("Voltar", "keys", "alt+left", "<"),
        _b("Avançar", "keys", "alt+right", ">"),
        _b("Recarregar", "keys", "f5", "F5"),
        _b("Tela cheia", "keys", "f11", "F11"),
        _b("Zoom +", "keys", "ctrl+plus", "Z+"),
        _b("Zoom −", "keys", "ctrl+minus", "Z-"),
    ]},
    {"name": "Áudio", "buttons": [
        _b("Tocar/pausar", "action", "media.play_pause", ">||"),
        _b("Anterior", "action", "media.prev", "|<"),
        _b("Próxima", "action", "media.next", ">|"),
        _b("Volume −5", "action", "volume.down", "V-"),
        _b("Volume +5", "action", "volume.up", "V+"),
        _b("Mudo", "action", "media.mute", "MUT"),
        _b("Trocar saída", "action", "audio.cycle", "OUT"),
        _b("Discord: mutar", "action", "discord.mute", "MIC"),
        _b("Discord: ensurdecer", "action", "discord.deafen", "AUD"),
        _a("Microfone do PC", "mic.toggle", "MIC"),
    ]},
    # páginas que abrem sozinhas quando o programa entra em foco ("apps")
    {"name": "VS Code", "apps": ["code.exe"], "buttons": [
        _k("Paleta de comandos", "ctrl+shift+p", "CMD"),
        _k("Abrir arquivo", "ctrl+p", "OPN"),
        _k("Salvar", "ctrl+s", "SAV"),
        _k("Formatar", "shift+alt+f", "FMT"),
        _k("Buscar no projeto", "ctrl+shift+f", "FND"),
        _k("Barra lateral", "ctrl+b", "[|"),
        _k("Painel / terminal", "ctrl+j", "TRM"),
        _k("Ir para definição", "f12", "DEF"),
        _k("Rodar / depurar", "f5", "RUN"),
        _k("Parar", "shift+f5", "STP"),
        _k("Fechar aba", "ctrl+w", "x"),
        _k("Configurações", "ctrl+comma", "CFG"),
    ]},
    {"name": "Terminal", "apps": ["windowsterminal.exe"], "buttons": [
        _k("Nova aba", "ctrl+shift+t", "+"),
        _k("Fechar aba", "ctrl+shift+w", "x"),
        _k("Próxima aba", "ctrl+tab", "->"),
        _k("Dividir painel", "alt+shift+plus", "[|]"),
        _k("Copiar", "ctrl+shift+c", "CPY"),
        _k("Colar", "ctrl+shift+v", "PST"),
        _k("Interromper", "ctrl+c", "^C"),
        _b("Limpar a tela", "text", "clear\n", "CLR"),
        _k("Último comando", "up", "^"),
        _k("Buscar", "ctrl+shift+f", "FND"),
    ]},
    {"name": "Discord", "apps": ["discord.exe"], "buttons": [
        _a("Mutar (Discord)", "discord.mute", "MIC"),
        _a("Ensurdecer", "discord.deafen", "AUD"),
        _a("Microfone do PC", "mic.toggle", "PC"),
        _k("Busca rápida", "ctrl+k", "FND"),
        _k("Canal acima", "alt+up", "^"),
        _k("Canal abaixo", "alt+down", "v"),
        _k("Servidor acima", "ctrl+alt+up", "^^"),
        _k("Servidor abaixo", "ctrl+alt+down", "vv"),
        _k("Marcar como lido", "esc", "OK"),
    ]},
    {"name": "Spotify", "apps": ["spotify.exe"], "buttons": [
        _a("Tocar/pausar", "media.play_pause", ">||"),
        _a("Anterior", "media.prev", "|<"),
        _a("Próxima", "media.next", ">|"),
        _k("Aleatório", "ctrl+s", "RND"),
        _k("Repetir", "ctrl+r", "RPT"),
        _k("Curtir", "alt+shift+b", "<3"),
        _k("Buscar", "ctrl+k", "FND"),
        _k("Volume do Spotify −", "ctrl+down", "V-"),
        _k("Volume do Spotify +", "ctrl+up", "V+"),
    ]},
    {"name": "WhatsApp", "apps": ["whatsapp.exe", "whatsapp.root.exe"], "buttons": [
        _k("Nova conversa", "ctrl+n", "NEW"),
        _k("Buscar", "ctrl+f", "FND"),
        _k("Próxima conversa", "ctrl+tab", "->"),
        _k("Conversa anterior", "ctrl+shift+tab", "<-"),
        _k("Fechar conversa", "esc", "ESC"),
        _k("Enviar", "enter", "OK"),
    ]},
    {"name": "Macros", "buttons": [
        _b("Modo trabalho", "macro", "abrir vscode\nesperar 2s\nabrir terminal\nesperar 1s\nabrir opera", "DEV"),
        _b("Modo jogo", "macro", "abrir discord\nesperar 3s\nabrir steam", "GAME"),
        _b("Ausente / voltar", "macro", "acao media.play_pause\nacao mic.toggle\nteclas win+d", "AFK"),
    ]},
    {"name": "Jogo", "apps": ["@jogo"], "buttons": [
        _k("Overlay da Steam", "shift+tab", "STM"),
        _k("Print da Steam", "f12", "F12"),
        _k("Xbox Game Bar", "win+g", "BAR"),
        _k("Gravar últimos 30 s", "win+alt+g", "30S"),
        _k("Gravar / parar", "win+alt+r", "REC"),
        _k("Print (Game Bar)", "win+alt+printscreen", "PRT"),
        _a("Discord: mutar", "discord.mute", "MIC"),
        _a("Discord: ensurdecer", "discord.deafen", "AUD"),
        _a("Microfone do PC", "mic.toggle", "PC"),
    ]},
]}
# --------------------------------------------------------------------------
# Macros: um passo por linha ("teclas ctrl+t", "texto oi", "esperar 500", "abrir discord", "acao media.next")
# --------------------------------------------------------------------------
MACRO_VERBS = {"teclas": "keys", "tecla": "keys", "keys": "keys", "texto": "text", "text": "text",
               "esperar": "wait", "wait": "wait", "abrir": "open", "open": "open",
               "acao": "action", "ação": "action", "action": "action"}
MACRO_MAX_STEPS, MACRO_MAX_WAIT_MS = 40, 60000
PROG_BAD = re.compile(r"[\\/:\x00-\x1f\x7f]")  # "abrir X": X é id/nome do config.json, não caminho


def _ms(arg):
    a = arg.lower().replace(",", ".").strip()
    if a.endswith("ms"):
        return float(a[:-2])
    if a.endswith("s"):
        return float(a[:-1]) * 1000
    return float(a)


def parse_macro(src, valid_combo, shortcuts):
    """shortcuts: {id_ou_nome_minúsculo: id}. Devolve [(tipo, valor)] ou ValueError com a linha."""
    steps, waited = [], 0.0
    for n, line in enumerate(str(src).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        verb, _, arg = line.partition(" ")
        kind, arg = MACRO_VERBS.get(verb.lower()), arg.strip()
        if not kind:
            raise ValueError(f"linha {n}: '{verb}' não existe (use teclas, texto, esperar, abrir ou acao)")
        try:
            if kind == "keys":
                arg = arg.lower().replace(" ", "")
                valid_combo(arg)
            elif kind == "text":
                arg = arg.replace("\\n", "\n")[:500]
            elif kind == "wait":
                arg = max(0.0, _ms(arg))
                waited += arg
            elif kind == "open":
                # programa ainda fora do config.json: aceita o nome (só nome, nunca caminho);
                # na hora de rodar, deck_press confere de novo e só abre ids do config.json
                sid = shortcuts.get(arg.lower())
                if sid:
                    arg = sid
                elif not arg or len(arg) > 60 or PROG_BAD.search(arg):
                    raise ValueError("nome de programa inválido")
                else:
                    arg = arg.lower()
            elif kind == "action":
                if arg not in DECK_ACTIONS:
                    raise ValueError(f"ação '{arg}' não existe")
        except ValueError as e:
            raise ValueError(f"linha {n}: {e}") from None
        steps.append((kind, arg))
    if not steps:
        raise ValueError("a macro está vazia")
    if len(steps) > MACRO_MAX_STEPS:
        raise ValueError(f"no máximo {MACRO_MAX_STEPS} passos")
    if waited > MACRO_MAX_WAIT_MS:
        raise ValueError("no máximo 60 s de espera somando tudo")
    return steps


APP_RE = re.compile(r"^(@jogo|[\w .()+-]{1,60}\.exe)$")


def _apps(pg):
    """Lista de programas (exe) que abrem a página sozinha."""
    out = []
    for a in (pg.get("apps") or [])[:12]:
        a = str(a).strip().lower()
        if a and not a.endswith(".exe") and a != "@jogo":
            a += ".exe"
        if APP_RE.match(a) and a not in out:
            out.append(a)
    return out

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _svg_names():
    """Ícones desenhados (campo "svg" do botão): lista fechada = os <symbol id="ic-..."> de web/icons/lucide.svg.
    O celular só manda o nome; o app monta o desenho a partir do arquivo."""
    try:
        text = (Path(__file__).resolve().parent / "web" / "icons" / "lucide.svg").read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    return frozenset(re.findall(r'<symbol id="ic-([a-z0-9-]{1,40})"', text))


SVG_ICONS = _svg_names()
# ícones só em texto: tira emoji colorido (e os seletores que forçam emoji)
NO_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u231A-\u23FF\uFE0F\u200D\u20E3]")


class Deck:
    def __init__(self, base: Path):
        self.path = base / "deck.json"

    def get(self):
        try:
            if self.path.exists():
                return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("deck.json inválido, usando o padrão: %s", e)
        return json.loads(json.dumps(DEFAULT_DECK))

    def save(self, deck, valid_combo, shortcuts=None):
        pages = deck.get("pages") if isinstance(deck, dict) else None
        if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
            raise ValueError("deck precisa de 1 a 20 páginas")
        clean = []
        for pg in pages:
            name = str(pg.get("name") or "Página")[:24]
            apps = _apps(pg)
            if pg.get("auto") == "shortcuts":
                clean.append({"name": name, "auto": "shortcuts", **({"apps": apps} if apps else {})})
                continue
            buttons = []
            for b in (pg.get("buttons") or [])[:48]:
                t, v = b.get("type"), str(b.get("value") or "")
                if t == "keys":
                    valid_combo(v)
                elif t == "text":
                    v = v[:1000]
                elif t == "action":
                    if v not in DECK_ACTIONS:
                        raise ValueError(f"ação desconhecida: {v}")
                elif t == "shortcut":
                    v = v[:64]
                elif t == "macro":
                    v = v[:4000]
                    parse_macro(v, valid_combo, shortcuts or {})
                else:
                    raise ValueError(f"tipo de botão inválido: {t}")
                nb = {"label": str(b.get("label") or "?")[:40], "type": t, "value": v}
                if b.get("icon"):
                    nb["icon"] = NO_EMOJI.sub("", str(b["icon"]))[:8]
                if b.get("svg"):
                    if str(b["svg"]) not in SVG_ICONS:
                        raise ValueError(f"ícone desconhecido: {str(b['svg'])[:40]}")
                    nb["svg"] = str(b["svg"])
                if b.get("color") and HEX.match(str(b["color"])):
                    nb["color"] = b["color"]
                if b.get("confirm"):
                    nb["confirm"] = True
                buttons.append(nb)
            clean.append({"name": name, "buttons": buttons, **({"apps": apps} if apps else {})})
        data = {"pages": clean}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return data

    def reset(self):
        self.path.unlink(missing_ok=True)
        return self.get()

    def button(self, page, index):
        pages = self.get()["pages"]
        try:
            pg = pages[int(page)]
            return pg["buttons"][int(index)]
        except (IndexError, KeyError, ValueError, TypeError):
            raise ValueError("botão não existe (deck mudou?)") from None


# --------------------------------------------------------------------------
# Downloads temporários (PC -> celular): /dl/<id>/<nome>
# --------------------------------------------------------------------------
DL: dict = {}  # id -> {"exp", "name", "path" | "data", "type"}
DL_MAX = 300 * 1024 * 1024


def dl_register(name, path=None, data=None, ctype="application/octet-stream", ttl=900):
    now = time.time()
    for k in [k for k, v in DL.items() if v["exp"] < now]:
        del DL[k]
    i = secrets.token_urlsafe(16)
    DL[i] = {"exp": now + ttl, "name": name, "path": path, "data": data, "type": ctype}
    from urllib.parse import quote
    return f"/dl/{i}/{quote(name)}"


def dl_fetch(path):
    """(nome, bytes, content-type) ou None."""
    parts = path.split("/")
    if len(parts) < 3:
        return None
    e = DL.get(parts[2])
    if not e or e["exp"] < time.time():
        return None
    if e["data"] is not None:
        return e["name"], e["data"], e["type"]
    p = Path(e["path"])
    if not p.is_file() or p.stat().st_size > DL_MAX:
        return None
    return e["name"], p.read_bytes(), e["type"]


# --------------------------------------------------------------------------
# Área de transferência
# --------------------------------------------------------------------------
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x2
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]


def _open_clipboard():
    for _ in range(20):  # outro programa pode estar com ela aberta
        if user32.OpenClipboard(None):
            return
        time.sleep(0.03)
    raise RuntimeError("área de transferência ocupada")


def clip_set_text(text):
    text = str(text)[:1_000_000]
    data = (text + "\0").encode("utf-16-le")
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            raise RuntimeError("não consegui copiar")
    finally:
        user32.CloseClipboard()


def _clip_text():
    _open_clipboard()
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clip_get():
    """Texto, imagem (print) ou arquivos copiados no PC."""
    from PIL import Image, ImageGrab
    try:
        got = ImageGrab.grabclipboard()
    except Exception:  # noqa: BLE001
        got = None
    if isinstance(got, Image.Image):
        png = io.BytesIO()
        got.save(png, "PNG")
        prev = got.convert("RGB")
        prev.thumbnail((1200, 1200))
        jpg = io.BytesIO()
        prev.save(jpg, "JPEG", quality=80)
        name = time.strftime("captura-%Y%m%d-%H%M%S.png")
        return {"kind": "image", "name": name, "w": got.width, "h": got.height,
                "preview": base64.b64encode(jpg.getvalue()).decode(),
                "url": dl_register(name, data=png.getvalue(), ctype="image/png")}
    if isinstance(got, list) and got:
        files = []
        for f in got[:50]:
            p = Path(f)
            if p.is_file():
                size = p.stat().st_size
                files.append({"name": p.name, "size": size,
                              "url": dl_register(p.name, path=str(p)) if size <= DL_MAX else None})
            else:
                files.append({"name": p.name, "size": None, "url": None, "dir": True})
        return {"kind": "files", "files": files}
    text = _clip_text()
    if text:
        return {"kind": "text", "text": text[:200_000]}
    return {"kind": "empty"}


# --------------------------------------------------------------------------
# Arquivos celular -> PC (em pedaços pelo WebSocket)
# --------------------------------------------------------------------------
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# nomes de dispositivo do Windows: "CON.txt", "nul", "com1.tar.gz" não podem ser arquivos comuns
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class Uploads:
    def __init__(self, folder):
        self.folder = Path(os.path.expandvars(folder))
        self.active: dict = {}

    def sweep(self):
        """Cancela envios parados há mais de 5 min (fecha o arquivo e apaga o .part)."""
        now = time.time()
        for i, u in list(self.active.items()):
            if now - u["t"] > 300:
                self.cancel(i)

    PART_RE = re.compile(r"^\.[0-9a-f]{16}\.part$")  # o nome que begin() cria

    def clean_stale_parts(self, max_age=600):
        """Na abertura: apaga .part de envios de uma execução anterior (servidor fechado no meio).
        Só arquivos com o nome exato que begin() cria e com mais de max_age segundos."""
        n, now = 0, time.time()
        try:
            entries = list(os.scandir(self.folder))
        except OSError:
            return 0  # pasta ainda não existe
        for e in entries:
            try:
                if (self.PART_RE.match(e.name) and e.is_file(follow_symlinks=False)
                        and now - e.stat(follow_symlinks=False).st_mtime > max_age):
                    os.remove(e.path)
                    n += 1
            except OSError as err:
                log.warning("não consegui apagar %s: %s", e.name, err)
        return n

    def cancel_owner(self, owner):
        """A conexão que começou o envio caiu: cancela os envios dela."""
        for i, u in list(self.active.items()):
            if owner is not None and u.get("owner") is owner:
                self.cancel(i)

    def begin(self, name, size, owner=None):
        self.sweep()
        name = BAD.sub("_", os.path.basename(str(name))).strip(" .")[:120] or "arquivo"
        if name.split(".", 1)[0].rstrip(" ").upper() in RESERVED:
            name = "_" + name
        size = int(size)
        if size < 0 or size > 8 * 1024**3:
            raise ValueError("arquivo grande demais")
        self.folder.mkdir(parents=True, exist_ok=True)
        i = secrets.token_hex(8)
        part = self.folder / f".{i}.part"
        self.active[i] = {"name": name, "size": size, "got": 0, "part": part, "fh": open(part, "wb"), "t": time.time(), "owner": owner}
        return {"id": i}

    def chunk(self, i, data):
        u = self.active.get(i)
        if not u:
            raise ValueError("envio não encontrado")
        raw = base64.b64decode(data)
        if u["got"] + len(raw) > u["size"]:
            self.cancel(i)
            raise ValueError("arquivo maior que o anunciado")
        u["fh"].write(raw)
        u["got"] += len(raw)
        u["t"] = time.time()
        return {"got": u["got"]}

    def end(self, i):
        u = self.active.pop(i, None)
        if not u:
            raise ValueError("envio não encontrado")
        u["fh"].close()
        if u["got"] != u["size"]:
            u["part"].unlink(missing_ok=True)
            raise ValueError("envio incompleto")
        dest = self.folder / u["name"]
        stem, suf, n = dest.stem, dest.suffix, 1
        while dest.exists():
            n += 1
            dest = self.folder / f"{stem} ({n}){suf}"
        u["part"].replace(dest)
        log.info("arquivo recebido do celular: %s (%d bytes)", dest.name, u["size"])
        self.last = dest
        return {"name": dest.name, "path": str(dest)}

    def cancel(self, i):
        u = self.active.pop(i, None)
        if u:
            u["fh"].close()
            u["part"].unlink(missing_ok=True)

    def open_folder(self, select_last=True):
        import subprocess
        last = getattr(self, "last", None)
        if select_last and last and last.exists():
            subprocess.Popen(["explorer", "/select,", str(last)])
        else:
            self.folder.mkdir(parents=True, exist_ok=True)
            os.startfile(self.folder)


# --------------------------------------------------------------------------
# Tela do PC (captura + clique por toque)
# --------------------------------------------------------------------------
_DPI_AWARE = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]


class _DpiAware:
    """Coordenadas em pixels reais (sem a escala do Windows) nesta thread."""
    def __enter__(self):
        self.old = user32.SetThreadDpiAwarenessContext(_DPI_AWARE)

    def __exit__(self, *a):
        if self.old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(self.old))


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_MonEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MonEnumProc, wintypes.LPARAM]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]


def monitors():
    out = []
    with _DpiAware():
        def cb(hmon, _hdc, _rect, _lp):
            mi = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
            user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
            r = mi.rcMonitor
            out.append({"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top, "primary": bool(mi.dwFlags & 1)})
            return True
        user32.EnumDisplayMonitors(None, None, _MonEnumProc(cb), 0)
    out.sort(key=lambda m: (not m["primary"], m["x"]))
    return out


_last_frame = {}


def screen_shot(mon=0, width=1280, quality=60, client=None):
    from PIL import ImageDraw, ImageGrab
    mons = monitors()
    m = mons[max(0, min(int(mon), len(mons) - 1))]
    with _DpiAware():
        im = ImageGrab.grab(bbox=(m["x"], m["y"], m["x"] + m["w"], m["y"] + m["h"]), all_screens=True)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
    width = max(320, min(int(width), 2560, m["w"]))
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), 2)  # BILINEAR: rápido
    sc = im.width / m["w"]
    cx, cy = (pt.x - m["x"]) * sc, (pt.y - m["y"]) * sc
    if 0 <= cx < im.width and 0 <= cy < im.height:  # a captura não inclui o cursor
        d = ImageDraw.Draw(im)
        k = max(10, im.width / 90)
        d.polygon([(cx, cy), (cx, cy + k * 1.5), (cx + k * 0.42, cy + k * 1.12), (cx + k * 1.05, cy + k * 1.05)],
                  fill="white", outline="black")
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=max(20, min(int(quality), 90)))
    data = buf.getvalue()
    h = hashlib.blake2b(data, digest_size=8).digest()
    meta = {"mon": mons.index(m), "mons": len(mons), "w": im.width, "h": im.height}
    if client is not None and _last_frame.get(client) == h:
        return {**meta, "same": True}
    if client is not None:
        _last_frame[client] = h
    return {**meta, "jpg": base64.b64encode(data).decode()}


def screen_point(mon, x, y):
    """Move o cursor para (x, y) em 0..1 do monitor."""
    mons = monitors()
    m = mons[max(0, min(int(mon), len(mons) - 1))]
    x = max(0.0, min(1.0, float(x)))
    y = max(0.0, min(1.0, float(y)))
    with _DpiAware():
        user32.SetCursorPos(int(m["x"] + x * (m["w"] - 1)), int(m["y"] + y * (m["h"] - 1)))


# --------------------------------------------------------------------------
# Downloads concluídos (pasta Downloads)
# --------------------------------------------------------------------------
TEMP_EXT = {".crdownload", ".part", ".partial", ".tmp", ".opdownload", ".download", ".!ut", ".aria2"}


class DownloadWatcher:
    def __init__(self, folder):
        self.folder = Path(os.path.expandvars(folder))
        self.known = None
        self.pending = {}  # nome -> tamanho na última olhada (esperando ficar estável)

    def _scan(self):
        out = {}
        try:
            with os.scandir(self.folder) as it:
                for e in it:
                    if e.is_file() and not e.name.startswith(".") and Path(e.name).suffix.lower() not in TEMP_EXT:
                        out[e.name] = e.stat().st_size
        except OSError:
            pass
        return out

    def poll(self):
        """Lista de arquivos que acabaram de chegar (tamanho estável)."""
        now = self._scan()
        if self.known is None:
            self.known = now
            return []
        done = []
        for name, size in now.items():
            if name in self.known:
                continue
            if self.pending.get(name) == size and size > 0:
                done.append({"name": name, "size": size})
                self.known[name] = size
                self.pending.pop(name, None)
            else:
                self.pending[name] = size
        for name in list(self.known):
            if name not in now:
                del self.known[name]
        return done
