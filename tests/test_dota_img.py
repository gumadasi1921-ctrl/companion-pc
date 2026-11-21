"""Proxy de imagens do Dota (windows/server/dota_img.py): unidade (ponte FALSA em outra porta) e ao vivo numa CÓPIA
do servidor (pasta temporária, porta de teste). Nunca mexe no Companion instalado.
Rodar: py -3 tests/test_dota_img.py"""
import asyncio
import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent / "windows" / "server"
FIX = ROOT / "fixtures"
PORT, BRIDGE_PORT = 8798, 8799
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
PNG = b"\x89PNG\r\n\x1a\n"
fails = []
sys.path.insert(0, str(SRC))
import dota_img  # noqa: E402


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


# ------------------------------------------------------------------ ponte falsa
STATE = {"hits": [], "missing": set(), "mode": "ok"}   # mode: ok | text | big | down500


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        STATE["hits"].append(self.path)
        if self.path == "/companion/v1/state":
            body = (FIX / "dota-menu.json").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        base = self.path.split("?")[0]
        if base in STATE["missing"]:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if STATE["mode"] == "down500":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body, ctype = PNG + self.path.encode() + b"." * 700, "image/png"
        if STATE["mode"] == "text":
            body, ctype = b"<html>nao sou png</html>", "text/html"
        if STATE["mode"] == "big":
            body = PNG + b"x" * (dota_img.MAX_IMAGE_BYTES + 10)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fake():
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    return fake


def new_proxy(cache, **kw):
    bridge = kw.pop("bridge", f"http://127.0.0.1:{BRIDGE_PORT}/companion/v1/state")
    return dota_img.ImageProxy(cache, lambda: TOKEN, lambda: bridge, **kw)


def hits():
    return [h for h in STATE["hits"] if "/img/" in h]


