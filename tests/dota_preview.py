"""Servidor de PRÉVIA da tela Dota (nunca o Companion instalado): ponte FALSA com os fixtures de cada fase
(tests/fixtures/dota-<fase>.json) e PNGs gerados nos tamanhos reais da ponte (ícone 32x32, retrato 128x72,
loadout 448x678, item 88x64) + uma CÓPIA do servidor em pasta temporária, em portas de teste.
  py -3 tests/dota_preview.py [porta_do_app=8800] [porta_da_ponte=8801]
Imprime a URL do app (com o token de teste). Trocar a fase da ponte: GET http://127.0.0.1:<ponte>/__set/<fase>
(offline|menu|draft|pregame|game|postgame|down = ponte fora do ar) e, no app, a tela Dota consulta de novo ao abrir.
/__missing/<id,id> faz a ponte responder 404 nas imagens desses heróis (e /__missing/ limpa). Encerra com Ctrl+C
ou apagando o arquivo "stop" da pasta temporária impressa."""
import http.server
import io
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent / "windows" / "server"
FIX = ROOT / "fixtures"
PHASES = ("offline", "menu", "draft", "pregame", "game", "postgame")
SIZES = {"icon": (32, 32), "portrait": (128, 72), "loadout": (448, 678), "item": (88, 64)}
STATE = {"phase": "menu", "missing": set(), "state": None}


def png(kind, ident):
    w, h = SIZES[kind]
    hue = (int(ident) * 47) % 360
    im = Image.new("RGB", (w, h), (30, 30, 30))
    d = ImageDraw.Draw(im)
    color = Image.new("HSV", (1, 1), (hue * 255 // 360, 200, 220)).convert("RGB").getpixel((0, 0))
    d.rectangle([0, 0, w - 1, h - 1], fill=color)
    d.line([0, 0, w, h], fill=(255, 255, 255), width=max(1, w // 40))      # diagonais: um esticão ficaria visível
    d.line([0, h, w, 0], fill=(0, 0, 0), width=max(1, w // 40))
    d.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path, _, query = self.path.partition("?")
        if path.startswith("/__set/"):
            STATE["phase"] = path.rsplit("/", 1)[1]
            return self.send(200, b"ok")
        if path.startswith("/__missing/"):
            STATE["missing"] = {x for x in path.rsplit("/", 1)[1].split(",") if x}
            return self.send(200, b"ok")
        if path == "/companion/v1/state":
            if STATE["phase"] == "down":
                return self.send(503)
            return self.send(200, (FIX / f"dota-{STATE['phase']}.json").read_bytes(), "application/json")
        parts = path.split("/")                      # ['', 'companion', 'v1', 'img', 'hero'|'item', id]
        if len(parts) == 6 and parts[3] == "img":
            if parts[5] in STATE["missing"]:
                return self.send(404)
            kind = "item" if parts[4] == "item" else dict(p.split("=") for p in query.split("&") if "=" in p).get("kind", "portrait")
            return self.send(200, png(kind, parts[5]), "image/png")
        self.send(404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
    bport = int(sys.argv[2]) if len(sys.argv) > 2 else 8801
    token, panel = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", bport), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="companion-preview-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json",
                                                            "pomodoro*.json", "games-history.json", "dota-img"))
    (srv / "config.json").write_text(json.dumps({
        "token": token, "panel_token": panel,
        "web": {"host": "127.0.0.1", "port": port, "adb_reverse": False, "adb_path": ""},
        "dota": {"bridge": f"http://127.0.0.1:{bport}/companion/v1/state", "auto_open": False}}), encoding="utf-8")
    (tmp / "stop").write_text("apague este arquivo para encerrar")
    p = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"APP http://127.0.0.1:{port}/#t={token}", flush=True)
    print(f"PONTE http://127.0.0.1:{bport}  TMP {tmp}", flush=True)
    try:
        while (tmp / "stop").exists() and p.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        p.kill()
        p.wait()
        fake.shutdown()
        fake.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
