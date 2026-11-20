"""Informações extras do PC: mídia tocando (Windows GSMTC), volume, mixer e saída
de áudio (Core Audio), GPU (nvidia-smi) e sensores (LibreHardwareMonitor).

Cada parte é opcional: se a biblioteca não estiver instalada, a função
correspondente devolve None e o resto do servidor continua funcionando.
"""
import asyncio
import base64
import hashlib
import io
import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

log = logging.getLogger("companion")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MEDIA = None  # instância de Media, criada pelo servidor

# --------------------------------------------------------------------------
# Mídia (o que está tocando)
# --------------------------------------------------------------------------
try:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as _MM
    from winrt.windows.storage.streams import Buffer, InputStreamOptions
    HAS_MEDIA = True
except ImportError:  # pragma: no cover
    HAS_MEDIA = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False

STATUS = {4: "playing", 5: "paused", 3: "stopped", 2: "stopped", 1: "opened", 0: "closed"}
APP_NAMES = {
    "spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
    "opera": "Opera", "zunemusic": "Media Player", "microsoft.media.player": "Media Player",
    "vlc": "VLC", "brave": "Brave", "deezer": "Deezer", "youtube": "YouTube Music",
}


def app_name(aumid: str) -> str:
    low = (aumid or "").lower()
    for key, name in APP_NAMES.items():
        if key in low:
            return name
    base = (aumid or "?").split("!")[-1].split("\\")[-1]
    return base.removesuffix(".exe")[:24]


class Media:
    def __init__(self):
        self.mgr = None
        self.art_key = None
        self.art_b64 = None

    async def _session(self):
        if not HAS_MEDIA:
            return None
        if self.mgr is None:
            self.mgr = await _MM.request_async()
        return self.mgr.get_current_session()

    async def info(self):
        """Dados da mídia atual (ou None). Atualiza a capa quando a faixa muda."""
        try:
            s = await self._session()
            if not s:
                return None
            p = await s.try_get_media_properties_async()
            tl = s.get_timeline_properties()
            pb = s.get_playback_info()
            status = STATUS.get(int(pb.playback_status), "unknown")
            pos = tl.position.total_seconds()
            end = tl.end_time.total_seconds()
            if status == "playing" and tl.last_updated_time:
                # a posição só é atualizada pelo app de tempos em tempos
                pos += (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
            pos = max(0.0, min(pos, end)) if end > 0 else max(0.0, pos)

            key = hashlib.sha1(f"{p.title}|{p.artist}|{p.album_title}".encode()).hexdigest()[:12]
            if key != self.art_key:
                self.art_key = key
                self.art_b64 = await self._art(p.thumbnail)
            c = pb.controls
            return {
                "title": p.title or "", "artist": p.artist or "", "album": p.album_title or "",
                "app": app_name(s.source_app_user_model_id), "status": status,
                "pos": round(pos, 1), "dur": round(end, 1), "art": key if self.art_b64 else None,
                "can_next": bool(c.is_next_enabled), "can_prev": bool(c.is_previous_enabled),
                "can_seek": bool(c.is_playback_position_enabled),
            }
        except Exception as e:  # noqa: BLE001
            log.debug("media.info: %s", e)
            self.mgr = None
            return None

    async def _art(self, ref):
        if not ref:
            return None
        try:
            st = await ref.open_read_async()
            size = int(st.size)
            if not size or size > 8_000_000:
                return None
            buf = Buffer(size)
            await st.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            data = bytes(buf)
            if HAS_PIL:
                im = Image.open(io.BytesIO(data)).convert("RGB")
                im.thumbnail((200, 200))
                out = io.BytesIO()
                im.save(out, "JPEG", quality=85)
                data = out.getvalue()
            return base64.b64encode(data).decode()
        except Exception as e:  # noqa: BLE001
            log.debug("capa: %s", e)
            return None

    async def control(self, action, value=None):
        """Controla a sessão atual. Retorna False se não houver sessão."""
        s = await self._session() if HAS_MEDIA else None
        if not s:
            return False
        if action == "play_pause":
            await s.try_toggle_play_pause_async()
        elif action == "next":
            await s.try_skip_next_async()
        elif action == "prev":
            await s.try_skip_previous_async()
        elif action == "stop":
            await s.try_stop_async()
        elif action == "seek":
            tl = s.get_timeline_properties()
            cur = tl.position.total_seconds()
            pb = s.get_playback_info()
            if int(pb.playback_status) == 4 and tl.last_updated_time:
                cur += (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
            target = max(0.0, min(cur + float(value or 0), tl.end_time.total_seconds()))
            await s.try_change_playback_position_async(int(target * 10_000_000))
        else:
            return False
        return True


# --------------------------------------------------------------------------
# Volume (Core Audio via pycaw) - COM precisa de uma thread fixa
# --------------------------------------------------------------------------
_com = ThreadPoolExecutor(max_workers=1, thread_name_prefix="com")
_endpoint = None


def _init_com():
    import comtypes
    comtypes.CoInitialize()


def _ep():
    global _endpoint
    if _endpoint is None:
        from pycaw.pycaw import AudioUtilities
        spk = AudioUtilities.GetSpeakers()
        _endpoint = getattr(spk, "EndpointVolume", None)
        if _endpoint is None:  # API antiga do pycaw
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            _endpoint = cast(spk.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
    return _endpoint


def _volume_get():
    global _endpoint
    try:
        ep = _ep()
        return {"level": round(ep.GetMasterVolumeLevelScalar() * 100), "muted": bool(ep.GetMute())}
    except Exception as e:  # noqa: BLE001
        log.debug("volume: %s", e)
        _endpoint = None  # dispositivo pode ter mudado
        return None


def _volume_set(level=None, muted=None):
    ep = _ep()
    if level is not None:
        ep.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(level) / 100)), None)
    if muted is not None:
        ep.SetMute(1 if muted else 0, None)
    return _volume_get()