# ------------------------------------------------------------------ unidade
def unit(tmp):
    cache = tmp / "cache"
    p = new_proxy(cache)
    k = p.key()
    get = lambda path: p.handle(path)  # noqa: E731
    check("chave derivada do token, 24 hex, sem o token", len(k) == 24 and TOKEN not in k and k == dota_img.image_key(TOKEN))
    check("chave muda com o token", dota_img.image_key(TOKEN) != dota_img.image_key(TOKEN + "x"))

    # id válido: 200 + content-type + cabeçalhos; 1ª vez vai à ponte, 2ª sai do disco
    st, ct, body, h = get(f"/dota/img/hero/44?kind=portrait&k={k}")
    check("hero válido = 200 image/png", st == 200 and ct == "image/png" and body.startswith(PNG))
    check("Cache-Control max-age=604800", h.get("Cache-Control") == "max-age=604800")
    check("1ª vez busca na ponte (kind repassado)", hits() == ["/companion/v1/img/hero/44?kind=portrait"], str(hits()))
    check("arquivo guardado no cache", (cache / "hero-44-portrait.png").is_file())
    n = len(hits())
    st2, _, body2, _ = get(f"/dota/img/hero/44?kind=portrait&k={k}")
    check("cache hit: 200, mesmo corpo, sem nova busca", st2 == 200 and body2 == body and len(hits()) == n)
    # sem kind = portrait (mesmo padrão da ponte): mesmo arquivo
    st3, _, _, _ = get(f"/dota/img/hero/44?k={k}")
    check("sem kind = portrait (cache hit)", st3 == 200 and len(hits()) == n)
    for kind in ("icon", "loadout"):
        st, _, _, _ = get(f"/dota/img/hero/44?kind={kind}&k={k}")
        check(f"kind={kind} = arquivo próprio", st == 200 and (cache / f"hero-44-{kind}.png").is_file())
    st, ct, _, _ = get(f"/dota/img/item/116?k={k}")
    check("item válido = 200 image/png", st == 200 and ct == "image/png" and (cache / "item-116.png").is_file())
    check("item busca /img/item/116", "/companion/v1/img/item/116" in hits())
    st, _, _, _ = get(f"/dota/img/item/007?kind=bogus&k={k}")
    check("item ignora kind; id 007 vira 7", st == 200 and (cache / "item-7.png").is_file())

    # recusas: sem chave, chave errada, repetida
    before = len(hits())
    for name, path in [("sem chave", "/dota/img/hero/44?kind=icon"), ("chave errada", f"/dota/img/hero/44?kind=icon&k={'0' * 24}"),
                       ("chave vazia", "/dota/img/hero/44?k="), ("chave com o token", f"/dota/img/hero/44?k={TOKEN}"),
                       ("chave repetida", f"/dota/img/hero/44?k={k}&k={k}")]:
        check(f"{name}: 403", get(path)[0] == 403)
    check("recusado não chega à ponte nem ao disco", len(hits()) == before)

    # ids e kinds inválidos
    bad = {"letras": "abc", "zero": "0", "negativo": "-1", "grande": "99999999999", "decimal": "1.5", "sinal": "+5",
           "espaço": "7%204", "traversal": "..", "traversal codificado": "%2e%2e", "barra codificada": "..%2f..%2fconfig.json",
           "unicode": "%EF%BC%94%EF%BC%94", "vazio": ""}
    for name, ident in bad.items():
        path = f"/dota/img/hero/{ident}?kind=icon&k={k}"
        check(f"id inválido ({name}) = 400", get(path)[0] == 400, str(get(path)[0]))
    check("id inválido em item = 400", get(f"/dota/img/item/x1?k={k}")[0] == 400)
    for name, q in {"fora da lista": "kind=ICON", "caminho": "kind=../icon", "extensão": "kind=icon.png", "vazio": "kind=",
                    "repetido": "kind=icon&kind=loadout"}.items():
        check(f"kind {name} = 400", get(f"/dota/img/hero/44?{q}&k={k}")[0] == 400)
    for name, path in {"segmento a mais": f"/dota/img/hero/44/extra?k={k}", "traversal no caminho": f"/dota/img/hero/../../config.json?k={k}",
                       "outro tipo": f"/dota/img/ability/44?k={k}", "sem id": f"/dota/img/hero?k={k}", "URL livre": f"/dota/img/hero/http://x.y/a?k={k}"}.items():
        check(f"caminho {name} = 404", get(path)[0] == 404, str(get(path)[0]))
    check("nada disso foi ao disco/ponte além dos válidos", sorted(p.name for p in cache.iterdir()) ==
          sorted(["hero-44-portrait.png", "hero-44-icon.png", "hero-44-loadout.png", "item-116.png", "item-7.png"]))

    # 404 da ponte = 404 aqui e não fica no cache; depois que a ponte tiver a imagem, passa a servir
    STATE["missing"].add("/companion/v1/img/hero/55")
    st, _, _, h = get(f"/dota/img/hero/55?kind=icon&k={k}")
    check("404 da ponte = 404", st == 404 and h["Cache-Control"] == "no-store")
    check("404 da ponte não fica no cache", not (cache / "hero-55-icon.png").exists())
    get(f"/dota/img/hero/55?kind=icon&k={k}")
    check("e pergunta de novo a cada vez (pode ter sido extraída)", sum(x.startswith("/companion/v1/img/hero/55") for x in hits()) == 2)
    STATE["missing"].clear()
    st, _, _, _ = get(f"/dota/img/hero/55?kind=icon&k={k}")
    check("extraída depois: 200 e guardada", st == 200 and (cache / "hero-55-icon.png").is_file())

    # resposta inválida da ponte não é guardada
    STATE["mode"] = "text"
    st, _, _, _ = get(f"/dota/img/hero/66?kind=icon&k={k}")
    check("não-PNG da ponte = 502, não guarda", st == 502 and not (cache / "hero-66-icon.png").exists())
    STATE["mode"] = "big"
    st, _, _, _ = get(f"/dota/img/hero/66?kind=icon&k={k}")
    check("PNG grande demais = 502, não guarda", st == 502 and not (cache / "hero-66-icon.png").exists())
    STATE["mode"] = "down500"
    st, _, _, _ = get(f"/dota/img/hero/66?kind=icon&k={k}")
    check("erro 500 da ponte = 502, não guarda", st == 502 and not (cache / "hero-66-icon.png").exists())
    STATE["mode"] = "ok"

    # arquivo estragado no cache é descartado e buscado de novo
    (cache / "item-116.png").write_bytes(b"lixo")
    st, _, body, _ = get(f"/dota/img/item/116?k={k}")
    check("cache estragado: busca de novo", st == 200 and body.startswith(PNG) and (cache / "item-116.png").read_bytes().startswith(PNG))

    # só a ponte local
    called = []
    far = new_proxy(tmp / "cache-far", bridge="http://exemplo.com:32123/companion/v1/state", opener=lambda *a, **kw: called.append(1))
    check("ponte fora do PC: 502 sem tentar", far.handle(f"/dota/img/hero/1?k={far.key()}")[0] == 502 and not called)

    # ponte fora do ar: 502 e por 10 s nem tenta de novo
    now = [100.0]
    attempts = []

    def refuse(*a, **kw):
        attempts.append(1)
        raise urllib.error.URLError("recusado")

    down = new_proxy(tmp / "cache-down", opener=refuse, clock=lambda: now[0])
    kd = down.key()
    check("ponte fora do ar = 502", down.handle(f"/dota/img/hero/1?k={kd}")[0] == 502 and len(attempts) == 1)
    now[0] += 5
    down.handle(f"/dota/img/hero/2?k={kd}")
    check("nos 10 s seguintes não tenta de novo", len(attempts) == 1)
    now[0] += 6
    down.handle(f"/dota/img/hero/2?k={kd}")
    check("passados os 10 s tenta de novo", len(attempts) == 2)

    # limite do cache: apaga as mais antigas
    check("limite padrão = 50 MB", dota_img.CACHE_MAX_BYTES == 50 * 1024 * 1024)
    small = tmp / "cache-small"
    sp = new_proxy(small, cache_max=1600)
    ks = sp.key()
    for i in (1, 2):
        sp.handle(f"/dota/img/hero/{i}?kind=icon&k={ks}")
    size = (small / "hero-1-icon.png").stat().st_size
    check("imagem de teste ~1 KB", 700 < size < 1200, str(size))
    base = time.time() - 1000
    os.utime(small / "hero-1-icon.png", (base, base))
    os.utime(small / "hero-2-icon.png", (base + 100, base + 100))
    sp.handle(f"/dota/img/hero/3?kind=icon&k={ks}")
    left = sorted(f.name for f in small.iterdir())
    check("passou do limite: a mais antiga saiu", left == ["hero-2-icon.png", "hero-3-icon.png"], str(left))
    check("cache dentro do limite", sum(f.stat().st_size for f in small.iterdir()) <= 1600)
    # usar uma imagem a renova (não é a próxima a sair)
    os.utime(small / "hero-2-icon.png", (base, base))
    os.utime(small / "hero-3-icon.png", (base + 100, base + 100))
    sp.handle(f"/dota/img/hero/2?kind=icon&k={ks}")      # hit: renova a 2
    sp.handle(f"/dota/img/hero/4?kind=icon&k={ks}")
    left = sorted(f.name for f in small.iterdir())
    check("imagem usada recentemente fica", left == ["hero-2-icon.png", "hero-4-icon.png"], str(left))
    (small / "config.json").write_text("{}")
    (small / "notas.txt").write_text("x")
    sp.prune()
    check("a poda só mexe nos arquivos do cache de imagens", (small / "config.json").exists() and (small / "notas.txt").exists())


