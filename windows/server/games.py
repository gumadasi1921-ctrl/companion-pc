"""Detecção do jogo em execução (Steam, Epic, GOG, Riot, EA, Ubisoft e pastas extras),
histórico de sessões e estado do Discord.

O jogo é identificado pelo caminho do executável: se ele está dentro da pasta de
instalação de um jogo conhecido, conta como jogando. A sessão começa quando o
primeiro processo do jogo foi criado.
"""
import base64
import ctypes
import glob
import json
import logging
import os
import re
import time
import winreg
from ctypes import wintypes
from pathlib import Path

import psutil

log = logging.getLogger("companion")

LAUNCHER_PROCS = {
    "steam.exe", "steamwebhelper.exe", "steamservice.exe", "gameoverlayui.exe",
    "epicgameslauncher.exe", "epicwebhelper.exe", "epiconlineservices.exe", "epiconlineserviceshost.exe",
    "riotclientservices.exe", "riotclientux.exe", "riotclientuxrender.exe", "riotclientcrashhandler.exe",
    "galaxyclient.exe", "galaxyclient helper.exe", "galaxycommunication.exe",
    "eadesktop.exe", "eabackgroundservice.exe", "ealocalhostsvc.exe", "link2ea.exe",
    "upc.exe", "uplaywebcore.exe", "ubisoftconnect.exe", "ubisoftgamelauncher.exe",
    "unrealcefsubprocess.exe", "crashreportclient.exe", "easyanticheat.exe", "easyanticheat_eos.exe",
    "beservice.exe", "vgc.exe", "vgtray.exe",
}
DEFAULT_IGNORE = ["Wallpaper Engine", "Steamworks Common Redistributables", "Lossless Scaling",
                  "Soundpad", "SteamVR", "Riot Client"]
HISTORY_MAX = 200


# --------------------------------------------------------------------------
# VDF (formato de texto da Steam)
# --------------------------------------------------------------------------
_TOK = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])')


def parse_vdf(text):
    stack, cur, key = [], {}, None
    for m in _TOK.finditer(text):
        s, brace = m.group(1), m.group(2)
        if brace == "{":
            new = {}
            cur[key] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == "}":
            cur = stack.pop() if stack else cur
        elif key is None:
            key = s.replace("\\\\", "\\")
        else:
            cur[key] = s.replace("\\\\", "\\")
            key = None
    return cur


