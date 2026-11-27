"""Lê a bateria REAL que o Windows informa agora (só leitura). Rodar com o fone/mouse Bluetooth conectado: py -3 tests/test_battery_live.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import battery  # noqa: E402

b = battery.Battery()
b.poll()
print(f"{len(b.items)} aparelho(s) com bateria no Windows")
for i in b.items:
    print(f"  {i['name']}: {i['pct']}%  (id {i['id']})")
if not b.items:
    print("nenhum: ligue e conecte o aparelho (e o adaptador Bluetooth) e rode de novo")