# ------------------------------------------------------------------ ao vivo
def http_get(path, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=headers or {}), timeout=8) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


async def live():
    ws = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await ws.send(json.dumps({"t": "hello", "token": TOKEN}))
    key = None
    for _ in range(40):
        m = json.loads(await asyncio.wait_for(ws.recv(), 8))
        if m.get("t") == "dota":
            key = m.get("img_key")
            check("mensagem dota traz a chave de imagens", key == dota_img.image_key(TOKEN))
            check("o estado da ponte não leva a chave", "img_key" not in m["d"])
            break
    else:
        check("mensagem dota recebida", False)
    await ws.send(json.dumps({"t": "req", "id": 7, "a": "dota.get", "p": {}}))
    for _ in range(40):
        m = json.loads(await asyncio.wait_for(ws.recv(), 8))
        if m.get("t") == "res" and m.get("id") == 7:
            check("dota.get também traz a chave", m["ok"] and m["d"].get("img_key") == key)
            break
    await ws.close()
    return key


def live_http(key):
    n = len(hits())
    st, h, body = http_get(f"/dota/img/hero/44?kind=portrait&k={key}")
    check("HTTP: hero válido = 200 image/png", st == 200 and h.get("Content-Type") == "image/png" and body.startswith(PNG))
    check("HTTP: Cache-Control max-age=604800", h.get("Cache-Control") == "max-age=604800")
    check("HTTP: Content-Length confere", int(h.get("Content-Length", -1)) == len(body))
    st, h, body = http_get(f"/dota/img/item/116?k={key}")
    check("HTTP: item válido = 200 image/png", st == 200 and h.get("Content-Type") == "image/png")
    http_get(f"/dota/img/hero/44?kind=portrait&k={key}")
    check("HTTP: 2ª vez sai do disco (a ponte viu 2 pedidos)", len(hits()) - n == 2, str(len(hits()) - n))
    for name, path, want in [("sem chave", "/dota/img/hero/44?kind=icon", 403), ("chave errada", f"/dota/img/hero/44?k={'a' * 24}", 403),
                             ("id inválido", f"/dota/img/hero/abc?k={key}", 400), ("kind fora da lista", f"/dota/img/hero/44?kind=x&k={key}", 400),
                             ("../", f"/dota/img/hero/../../config.json?k={key}", 404), ("../ codificado", f"/dota/img/hero/..%2f..%2fconfig.json?k={key}", 400),
                             ("tipo errado", f"/dota/img/ability/1?k={key}", 404)]:
        check(f"HTTP: {name} = {want}", http_get(path)[0] == want, str(http_get(path)[0]))
    st, _, body = http_get("/")
    check("HTTP: o app (/) continua servido", st == 200 and b"<html" in body[:2000].lower())
    st, _, body = http_get("/config.json")
    check("HTTP: config.json do servidor não é servido", st == 404 and TOKEN.encode() not in body)
    STATE["missing"].add("/companion/v1/img/hero/77")
    check("HTTP: 404 da ponte = 404", http_get(f"/dota/img/hero/77?kind=icon&k={key}")[0] == 404)
    STATE["missing"].clear()