def _ci(d, *keys):
    """Acesso a dicionário VDF sem diferenciar maiúsculas."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = next((v for kk, v in d.items() if kk.lower() == k.lower()), None)
    return d


def _reg(root, path, name):
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


ICON_REGISTER = None   # o servidor liga isto a icons.Icons.register: devolve um id opaco (o caminho nunca sai daqui)


def _icon_id(path):
    return ICON_REGISTER(path) if ICON_REGISTER and path else None


def steam_libraries(sp):
    """Pastas de biblioteca da Steam (a principal e as de libraryfolders.vdf), sem repetir."""
    libs = {os.path.normcase(sp)}
    try:
        lf = parse_vdf(Path(sp, "steamapps", "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore"))
        for v in (_ci(lf, "libraryfolders") or {}).values():
            if isinstance(v, dict) and v.get("path"):
                libs.add(os.path.normcase(v["path"]))
    except OSError:
        pass
    return libs


# --------------------------------------------------------------------------
# Catálogo de jogos instalados
# --------------------------------------------------------------------------
class Catalog:
    def __init__(self, cfg):
        self.cfg = cfg
        self.roots = []          # (pasta_minúscula, info)
        self.steam_by_id = {}
        self.steam_path = None
        self.built = 0.0

    def build(self):
        roots, ignore = [], {n.lower() for n in self.cfg.get("games_ignore", DEFAULT_IGNORE)}

        def add(path, name, store, appid=None, launch=None):
            if path and name and name.lower() not in ignore and os.path.isdir(path):
                if store == "Steam" and appid:
                    launch = f"steam://rungameid/{appid}"
                roots.append((os.path.normcase(os.path.abspath(path)) + os.sep,
                              {"name": name, "store": store, "appid": appid, "launch": launch}))

        # Steam
        sp = _reg(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
        self.steam_path = sp
        self.steam_by_id = {}
        if sp and os.path.isdir(sp):
            for lib in steam_libraries(sp):
                for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
                    try:
                        st = _ci(parse_vdf(Path(acf).read_text(encoding="utf-8", errors="ignore")), "AppState") or {}
                    except OSError:
                        continue
                    name, appid, inst = st.get("name"), st.get("appid"), st.get("installdir")
                    if appid:
                        self.steam_by_id[appid] = name
                    if inst:
                        add(os.path.join(lib, "steamapps", "common", inst), name, "Steam", appid)

        # Epic
        for item in glob.glob(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item"):
            try:
                d = json.loads(Path(item).read_text(encoding="utf-8", errors="ignore"))
                ids = [d.get(k) for k in ("CatalogNamespace", "CatalogItemId", "AppName")]
                uri = ("com.epicgames.launcher://apps/" + "%3A".join(ids) + "?action=launch&silent=true") if all(ids) else None
                add(d.get("InstallLocation"), d.get("DisplayName"), "Epic", launch=uri)
            except (OSError, ValueError):
                pass

        # GOG (registro)
        for base in (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        sub = winreg.EnumKey(k, i)
                        add(_reg(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "path"),
                            _reg(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "gameName"), "GOG",
                            launch=_reg(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "exe"))
            except OSError:
                pass

        # Pastas onde cada subpasta é um jogo
        folders = {
            r"C:\Riot Games": "Riot",
            r"C:\Program Files\EA Games": "EA",
            r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games": "Ubisoft",
        }
        extra = [f for f in os.environ.get("COMPANION_GAME_FOLDERS", "").split(os.pathsep) if f]  # testes
        for f in list(self.cfg.get("game_folders", [])) + extra:
            folders[f] = "PC"
        for folder, store in folders.items():
            for sub in glob.glob(os.path.join(folder, "*")):
                if os.path.isdir(sub):
                    add(sub, os.path.basename(sub), store)

        roots.sort(key=lambda r: len(r[0]), reverse=True)  # pasta mais específica primeiro
        self.roots, self.built = roots, time.monotonic()
        log.info("catálogo de jogos: %d jogos", len(roots))

    def match(self, exe):
        if not exe:
            return None
        exe = os.path.normcase(exe)
        for root, info in self.roots:
            if exe.startswith(root):
                return info
        return None

    # -- extras da Steam --
    def steam_art(self, appid):
        """Capa 460x215 do cache da biblioteca (formato antigo e novo, com subpastas)."""
        if not (self.steam_path and appid):
            return None
        base = os.path.join(self.steam_path, "appcache", "librarycache")
        old = os.path.join(base, f"{appid}_header.jpg")
        if os.path.isfile(old):
            return old
        for name in ("header.jpg", "library_header.jpg", "library_hero.jpg"):
            hits = glob.glob(os.path.join(base, appid, name)) + glob.glob(os.path.join(base, appid, "*", name))
            if hits:
                return max(hits, key=os.path.getmtime)
        return None

    def steam_icon(self, appid):
        """ID do ícone local da Steam (<appid>_icon.jpg do cache da biblioteca), se o arquivo existir. Nada de adivinhar exe."""
        appid = str(appid or "")
        if not (self.steam_path and appid.isdigit()):
            return None
        p = os.path.join(self.steam_path, "appcache", "librarycache", f"{appid}_icon.jpg")
        return _icon_id(p) if os.path.isfile(p) else None

    def icon_for(self, info):
        """Ícone de um jogo do catálogo: Steam = _icon.jpg local; GOG = o exe de launch; Epic e o resto = nenhum."""
        if info.get("appid"):
            return self.steam_icon(info["appid"])
        launch = info.get("launch")
        return _icon_id(launch) if launch and "://" not in launch else None

    def info_by_name(self, name):
        return next((i for _r, i in self.roots if i["name"] == name), None)

    def steam_playtime_min(self, appid):
        if not (self.steam_path and appid):
            return None
        cfgs = glob.glob(os.path.join(self.steam_path, "userdata", "*", "config", "localconfig.vdf"))
        if not cfgs:
            return None
        newest = max(cfgs, key=os.path.getmtime)
        try:
            d = parse_vdf(Path(newest).read_text(encoding="utf-8", errors="ignore"))
            apps = _ci(d, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps") or {}
            pt = _ci(apps, appid, "Playtime")
            return int(pt) if pt else None
        except (OSError, ValueError):
            return None


# --------------------------------------------------------------------------
# Discord
# --------------------------------------------------------------------------
user32 = ctypes.windll.user32
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def _window_titles(pids):
    titles = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    titles.append(buf.value)
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return titles


def discord_state(procs):
    pids = {p["pid"] for p in procs if p["name"] == "discord.exe"}
    if not pids:
        return {"running": False}
    channel = server = None
    for t in _window_titles(pids):
        m = re.match(r"^(.*?) - Discord$", t)
        if not m:
            continue
        left = m.group(1)
        if " | " in left:
            channel, server = left.split(" | ", 1)
        else:
            channel = left
        break
    return {"running": True, "channel": channel, "server": server}


# --------------------------------------------------------------------------
# Monitor de jogo + histórico
# --------------------------------------------------------------------------
class GameMonitor:
    def __init__(self, cfg, base: Path):
        self.cfg = cfg
        self.catalog = Catalog(cfg)
        self.hist_path = base / "games-history.json"
        self.history = self._load()
        self.current = None      # {"name","store","appid","start"}
        self.state = {"game": None, "recent": [], "today_s": 0, "discord": {"running": False}}
        self.art = {}            # id -> base64 jpg
        self.playtime_cache = {}
        self.exe_cache = {}      # (pid, create_time) -> exe ou None

    def _load(self):
        try:
            return json.loads(self.hist_path.read_text(encoding="utf-8")).get("sessions", [])
        except (OSError, ValueError):
            return []

    def _save(self):
        try:
            self.hist_path.write_text(json.dumps({"sessions": self.history[-HISTORY_MAX:]}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
        except OSError as e:
            log.warning("histórico de jogos: %s", e)

    def _end_session(self, now):
        cur = self.current
        if cur and now - cur["start"] >= 60:  # ignora aberturas de menos de 1 min
            self.history.append({"name": cur["name"], "store": cur["store"], "appid": cur.get("appid"),
                                 "start": int(cur["start"]), "end": int(now)})
            self._save()
            log.info("sessão encerrada: %s (%d min)", cur["name"], (now - cur["start"]) // 60)
        self.current = None

    def _today_seconds(self, name, now):
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        total = 0
        for s in self.history:
            if name is None or s["name"] == name:
                total += max(0, s["end"] - max(s["start"], midnight))
        if self.current and (name is None or self.current["name"] == name):
            total += now - max(self.current["start"], midnight)
        return int(total)

    def _art_for(self, info, size=(300, 140), prefix="steam"):
        if info.get("store") != "Steam":
            return None
        key = f"{prefix}-{info['appid']}"
        if key not in self.art:
            path = self.catalog.steam_art(info["appid"])
            data = None
            if path:
                try:
                    from PIL import Image
                    import io
                    from PIL import ImageOps
                    im = ImageOps.fit(Image.open(path).convert("RGB"), size, Image.LANCZOS)
                    out = io.BytesIO()
                    im.save(out, "JPEG", quality=85)
                    data = base64.b64encode(out.getvalue()).decode()
                except Exception:  # noqa: BLE001
                    data = None
            self.art[key] = data
        return key if self.art[key] else None

    # -- biblioteca e histórico (celular) --
    @staticmethod
    def _gid(info):
        return f"{info['store']}:{info.get('appid') or info['name']}"

    def _sessions(self, now):
        cur = [{"name": self.current["name"], "start": self.current["start"], "end": now}] if self.current else []
        return self.history + cur

    def _icon_field(self, name):
        info = self.catalog.info_by_name(name)
        ic = self.catalog.icon_for(info) if info else None
        return {"icon": ic} if ic else {}

    def library(self):
        """Jogos instalados que dá para abrir, os jogados por último primeiro."""
        now, last = time.time(), {}
        for s in self._sessions(now):
            last[s["name"]] = max(last.get(s["name"], 0), s["end"])
        out, seen = [], set()
        for _root, info in self.catalog.roots:
            if not info.get("launch") or info["name"] in seen:
                continue
            seen.add(info["name"])
            key = self._art_for(info, (460, 215), "lib")  # capa grande para a biblioteca
            out.append({"id": self._gid(info), "name": info["name"], "store": info["store"],
                        "art": self.art.get(key) if key else None, "icon": self.catalog.icon_for(info),
                        "ago": int(now - last[info["name"]]) if info["name"] in last else None,
                        "running": bool(self.current and self.current["name"] == info["name"])})
        out.sort(key=lambda g: (g["ago"] is None, g["ago"] or 0, g["name"].lower()))
        return out

    def launch(self, gid):
        for _root, info in self.catalog.roots:
            if self._gid(info) == gid and info.get("launch"):
                target = info["launch"]
                if "://" in target:
                    os.startfile(target)
                else:
                    os.startfile(target, cwd=os.path.dirname(target))
                log.info("abrindo jogo: %s", info["name"])
                return info["name"]
        raise ValueError("jogo não encontrado (o catálogo mudou?)")

    def summary(self, days=7):
        """Horas jogadas por dia na última semana e os jogos mais jogados nela."""
        now = time.time()
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        starts = [midnight - 86400 * i for i in range(days - 1, -1, -1)]
        per_day, per_game = [0.0] * days, {}
        for s in self._sessions(now):
            for i, d0 in enumerate(starts):
                ov = min(s["end"], d0 + 86400) - max(s["start"], d0)
                if ov > 0:
                    per_day[i] += ov
                    per_game[s["name"]] = per_game.get(s["name"], 0) + ov
        wd = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
        return {"days": [{"d": wd[time.localtime(d0).tm_wday], "s": int(v)} for d0, v in zip(starts, per_day)],
                "top": [{"name": n, "s": int(v), **self._icon_field(n)} for n, v in sorted(per_game.items(), key=lambda x: -x[1])[:6]],
                "week_s": int(sum(per_day))}

    def poll(self):
        """Roda numa thread (a cada ~3 s). Atualiza self.state."""
        if time.monotonic() - self.catalog.built > 600:
            self.catalog.build()
        now = time.time()
        procs, found, alive = [], {}, set()
        for p in psutil.process_iter():
            try:
                info = p.as_dict(attrs=["pid", "name", "create_time"])  # não usar p.info (compartilhado)
            except (psutil.Error, ProcessLookupError):  # processo fechou durante a leitura
                continue
            name = (info["name"] or "").lower()
            procs.append({"pid": info["pid"], "name": name})
            if name in LAUNCHER_PROCS or "crash" in name:
                continue
            key = (info["pid"], info["create_time"])
            alive.add(key)
            if key not in self.exe_cache:  # caminho do exe é caro; pega uma vez por processo
                try:
                    self.exe_cache[key] = p.exe()
                except psutil.Error:
                    self.exe_cache[key] = None
            game = self.catalog.match(self.exe_cache[key])
            if not game:
                continue
            try:
                mem = p.memory_info().rss
            except psutil.Error:
                mem = 0
            g = found.setdefault(game["name"], {**game, "start": info["create_time"] or now, "mem": 0, "exe": self.exe_cache[key]})
            g["start"] = min(g["start"], info["create_time"] or now)
            g["mem"] += mem
        self.exe_cache = {k: v for k, v in self.exe_cache.items() if k in alive}

        # Steam informa o jogo rodando no registro (ajuda com jogos fora das pastas conhecidas)
        rid = str(_reg(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "RunningAppID") or 0)
        if rid != "0" and not any(g.get("appid") == rid for g in found.values()):
            name = self.catalog.steam_by_id.get(rid)
            ignore = {n.lower() for n in self.cfg.get("games_ignore", DEFAULT_IGNORE)}
            if name and name.lower() not in ignore:
                start = self.current["start"] if self.current and self.current["name"] == name else now
                found[name] = {"name": name, "store": "Steam", "appid": rid, "start": start, "mem": 0}

        best = max(found.values(), key=lambda g: g["mem"]) if found else None
        if self.current and (not best or best["name"] != self.current["name"]):
            self._end_session(now)
        if best and not self.current:
            self.current = {k: best[k] for k in ("name", "store", "appid", "start")}
            self.current["exe"] = best.get("exe")
            log.info("jogo detectado: %s (%s)", best["name"], best["store"])

        game = None
        if self.current:
            c = self.current
            pt = None
            if c.get("appid"):
                if c["appid"] not in self.playtime_cache:
                    self.playtime_cache[c["appid"]] = self.catalog.steam_playtime_min(c["appid"])
                pt = self.playtime_cache[c["appid"]]
            game = {"name": c["name"], "store": c["store"], "elapsed": int(now - c["start"]),
                    "today_s": self._today_seconds(c["name"], now),
                    "total_h": round(pt / 60, 1) if pt else None,
                    "art": self._art_for(c),
                    "icon": _icon_id(c.get("exe")) or self.catalog.steam_icon(c.get("appid"))}

        recent, seen = [], set()
        for s in reversed(self.history):
            if s["name"] in seen:
                continue
            seen.add(s["name"])
            r = {"name": s["name"], "store": s["store"], "dur": s["end"] - s["start"], "ago": int(now - s["end"])}
            info = self.catalog.info_by_name(s["name"])
            ic = self.catalog.icon_for(info) if info else None
            if ic:
                r["icon"] = ic
            recent.append(r)
            if len(recent) == 4:
                break

        self.state = {"game": game, "recent": recent, "today_s": self._today_seconds(None, now),
                      "discord": discord_state(procs)}
        return self.state