# --------------------------------------------------------------------------
# Mixer por aplicativo e dispositivo de saída
# --------------------------------------------------------------------------
MIXER_NAMES = {"discord": "Discord", "spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge",
               "firefox": "Firefox", "opera": "Opera", "brave": "Brave", "vlc": "VLC", "steam": "Steam",
               "steamwebhelper": "Steam", "obs64": "OBS"}


def _sessions():
    from pycaw.pycaw import AudioUtilities
    groups = {}
    for s in AudioUtilities.GetAllSessions():
        try:
            if s.ProcessId == 0 or s.Process is None:
                key, name = "_system", "Sons do sistema"
            else:
                exe = s.Process.name()
                key = exe.lower().removesuffix(".exe")
                name = MIXER_NAMES.get(key) or exe.removesuffix(".exe")
            if s.State == 2:  # AudioSessionStateExpired
                continue
            groups.setdefault(key, {"key": key, "name": name, "items": []})["items"].append(s)
        except Exception:  # noqa: BLE001  (processo fechou no meio)
            continue
    return groups


def _peak(s):
    try:
        from pycaw.pycaw import IAudioMeterInformation
        return s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue()
    except Exception:  # noqa: BLE001
        return 0.0


def _mixer_get():
    out = []
    for g in _sessions().values():
        vol = g["items"][0].SimpleAudioVolume
        out.append({"key": g["key"], "name": g["name"], "level": round(vol.GetMasterVolume() * 100),
                    "muted": bool(vol.GetMute()), "peak": round(max(_peak(s) for s in g["items"]), 3)})
    out.sort(key=lambda x: (x["key"] == "_system", x["name"].lower()))
    return {"apps": out, "master": _volume_get(), "outputs": _outputs()}


def _mixer_set(key, level=None, muted=None):
    g = _sessions().get(key)
    if not g:
        raise ValueError("app não está tocando som")
    for s in g["items"]:
        v = s.SimpleAudioVolume
        if level is not None:
            v.SetMasterVolume(max(0.0, min(1.0, float(level) / 100)), None)
        if muted is not None:
            v.SetMute(int(not v.GetMute()) if muted == "toggle" else int(bool(muted)), None)
    return _mixer_get() if muted is not None else None  # slider: resposta rápida


def _default_output_id():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetDeviceEnumerator().GetDefaultAudioEndpoint(0, 1).GetId()  # eRender, eMultimedia


def _outputs():
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        devs = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    cur = _default_output_id()
    return [{"id": d.id, "name": d.FriendlyName or "?", "default": d.id == cur} for d in devs]