def main():
    fake = start_fake()
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    try:
        unit(tmp)
        STATE["hits"].clear()
        srv = tmp / "server"
        shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json",
                                                                "pomodoro*.json", "games-history.json", "dota-img"))
        (srv / "config.json").write_text(json.dumps({
            "token": TOKEN, "panel_token": PANEL,
            "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""},
            "dota": {"bridge": f"http://127.0.0.1:{BRIDGE_PORT}/companion/v1/state", "auto_open": True}}), encoding="utf-8")
        p = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(40):
                try:
                    socket.create_connection(("127.0.0.1", PORT), 0.5).close()
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                print("servidor de teste não subiu")
                return 2
            key = asyncio.run(live())
            live_http(key or "")
            check("cache do servidor de teste fica ao lado dele (dota-img)", (srv / "dota-img" / "hero-44-portrait.png").is_file())
            log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
            check("log sem imagem, id nem nome de arquivo", all(x not in log for x in ("/dota/img", "dota-img", ".png", "hero-44", "item-116")))
            check("log sem token nem chave de imagens", TOKEN not in log and PANEL not in log and (not key or key not in log))
            check("log sem erro", "Traceback" not in log and " ERROR " not in log)
        finally:
            p.kill()
            p.wait()
        return 1 if fails else 0
    finally:
        fake.shutdown()
        fake.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
