"""Coisas que rodam em segundo plano para o celular: ping da internet,
notificações do Windows e FPS do jogo em foco (PresentMon)."""
import asyncio
import collections
import glob
import logging
import os
import shutil
import time

log = logging.getLogger("companion")
NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------
# Ping: tempo para abrir uma conexão TCP (não precisa de admin, ao contrário do ICMP)
# --------------------------------------------------------------------------
class Ping:
    TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53), ("208.67.222.222", 443))

    def __init__(self):
        self.ms, self.ok, self.fails = None, None, 0

    def state(self):
        return {"ms": self.ms, "ok": self.ok} if self.ok is not None else None

    async def _once(self):
        for host, port in self.TARGETS:
            t = time.perf_counter()
            try:
                _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 2)
            except (OSError, asyncio.TimeoutError):
                continue
            ms = (time.perf_counter() - t) * 1000
            w.close()
            try:
                await w.wait_closed()
            except OSError:
                pass
            return round(ms)
        return None

    async def loop(self, active):
        while True:
            if active():
                ms = await self._once()
                if ms is None:
                    self.fails += 1
                    if self.fails >= 2:  # uma falha solta não conta como queda
                        self.ms, self.ok = None, False
                else:
                    self.fails, self.ms, self.ok = 0, ms, True
            await asyncio.sleep(2)


# --------------------------------------------------------------------------
# Notificações do Windows (Central de notificações) -> celular
# --------------------------------------------------------------------------
class WinNotifications:
    def __init__(self):
        self.available = None
        self.seen: set[int] = set()
        self.primed = False

    async def loop(self, active, push, ignore):
        try:
            from winrt.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
            from winrt.windows.ui.notifications.management import (UserNotificationListener,
                                                                   UserNotificationListenerAccessStatus)
        except Exception as e:  # noqa: BLE001
            log.info("notificações do Windows indisponíveis (pacote winrt): %s", e)
            self.available = False
            return
        lst = UserNotificationListener.current
        allowed = UserNotificationListenerAccessStatus.ALLOWED
        try:
            st = lst.get_access_status()
            if st != allowed:
                st = await lst.request_access_async()
        except Exception as e:  # noqa: BLE001
            log.info("notificações do Windows: sem acesso (%s)", e)
            st = None
        if st != allowed:
            log.info("notificações do Windows bloqueadas: Configurações > Privacidade > Notificações")
            self.available = False
            return
        self.available = True
        log.info("notificações do Windows: ativas")
        while True:
            try:
                if not active():
                    self.primed = False
                    await asyncio.sleep(3)
                    continue
                cur = set()
                for n in await lst.get_notifications_async(NotificationKinds.TOAST):
                    cur.add(n.id)
                    if not self.primed or n.id in self.seen:
                        continue  # as que já estavam lá quando o celular conectou não são enviadas
                    try:
                        app = n.app_info.display_info.display_name
                    except Exception:  # noqa: BLE001
                        app = "Windows"
                    if app.lower() in ignore():
                        continue
                    texts = []
                    try:
                        b = n.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
                        texts = [t.text for t in b.get_text_elements()] if b else []
                    except Exception:  # noqa: BLE001
                        pass
                    await push({"app": app, "title": (texts[0] if texts else app)[:120],
                                "body": " ".join(texts[1:])[:300]})
                self.seen, self.primed = cur, True
            except Exception:  # noqa: BLE001
                log.exception("notificações do Windows")
                await asyncio.sleep(10)
            await asyncio.sleep(2)


# --------------------------------------------------------------------------
# FPS do jogo em foco, com o PresentMon Console da Intel
# --------------------------------------------------------------------------
def find_presentmon(custom=""):
    if custom:
        c = os.path.expandvars(custom)
        if os.path.isfile(c):
            return c
    hits = sorted(glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Intel.PresentMon.Console*\PresentMon-*.exe")))
    return hits[-1] if hits else shutil.which("presentmon")


def kill_orphans():
    """PresentMon que ficou aberto de uma execução anterior do servidor."""
    import psutil
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower().startswith("presentmon") and Fps.SESSION in (p.info["cmdline"] or []):
                p.kill()
        except psutil.Error:
            pass


class Fps:
    SESSION = "PCCompanion"

    def __init__(self, custom_path=""):
        self.custom = custom_path
        self.proc = None
        self.err = None            # "missing" | "perm" | None
        self.retry_at = 0.0
        self.frames: dict[int, collections.deque] = {}

    def state(self, pid):
        if not pid:
            return None
        dq = self.frames.get(pid)
        if dq and time.monotonic() - dq[-1][0] < 3:
            ms = [m for _t, m in dq]
            last, acc = [], 0.0
            for m in reversed(ms):           # quadros do último segundo
                last.append(m)
                acc += m
                if acc >= 1000:
                    break
            fps = round(1000 * len(last) / acc) if acc else 0
            worst = sorted(ms)[int(len(ms) * 0.99) - 1] if len(ms) >= 100 else None
            return {"fps": fps, "low": round(1000 / worst) if worst else None}
        if self.err:
            return {"error": self.err}
        return None

    async def loop(self, want_pid):
        while True:
            try:
                pid = want_pid()
                if pid and not self.proc and time.monotonic() >= self.retry_at:
                    await self._start()
                elif not pid and self.proc:
                    self.stop()
            except Exception:  # noqa: BLE001
                log.exception("fps")
            await asyncio.sleep(2)

    async def _start(self):
        exe = find_presentmon(self.custom)
        if not exe:
            self.err, self.retry_at = "missing", time.monotonic() + 300
            return
        self.proc = await asyncio.create_subprocess_exec(
            exe, "--output_stdout", "--no_console_stats", "--stop_existing_session",
            "--session_name", self.SESSION, "--no_track_gpu", "--no_track_input",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW)
        asyncio.create_task(self._read(self.proc))

    async def _read(self, proc):
        cols = None
        try:
            async for raw in proc.stdout:
                parts = raw.decode("utf-8", "ignore").strip().split(",")
                if cols is None:
                    if "ProcessID" in parts:
                        cols = (parts.index("ProcessID"),
                                parts.index("FrameTime") if "FrameTime" in parts else parts.index("MsBetweenPresents"))
                        self.err = None
                    continue
                try:
                    pid, ms = int(parts[cols[0]]), float(parts[cols[1]])
                except (ValueError, IndexError):
                    continue
                if 0 < ms < 1000:
                    dq = self.frames.get(pid)
                    if dq is None:
                        dq = self.frames[pid] = collections.deque(maxlen=1500)
                    dq.append((time.monotonic(), ms))
        except Exception:  # noqa: BLE001
            log.exception("fps: leitura")
        err = ""
        try:
            err = (await proc.stderr.read()).decode("utf-8", "ignore").lower()
        except Exception:  # noqa: BLE001
            pass
        if cols is None and "access denied" in err:
            if self.err != "perm":
                log.info("FPS: o PresentMon precisa do grupo 'Usuários de log de desempenho'")
            self.err, self.retry_at = "perm", time.monotonic() + 120
        if self.proc is proc:
            self.proc = None
        self.frames.clear()

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.returncode is None:
            try:
                p.terminate()
            except ProcessLookupError:
                pass
        self.frames.clear()