def _set_output(dev_id=None, cycle=False):
    global _endpoint
    from pycaw.constants import ERole
    from pycaw.pycaw import AudioUtilities
    outs = _outputs()
    if cycle:
        if not outs:
            raise RuntimeError("nenhuma saída de áudio")
        i = next((n for n, o in enumerate(outs) if o["default"]), -1)
        dev_id = outs[(i + 1) % len(outs)]["id"]
    if dev_id not in {o["id"] for o in outs}:
        raise ValueError("saída de áudio desconhecida")
    AudioUtilities.SetDefaultDevice(dev_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
    _endpoint = None  # o volume mestre agora é de outro dispositivo
    return next(o["name"] for o in outs if o["id"] == dev_id)


async def _on_com(fn, *args):
    if not HAS_VOLUME:
        raise RuntimeError("pycaw não instalado")
    return await asyncio.get_running_loop().run_in_executor(_com, fn, *args)


# --------------------------------------------------------------------------
# Microfone do PC (mudo geral: vale para qualquer programa)
# --------------------------------------------------------------------------
_mic_cache = (0.0, None)


def _mics():
    """Microfone padrão e o de comunicação (Discord usa esse), sem repetir."""
    import comtypes
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import AudioUtilities
    en = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    out, seen = [], set()
    for role in (0, 2):  # eConsole, eCommunications
        try:
            d = en.GetDefaultAudioEndpoint(1, role)  # eCapture
        except Exception:  # noqa: BLE001  (nenhum microfone)
            continue
        if d.GetId() not in seen:
            seen.add(d.GetId())
            out.append(AudioUtilities.CreateDevice(d))
    return out


def _mic_get(fresh=False):
    global _mic_cache
    if not fresh and time.monotonic() - _mic_cache[0] < 1.5:
        return _mic_cache[1]
    try:
        m = _mics()
        res = {"muted": bool(m[0].EndpointVolume.GetMute()), "name": m[0].FriendlyName or "Microfone"} if m else None
    except Exception as e:  # noqa: BLE001
        log.debug("microfone: %s", e)
        res = None
    _mic_cache = (time.monotonic(), res)
    return res


def _mic_set(muted):
    m = _mics()
    if not m:
        raise RuntimeError("nenhum microfone encontrado no PC")
    if muted == "toggle":
        muted = not m[0].EndpointVolume.GetMute()
    for d in m:
        d.EndpointVolume.SetMute(1 if muted else 0, None)
    return _mic_get(fresh=True)


async def mic_get():
    if not HAS_VOLUME:
        return None
    return await _on_com(_mic_get)


async def mic_set(muted):
    return await _on_com(_mic_set, muted)


async def mixer_get():
    return await _on_com(_mixer_get)


async def mixer_set(key, level=None, muted=None):
    return await _on_com(_mixer_set, key, level, muted)


async def set_output(dev_id=None, cycle=False):
    return await _on_com(_set_output, dev_id, cycle)


try:
    import pycaw  # noqa: F401
    _com.submit(_init_com).result()
    HAS_VOLUME = True
except Exception:  # noqa: BLE001
    HAS_VOLUME = False


async def volume_get():
    if not HAS_VOLUME:
        return None
    return await asyncio.get_running_loop().run_in_executor(_com, _volume_get)


async def volume_set(level=None, muted=None):
    if not HAS_VOLUME:
        raise RuntimeError("pycaw não instalado")
    return await asyncio.get_running_loop().run_in_executor(_com, _volume_set, level, muted)


# --------------------------------------------------------------------------
# GPU (NVIDIA via nvidia-smi), atualizada a cada 2 s
# --------------------------------------------------------------------------
_gpu_cache = (0.0, None)


def gpu():
    global _gpu_cache
    t, val = _gpu_cache
    if time.monotonic() - t < 2:
        return val
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,fan.speed,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=NO_WINDOW,
        ).stdout.strip().splitlines()[0]
        name, util, temp, used, total, fan, power = [x.strip() for x in out.split(",")]

        def num(x):
            try:
                return float(x)
            except ValueError:  # "[N/A]"
                return None
        val = {"name": name.replace("NVIDIA ", "").replace("GeForce ", ""), "util": float(util),
               "temp": float(temp), "mem_used": float(used) * 1048576, "mem_total": float(total) * 1048576,
               "fan": num(fan), "power": num(power)}
    except Exception:  # noqa: BLE001
        val = None
    _gpu_cache = (time.monotonic(), val)
    return val


# --------------------------------------------------------------------------
# Sensores (temperatura da CPU, ventoinhas) via LibreHardwareMonitor
# Precisa do LHM aberto com Options > Remote Web Server ligado (porta 8085).
# --------------------------------------------------------------------------
LHM_URL = "http://127.0.0.1:8085/data.json"
_lhm_cache = (0.0, None)


def _num(text):
    try:
        return float(str(text).split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return None


def sensors():
    """{'cpu_temp': °C, 'fans': [{'name', 'rpm'}]} ou None se o LHM não estiver rodando."""
    global _lhm_cache
    t, val = _lhm_cache
    if time.monotonic() - t < (2 if val else 20):  # sem LHM: tenta de novo a cada 20 s
        return val
    import json
    import urllib.request
    val = None
    try:
        data = json.loads(urllib.request.urlopen(LHM_URL, timeout=0.5).read())
        temps, fans = [], []

        def walk(node, path):
            sid = str(node.get("SensorId") or "")
            typ = node.get("Type") or ""
            text = node.get("Text") or ""
            if sid:
                v = _num(node.get("Value"))
                hw = sid.split("/")[1] if sid.count("/") > 1 else ""  # "/amdcpu/0/temperature/2" -> amdcpu
                if v is not None and typ == "Temperature" and "cpu" in hw:
                    temps.append((text, v))
                elif v is not None and typ == "Fan" and v > 0:
                    fans.append({"name": text, "rpm": round(v)})
            for ch in node.get("Children") or []:
                walk(ch, path + [text])
        walk(data, [])
        pref = ("CPU Package", "Core (Tctl/Tdie)", "Core (Tctl)", "Tctl", "CPU")
        cpu = next((v for name in pref for t_, v in temps if t_ == name), None)
        if cpu is None and temps:
            cpu = max(v for _, v in temps)
        val = {"cpu_temp": cpu, "fans": fans[:6]}
    except Exception as e:  # noqa: BLE001
        log.debug("lhm: %s", e)
    _lhm_cache = (time.monotonic(), val)
    return val
