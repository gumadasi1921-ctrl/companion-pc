"""Sonda SOMENTE LEITURA (não é teste automático): mostra se o Windows está com o microfone mudo e o pico de nível
(IAudioMeterInformation: um número por leitura; nenhuma amostra de áudio é lida, nada é gravado nem guardado).
Serve para decidir a etapa "Falando no mudo": rode com o mic MUDO no Windows e FALE por 6 s; depois com o mic desmutado.
Pico perto de 0 com o mic mudo = o mudo do Windows zera o medidor (não dá para detectar voz nesse caso).
Rodar: py -3 tests/mic_probe.py"""
import sys
import time
from ctypes import POINTER, cast
from pathlib import Path

from comtypes import CLSCTX_ALL

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "windows" / "server"))
import pc_info  # noqa: E402
from pycaw.pycaw import IAudioMeterInformation  # noqa: E402

mics = pc_info._mics()
if not mics:
    sys.exit("nenhum microfone encontrado")
d = mics[0]
m = cast(d._dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None), POINTER(IAudioMeterInformation))
print("mudo no Windows:", bool(d.EndpointVolume.GetMute()), "- fale agora por 6 s...")
vals = []
for _ in range(60):
    vals.append(m.GetPeakValue())
    time.sleep(0.1)
print("pico máximo: %.3f  | leituras acima de 0,05: %d de %d" % (max(vals), sum(v > 0.05 for v in vals), len(vals)))
