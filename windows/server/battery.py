"""Bateria de fones e mouses Bluetooth, lida do que o PRÓPRIO WINDOWS informa (nada é estimado, nenhuma marca é consultada).
Também mouse com receptor 2,4 GHz que responde ao pedido de bateria testado (ver "mouse com receptor" abaixo): só leitura, a cada 5 min.
Fonte: propriedade de bateria do dispositivo (DEVPKEY {104EA319-6EE2-4701-BD47-8DDBF425BBE5} pid 2, um byte 0..100) nos dispositivos
Bluetooth presentes (BTHENUM = clássico/HFP, BTHLE e BTHLEDEVICE = BLE com Battery Service), via CfgMgr32 + ctypes (sem biblioteca nova).
Sem a propriedade = o aparelho NÃO aparece. Mouse com receptor que não responde ao pedido do receptor: fica fora.
O celular recebe só {id opaco, nome que o Windows dá, pct}; nunca endereço Bluetooth nem caminho. O nome nunca vai ao log."""
import hashlib
import re
import sys
import time
import uuid

LOW_PCT = 15       # abaixo disso = aviso
REARM_PCT = 20     # só avisa de novo depois de passar daqui (ou do aparelho sumir)
MAX_ITEMS = 8

KEY_BATTERY = ("{104EA319-6EE2-4701-BD47-8DDBF425BBE5}", 2)
KEY_CONNECTED = ("{83DA6326-97A6-4088-9453-A1923F573B29}", 15)    # DEVPKEY_Device_IsConnected
KEY_NAME = ("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 14)         # DEVPKEY_Device_FriendlyName
KEY_DESC = ("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 2)          # DEVPKEY_Device_DeviceDesc
ENUMERATORS = ("BTHENUM", "BTHLE", "BTHLEDEVICE")
_ADDR = re.compile(r"DEV_([0-9A-Fa-f]{12})")


def valid_pct(v):
    """Só inteiro 0..100 (bool não vale). Qualquer outra coisa é descartada, nunca arredondada nem adivinhada."""
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100 else None


def addr_of(instance_id):
    m = _ADDR.search(instance_id or "")
    return m.group(1).upper() if m else None


def opaque_id(key):
    return hashlib.sha256(("companion-battery|" + str(key)).encode()).hexdigest()[:12]


def clean_name(name):
    n = " ".join(str(name or "").split())[:60]
    return n or "Dispositivo Bluetooth"


# ---- leitura no Windows (CfgMgr32) --------------------------------------------------------------------------------------
def read_windows():
    """[{key, name, pct}] dos dispositivos Bluetooth presentes que informam bateria. Falha = []."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    class PKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]

    cm = ctypes.WinDLL("cfgmgr32")

    def pkey(spec):
        g = GUID.from_buffer_copy(uuid.UUID(spec[0]).bytes_le)
        return PKEY(g, spec[1])

    def ids(enum):
        n = wintypes.ULONG(0)
        flags = 0x1 | 0x100    # por enumerador + só presentes
        if cm.CM_Get_Device_ID_List_SizeW(ctypes.byref(n), enum, flags) != 0 or n.value < 2:
            return []
        buf = ctypes.create_unicode_buffer(n.value)
        if cm.CM_Get_Device_ID_ListW(enum, buf, n.value, flags) != 0:
            return []
        return [s for s in buf[:].split("\0") if s]

    def prop(devinst, spec):
        k, typ, size = pkey(spec), wintypes.ULONG(0), wintypes.ULONG(0)
        cm.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(k), ctypes.byref(typ), None, ctypes.byref(size), 0)
        if not 0 < size.value <= 512:
            return None
        buf = (ctypes.c_ubyte * size.value)()
        if cm.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(k), ctypes.byref(typ), buf, ctypes.byref(size), 0) != 0:
            return None
        raw = bytes(buf)[:size.value]
        if typ.value == 0x12:       # DEVPROP_TYPE_STRING
            return raw.decode("utf-16-le", "ignore").rstrip("\0")
        if typ.value == 0x11:       # BOOLEAN (VARIANT_BOOL / byte)
            return any(raw)
        return raw[0] if raw else None   # BYTE (bateria)

    names, hits = {}, []
    for enum in ENUMERATORS:
        for iid in ids(enum):
            dn = wintypes.DWORD(0)
            if cm.CM_Locate_DevNodeW(ctypes.byref(dn), iid, 0) != 0:
                continue
            addr = addr_of(iid)
            nm = prop(dn.value, KEY_NAME) or prop(dn.value, KEY_DESC)
            if addr and nm and "\\" in iid and iid.split("\\")[1].upper().startswith("DEV_"):
                names.setdefault(addr, nm)          # nó "raiz" do aparelho: é o nome que o Windows mostra
            pct = prop(dn.value, KEY_BATTERY)
            if pct is None:
                continue
            if prop(dn.value, KEY_CONNECTED) is False:
                continue
            hits.append((addr or iid, nm, pct))
    out, seen = [], set()
    for key, nm, pct in hits:
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "name": names.get(key) or nm, "pct": pct})
    return out


# ---- mouse com receptor 2,4 GHz (pedido de bateria SÓ-LEITURA pelo canal do fabricante) -------------------------------------
# Nenhum VID/PID/serial no código: a interface é achada pela página/uso (0xFF01 / 0x10, 65 bytes) e só recebe o pedido se o MESMO
# dispositivo também tem uma coleção de mouse (página 0x01, uso 0x02). O pedido é UM output report fixo (00 55 30 A5 0B 2E 01 01 + zeros);
# nenhum outro comando é enviado. Resposta só vale se ecoar o pedido (AA 30 A5 0B) ou vier no formato AA FA .. D0.
HID_EVERY_S = 300        # a leitura do receptor roda no máximo a cada 5 min (o Bluetooth segue a cada poll)
HID_PAGE, HID_USAGE, HID_LEN = 0xFF01, 0x10, 65
HID_REQUEST = bytes([0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01]) + bytes(HID_LEN - 8)
MAX_TARGETS = 2
RECEIVER_NAME = "Mouse (receptor)"
_VP = re.compile(r"vid_[0-9a-f]{4}&pid_[0-9a-f]{4}", re.I)


def parse_receiver(report):
    """(pct 1..100, carregando) ou None. Aceita o eco do pedido (byte 8 = bateria, 9 = carregando) ou AA FA .. D0 (byte 9 / 10).
    O report ID 0x00 inicial (Windows) é tirado. Zero, fora de 1..100, curto ou eco errado = None: nunca se estima."""
    b = bytes(report or b"")
    for d in ((b[1:], b) if b[:1] == b"\x00" else (b,)):
        if len(d) >= 11 and d[:4] == bytes([0xAA, 0x30, 0xA5, 0x0B]):
            pct, chg = d[8], d[9] != 0
        elif len(d) >= 11 and d[0] == 0xAA and d[1] == 0xFA and d[8] == 0xD0:
            pct, chg = d[9], d[10] != 0
        else:
            continue
        return (pct, chg) if 1 <= pct <= 100 else None
    return None


def pick_targets(ifaces):
    """ifaces = [{path, group, page, usage, inlen, outlen}] -> caminhos que podem receber o pedido: interface 0xFF01/0x10 de 65 bytes
    cujo dispositivo (group = par vid/pid lido em tempo de execução) também tem uma coleção de mouse. No máximo MAX_TARGETS."""
    mice = {i.get("group") for i in ifaces if i.get("page") == 0x01 and i.get("usage") == 0x02}
    out = []
    for i in ifaces:
        if (i.get("page") == HID_PAGE and i.get("usage") == HID_USAGE and i.get("inlen") == HID_LEN and i.get("outlen") == HID_LEN
                and i.get("group") in mice and i.get("path") not in out):
            out.append(i["path"])
    return out[:MAX_TARGETS]


def read_receiver_hid():
    """[{key, name, pct, charging, kind}] dos mouses com receptor que responderam agora. Qualquer falha = [] (silencioso)."""
    if sys.platform != "win32":
        return []
    try:
        return _read_receiver()
    except Exception:  # noqa: BLE001
        return []


def _read_receiver():
    import ctypes
    import time
    from ctypes import wintypes as w

    hid, sp, k32 = ctypes.WinDLL("hid"), ctypes.WinDLL("setupapi"), ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = w.HANDLE
    k32.CreateEventW.restype = w.HANDLE
    sp.SetupDiGetClassDevsW.restype = w.HANDLE
    BAD = (None, -1, w.HANDLE(-1).value)

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    class DID(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("g", GUID), ("f", w.DWORD), ("r", ctypes.c_void_p)]

    class CAPS(ctypes.Structure):
        _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort), ("InLen", ctypes.c_ushort), ("OutLen", ctypes.c_ushort),
                    ("FeatLen", ctypes.c_ushort), ("res", ctypes.c_ushort * 17), ("n", ctypes.c_ushort * 10)]

    class OVL(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]

    hg = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(hg))
    hdev = sp.SetupDiGetClassDevsW(ctypes.byref(hg), None, None, 0x12)
    if hdev in BAD:
        return []
    paths, i = [], 0
    try:
        while i < 512:
            d = DID()
            d.cb = ctypes.sizeof(DID)
            if not sp.SetupDiEnumDeviceInterfaces(w.HANDLE(hdev), None, ctypes.byref(hg), i, ctypes.byref(d)):
                break
            i += 1
            n = w.DWORD(0)
            sp.SetupDiGetDeviceInterfaceDetailW(w.HANDLE(hdev), ctypes.byref(d), None, 0, ctypes.byref(n), None)
            if not 8 < n.value < 2048:
                continue
            buf = ctypes.create_string_buffer(n.value)
            ctypes.c_ulong.from_address(ctypes.addressof(buf)).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if sp.SetupDiGetDeviceInterfaceDetailW(w.HANDLE(hdev), ctypes.byref(d), buf, n, None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        sp.SetupDiDestroyDeviceInfoList(w.HANDLE(hdev))

    ifaces = []
    for p in paths:
        m = _VP.search(p)
        if not m:
            continue
        hd = k32.CreateFileW(p, 0, 3, None, 3, 0, None)          # acesso 0: só metadados
        if hd in BAD:
            continue
        try:
            pp = ctypes.c_void_p()
            if hid.HidD_GetPreparsedData(w.HANDLE(hd), ctypes.byref(pp)):
                c = CAPS()
                hid.HidP_GetCaps(pp, ctypes.byref(c))
                hid.HidD_FreePreparsedData(pp)
                ifaces.append({"path": p, "group": m.group(0).lower(), "page": c.UsagePage, "usage": c.Usage, "inlen": c.InLen, "outlen": c.OutLen})
        finally:
            k32.CloseHandle(w.HANDLE(hd))

    out = []
    for idx, p in enumerate(pick_targets(ifaces)):
        hd = k32.CreateFileW(p, 0xC0000000, 3, None, 3, 0x40000000, None)
        if hd in BAD:
            continue
        ev = sev = None
        try:
            ev, sev = k32.CreateEventW(None, True, False, None), k32.CreateEventW(None, True, False, None)
            rov, rb = OVL(), (ctypes.c_ubyte * HID_LEN)()
            rov.hEvent = ev
            k32.ReadFile(w.HANDLE(hd), rb, HID_LEN, None, ctypes.byref(rov))     # leitura pendente ANTES do envio
            sov = OVL()
            sov.hEvent = sev
            req = (ctypes.c_ubyte * HID_LEN).from_buffer_copy(HID_REQUEST)
            if not k32.WriteFile(w.HANDLE(hd), req, HID_LEN, None, ctypes.byref(sov)) and ctypes.get_last_error() != 997:
                continue
            k32.WaitForSingleObject(w.HANDLE(sev), 1000)
            t0, got, res = time.time(), 0, None
            while got < 3 and time.time() - t0 < 1.5 and res is None:
                if k32.WaitForSingleObject(w.HANDLE(ev), max(1, int((1.5 - (time.time() - t0)) * 1000))) != 0:
                    break
                nb = w.DWORD(0)
                k32.GetOverlappedResult(w.HANDLE(hd), ctypes.byref(rov), ctypes.byref(nb), False)
                got += 1
                res = parse_receiver(bytes(rb)[:nb.value])
                if res is None and got < 3:
                    rov = OVL()
                    rov.hEvent = ev
                    k32.ResetEvent(w.HANDLE(ev))
                    k32.ReadFile(w.HANDLE(hd), rb, HID_LEN, None, ctypes.byref(rov))
            k32.CancelIo(w.HANDLE(hd))
            k32.WaitForSingleObject(w.HANDLE(ev), 100)      # deixa o cancelamento terminar antes de soltar os buffers
            if res:
                out.append({"key": f"receptor-{idx}", "name": RECEIVER_NAME, "pct": res[0], "charging": res[1], "kind": "mouse"})
        finally:
            k32.CloseHandle(w.HANDLE(hd))
            for e in (ev, sev):
                if e:
                    k32.CloseHandle(w.HANDLE(e))
    return out


class Battery:
    """Estado das baterias + regra do aviso. read_fn() -> [{key, name, pct}] (injetável: o teste usa uma leitura falsa)."""

    def __init__(self, read_fn=read_windows, hid_fn=None, clock=time.monotonic):
        self.read_fn = read_fn
        self.hid_fn, self.clock = hid_fn, clock
        self.hid_at, self.hid_items = None, []     # última leitura do receptor (só a cada HID_EVERY_S)
        self.items = []
        self.polled = False
        self.low = set()      # ids já avisados e ainda abaixo de REARM_PCT

    def poll(self):
        """Lê o Windows. Devolve (mudou, avisos[{id,name,pct}]). Erro de leitura = mantém o que havia e não inventa."""
        try:
            raw = list(self.read_fn() or [])
        except Exception:  # noqa: BLE001
            return False, []
        now = self.clock()
        if self.hid_fn and (self.hid_at is None or now - self.hid_at >= HID_EVERY_S):
            self.hid_at = now
            try:
                self.hid_items = list(self.hid_fn() or [])     # mouse dormindo/sem resposta = some da lista
            except Exception:  # noqa: BLE001
                self.hid_items = []
        items = []
        for r in raw + self.hid_items:
            pct = valid_pct(r.get("pct")) if isinstance(r, dict) else None
            if pct is None or (r.get("kind") == "mouse" and pct < 1):
                continue
            it = {"id": opaque_id(r.get("key")), "name": clean_name(r.get("name")), "pct": pct}
            if r.get("kind") == "mouse":
                it["kind"], it["charging"] = "mouse", bool(r.get("charging"))
            items.append(it)
        items = sorted(items, key=lambda i: (i["pct"], i["name"]))[:MAX_ITEMS]
        alerts = []
        here = {i["id"]: i for i in items}
        self.low &= set(here)                                   # aparelho que sumiu: rearma
        for i in items:
            if i.get("charging"):
                continue                                        # carregando: sem aviso (e sem marcar como avisado)
            if i["pct"] < LOW_PCT and i["id"] not in self.low:
                self.low.add(i["id"])
                alerts.append(dict(i))
            elif i["pct"] >= REARM_PCT:
                self.low.discard(i["id"])
        changed = (items != self.items) or not self.polled
        self.items, self.polled = items, True
        return changed, alerts

    def state(self):
        return {"items": [dict(i) for i in self.items], "low_pct": LOW_PCT}


def message(a):
    """(título, corpo) do aviso. O corpo leva o nome que o Windows dá ao aparelho e a porcentagem."""
    return "Bateria baixa", f"{a['name']}: {a['pct']}%"
