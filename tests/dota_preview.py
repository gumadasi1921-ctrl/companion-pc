"""Servidor de PRÉVIA da tela Dota (nunca o Companion instalado): ponte FALSA com os fixtures de cada fase
(tests/fixtures/dota-<fase>.json) e PNGs gerados nos tamanhos reais da ponte (ícone 32x32, retrato 128x72,
loadout 448x678, item 88x64) + uma CÓPIA do servidor em pasta temporária, em portas de teste.
  py -3 tests/dota_preview.py [porta_do_app=8800] [porta_da_ponte=8801]
Imprime a URL do app (com o token de teste). Trocar a fase da ponte: GET http://127.0.0.1:<ponte>/__set/<fase>
(offline|menu|draft|pregame|game|postgame|down = ponte fora do ar) e, no app, a tela Dota consulta de novo ao abrir.
/__gsi/<menu|draft|pregame|game|game-early|game-mid|game-late|postgame|off> manda a GSI do Dota (sem Copilot: use /__set/down) e /__od/<on|off> liga/desliga o OpenDota
falso (perfil do fixture od-*.json; imagens de herói/item vêm do CDN real da Valve). /__missing/<id,id> faz a ponte responder 404 nas imagens desses heróis (e /__missing/ limpa). Encerra com Ctrl+C
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
STATE = {"phase": "menu", "missing": set(), "state": None, "gsi": None, "od_off": False}
OD_ROUTES = {"/players/1": "od-player", "/players/1/recentMatches": "od-recent", "/players/1/heroes": "od-heroes", "/players/1/wl": "od-wl",
             "/constants/heroes": "od-constants-heroes", "/constants/items": "od-coach-items",
             "/heroes/74/itemPopularity": "od-coach-pop", "/benchmarks?hero_id=74": "od-coach-bench"}   # partida: itens/percentis do herói 74 (Invoker)


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
        full = self.path
        if path.startswith("/__set/"):
            STATE["phase"] = path.rsplit("/", 1)[1]
            return self.send(200, b"ok")
        if path.startswith("/__gsi/"):     # GSI falsa: menu|draft|pregame|game|postgame|off (reenviada a cada 5 s pelo main)
            v = path.rsplit("/", 1)[1]
            STATE["gsi"] = None if v == "off" else v
            return self.send(200, b"ok")
        if path.startswith("/__od/"):      # OpenDota falso: on|off (off = sem conexão, a tela mostra a última cópia)
            STATE["od_off"] = path.rsplit("/", 1)[1] == "off"
            return self.send(200, b"ok")
        if path.startswith("/matches/") and not STATE["od_off"]:
            return self.send(200, (FIX / "od-match.json").read_bytes(), "application/json")
        if path in OD_ROUTES or full in OD_ROUTES:
            if STATE["od_off"]:
                return self.send(503)
            return self.send(200, (FIX / f"{OD_ROUTES.get(full) or OD_ROUTES[path]}.json").read_bytes(), "application/json")
        if path.startswith("/__missing/"):
            STATE["missing"] = {x for x in path.rsplit("/", 1)[1].split(",") if x}
            return self.send(200, b"ok")
        if path == "/companion/v1/state":
            if STATE["phase"] == "down":
                return self.send(503)
            return self.send(200, (FIX / f"dota-{STATE['phase']}.json").read_bytes(), "application/json")
        parts = path.split("/")                      # ['', 'companion', 'v1', 'img', 'hero'|'item', id]
        if len(parts) == 6 and parts[3] == "img":
            if STATE["phase"] == "down":          # ponte fora do ar: as imagens vêm do CDN da Valve (OpenDota/constants)
                return self.send(503)
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
        "dota": {"bridge": f"http://127.0.0.1:{bport}/companion/v1/state", "auto_open": False, "steam_id": "1"}}), encoding="utf-8")
    # itens das partidas já "lidos" (o servidor real lê 3 por ciclo de 10 min; a prévia pula a espera): cache semeado
    ids = [116, 48, 63, 147, 139, 156, 1, 44]
    seed = {"v": 1, "account": 1, "sec": {}, "consts": {}, "calls": {"day": "", "n": 0},
            "matches": {str(9000000000 + n): {"i": [ids[(n + k) % 8] if (n + k) % 7 else None for k in range(6)], "nt": 44 if n % 2 else None, "n": 1} for n in range(1, 6)}}
    (srv / "dota-od.json").write_text(json.dumps(seed), encoding="utf-8")
    (tmp / "stop").write_text("apague este arquivo para encerrar")
    import os
    import urllib.request
    env = {**os.environ, "COMPANION_OD_BASE": f"http://127.0.0.1:{bport}", "COMPANION_GSI_PORT": str(bport + 1)}
    p = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"APP http://127.0.0.1:{port}/#t={token}", flush=True)
    print(f"PONTE http://127.0.0.1:{bport}  TMP {tmp}", flush=True)
    try:
        n = 0
        while (tmp / "stop").exists() and p.poll() is None:
            time.sleep(1)
            n += 1
            if STATE["gsi"] and n % 5 == 0:      # a GSI do Dota é reenviada (heartbeat); sem isso o servidor a dá por velha em 35 s
                try:
                    tok = json.loads((srv / "config.json").read_text(encoding="utf-8"))["dota"]["gsi_token"]
                    d = json.loads((FIX / f"gsi-{STATE['gsi']}.json").read_text(encoding="utf-8"))
                    d["auth"]["token"] = tok
                    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{bport + 1}/", json.dumps(d).encode(), method="POST"), timeout=3).close()
                except Exception:  # noqa: BLE001
                    pass
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
