"""Ícone na bandeja do Windows + painel de status (tkinter) para o servidor.

O servidor roda sem janela (pythonw). O painel só aparece quando o usuário
pede: duplo clique no ícone, menu "Abrir painel" ou abrindo o programa de
novo (a segunda instância só avisa a primeira e sai).
"""
import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("companion")

kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

MUTEX_NAME = "Local\\CompanionPCServer"
SHOW_EVENT = "Local\\CompanionPCShowPanel"
ACK_EVENT = "Local\\CompanionPCPanelAck"  # a instância aberta confirma que mostrou o painel
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002

COLORS = {"ok": (46, 204, 113), "wait": (241, 196, 15), "off": (127, 140, 141)}


# --------------------------------------------------------------------------
# Instância única
# --------------------------------------------------------------------------
_mutex = None


def acquire_single_instance(wait_s=0.0):
    """True se esta é a única instância. Com wait_s, espera a anterior sair (reinício)."""
    global _mutex
    deadline = time.monotonic() + wait_s
    while True:
        h = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.GetLastError() != ERROR_ALREADY_EXISTS:
            _mutex = h
            return True
        kernel32.CloseHandle(h)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def _ack_event():
    return kernel32.CreateEventW(None, False, False, ACK_EVENT)  # automático; abre o mesmo se já existir


def ack_panel():
    """Instância aberta: o painel apareceu (a thread do tkinter está viva)."""
    h = _ack_event()
    if h:
        kernel32.SetEvent(h)
        kernel32.CloseHandle(h)


def signal_running_instance(wait_s=5.0):
    """Pede para a instância que já está rodando mostrar o painel e espera ela confirmar.
    True = respondeu; False = não respondeu em wait_s (travada, ou sem painel)."""
    ack = _ack_event()
    if ack:
        kernel32.ResetEvent(ack)
    h = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT)
    ok = False
    if h:
        kernel32.SetEvent(h)
        kernel32.CloseHandle(h)
        ok = bool(ack) and kernel32.WaitForSingleObject(ack, int(wait_s * 1000)) == 0  # WAIT_OBJECT_0
    if ack:
        kernel32.CloseHandle(ack)
    return ok


def ask_kill_stuck():
    """Pergunta se encerra a instância que não respondeu. True = sim."""
    msg = ("O Companion PC já está aberto, mas não respondeu ao pedido de mostrar o painel.\n\n"
           "Encerrar a instância travada e abrir de novo?")
    return ctypes.windll.user32.MessageBoxW(None, msg, "Companion PC", 0x4 | 0x30 | 0x10000) == 6  # SIM/NÃO, aviso; 6 = Sim


def kill_running_instances():
    """Encerra os outros processos python com companion_server.py. Devolve quantos."""
    import psutil
    me, n = os.getpid(), 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == me or (p.info["name"] or "").lower() not in ("python.exe", "pythonw.exe"):
                continue
            if any("companion_server.py" in (c or "") for c in (p.info["cmdline"] or [])):
                p.kill()
                n += 1
        except psutil.Error:
            continue
    return n


# --------------------------------------------------------------------------
# Ícone
# --------------------------------------------------------------------------
def make_icon(color):
    from PIL import Image, ImageDraw
    s = 64
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((2, 2, s - 3, s - 3), 14, fill=(30, 34, 42, 255))
    # controle estilizado
    d.rounded_rectangle((9, 20, 55, 46), 12, fill=color + (255,))
    d.rectangle((17, 31, 27, 35), fill=(30, 34, 42, 255))
    d.rectangle((20, 28, 24, 38), fill=(30, 34, 42, 255))
    d.ellipse((38, 26, 44, 32), fill=(30, 34, 42, 255))
    d.ellipse((44, 33, 50, 39), fill=(30, 34, 42, 255))
    return im


# --------------------------------------------------------------------------
# App da bandeja
# --------------------------------------------------------------------------
class TrayApp:
    def __init__(self, status_fn, log_path: Path, base: Path, version: str, stop_server, open_on_phone=None, ops=None):
        """status_fn() -> dict(state='ok'|'wait'|'off', text=str, game=str|None, phone=dict).
        ops: funções do servidor para as abas Trabalho/Configurações (rodam na fila de disco do servidor)."""
        self.ops = ops
        self.status_fn = status_fn
        self.open_on_phone_fn = open_on_phone
        self.log_path = log_path
        self.base = base
        self.version = version
        self.stop_server = stop_server
        self.icon = None
        self.panel_q = queue.Queue()
        self.panel_open = False
        self._last_state = None

    # -- ações --
    def open_panel(self, *_):
        if self.panel_open:
            self.panel_q.put("raise")
            return
        self.panel_open = True
        threading.Thread(target=self._panel_thread, daemon=True, name="panel").start()

    def open_folder(self, *_):
        os.startfile(self.base)

    def open_log(self, *_):
        os.startfile(self.log_path)

    def open_on_phone(self, *_):
        """Abre o app no celular ligado no cabo (adb). Retorna mensagem de erro ou None."""
        try:
            self.open_on_phone_fn()
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("abrir no celular: %s", e)
            return str(e)

    def restart(self, *_):
        log.info("reiniciando pelo ícone da bandeja")
        script = str(self.base / "companion_server.py")
        subprocess.Popen([sys.executable, script, "--restart"], cwd=str(self.base),
                         creationflags=subprocess.CREATE_NO_WINDOW)
        self.quit(restarting=True)

    def quit(self, *_, restarting=False):
        if not restarting:
            log.info("encerrado pelo ícone da bandeja")
        try:
            self.stop_server()
        finally:
            if self.icon:
                self.icon.stop()
            logging.shutdown()
            os._exit(0)

    # -- threads --
    def _watch_show_event(self):
        h = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
        while True:
            if kernel32.WaitForSingleObject(h, 0xFFFFFFFF) == 0:
                self.open_panel()

    def _watch_status(self):
        while True:
            try:
                st = self.status_fn()
                if self.icon:
                    key = (st["state"], st["text"])
                    if key != self._last_state:
                        self._last_state = key
                        self.icon.icon = make_icon(COLORS[st["state"]])
                        self.icon.title = f"Companion PC – {st['text']}"[:127]
            except Exception:  # noqa: BLE001
                log.exception("tray status")
            time.sleep(1.5)

    def _panel_thread(self):
        try:
            Panel(self).run()
        except Exception:  # noqa: BLE001
            log.exception("painel")
        finally:
            self.panel_open = False

    def run(self):
        import pystray
        from pystray import Menu, MenuItem as Item

        st = self.status_fn()
        self.icon = pystray.Icon(
            "companion-pc", make_icon(COLORS[st["state"]]), "Companion PC",
            menu=Menu(
                Item(lambda _: f"Companion PC {self.version}", None, enabled=False),
                Item(lambda _: self.status_fn()["text"], None, enabled=False),
                Menu.SEPARATOR,
                Item("Abrir painel", self.open_panel, default=True),
                Item("Abrir app no celular (cabo)", self.open_on_phone,
                     visible=lambda _: bool(self.open_on_phone_fn and self.status_fn().get("phone", {}).get("adb"))),
                Item("Ver log", self.open_log),
                Item("Abrir pasta (config.json)", self.open_folder),
                Menu.SEPARATOR,
                Item("Reiniciar servidor", self.restart),
                Item("Sair", self.quit),
            ),
        )
        threading.Thread(target=self._watch_show_event, daemon=True, name="show-event").start()
        threading.Thread(target=self._watch_status, daemon=True, name="tray-status").start()
        self.icon.run()


# --------------------------------------------------------------------------
# Painel
# --------------------------------------------------------------------------
def tk_mod():
    import tkinter
    return tkinter


def fmt_dur(s):
    s = int(s or 0)
    if s < 60:
        return f"{s}s" if s else "0"
    h, m = s // 3600, s // 60 % 60
    return f"{h}h{m:02d}" if h else f"{m} min"


class Panel:
    BG, CARD, TEXT, DIM = "#14171c", "#1f242c", "#e8ecf1", "#8a94a3"
    STATE_HEX = {"ok": "#2ecc71", "wait": "#f1c40f", "off": "#7f8c8d"}

    def __init__(self, app: TrayApp):
        self.app = app

    def run(self):
        import tkinter as tk
        from tkinter import font as tkfont

        root = self.root = tk.Tk()
        root.title("Companion PC")
        root.configure(bg=self.BG)
        root.geometry("780x700")
        root.minsize(460, 340)
        try:
            from PIL import ImageTk
            self._ico = ImageTk.PhotoImage(make_icon(COLORS["ok"]))
            root.iconphoto(True, self._ico)
        except Exception:  # noqa: BLE001
            pass

        base = tkfont.nametofont("TkDefaultFont").actual("family")
        f_title = (base, 15, "bold")
        f = (base, 10)
        f_b = (base, 10, "bold")

        self.f, self.f_b = f, f_b
        head = tk.Frame(root, bg=self.BG)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="Companion PC", font=f_title, fg=self.TEXT, bg=self.BG).pack(side="left")
        tk.Label(head, text=f"v{self.app.version}", font=f, fg=self.DIM, bg=self.BG).pack(side="left", padx=8, pady=(4, 0))

        # botões de baixo ficam fora das abas (sempre visíveis)
        btns = tk.Frame(root, bg=self.BG)
        btns.pack(side="bottom", fill="x", padx=16, pady=12)

        # abas no mesmo visual escuro do painel
        from tkinter import ttk
        style = ttk.Style(root)
        style.theme_use("clam")
        flat = {"bordercolor": self.BG, "lightcolor": self.BG, "darkcolor": self.BG}  # sem a borda clara do tema
        style.configure("TNotebook", background=self.BG, borderwidth=0, tabmargins=(16, 0, 16, 0), **flat)
        style.configure("TNotebook.Tab", background=self.CARD, foreground=self.DIM, borderwidth=0,
                        padding=(14, 6), font=f, **flat)
        style.map("TNotebook.Tab", background=[("selected", "#2b323c")], foreground=[("selected", self.TEXT)],
                  lightcolor=[("selected", "#2b323c")], bordercolor=[("selected", self.BG)])
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True)
        tab = tk.Frame(self.nb, bg=self.BG)
        self.nb.add(tab, text="Status")

        card = tk.Frame(tab, bg=self.CARD)
        card.pack(fill="x", padx=16, pady=(12, 0))
        row = tk.Frame(card, bg=self.CARD)
        row.pack(fill="x", padx=12, pady=(10, 2))
        self.dot = tk.Canvas(row, width=12, height=12, bg=self.CARD, highlightthickness=0)
        self.dot.pack(side="left", pady=2)
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill=self.STATE_HEX["off"], outline="")
        self.status = tk.Label(row, font=f_b, fg=self.TEXT, bg=self.CARD, anchor="w")
        self.status.pack(side="left", padx=8)
        self.game = tk.Label(card, font=f, fg=self.DIM, bg=self.CARD, anchor="w")
        self.game.pack(fill="x", padx=32, pady=(0, 10))

        # Celular: QR com o link (Wi-Fi) + estado do cabo
        tk.Label(tab, text="Celular (Android)", font=f_b, fg=self.DIM, bg=self.BG, anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        pc = tk.Frame(tab, bg=self.CARD)
        pc.pack(fill="x", padx=16)
        self.qr = tk.Label(pc, bg=self.CARD)
        self.qr.pack(side="left", padx=10, pady=10)
        info = tk.Frame(pc, bg=self.CARD)
        info.pack(side="left", fill="both", expand=True, padx=(4, 12), pady=10)
        self.phone_wifi = tk.Label(info, font=f, fg=self.TEXT, bg=self.CARD, anchor="w", justify="left", wraplength=380)
        self.phone_wifi.pack(fill="x")
        self.phone_cable = tk.Label(info, font=f, fg=self.DIM, bg=self.CARD, anchor="w", justify="left", wraplength=380)
        self.phone_cable.pack(fill="x", pady=(8, 0))
        self.phone_btn = tk.Button(info, text="Abrir app no celular (cabo)", font=f, fg=self.TEXT, bg="#2b323c",
                                   activebackground="#36404c", activeforeground=self.TEXT, relief="flat",
                                   borderwidth=0, padx=12, pady=5, cursor="hand2", command=self._open_phone)
        self.phone_msg = tk.Label(info, font=f, fg=self.DIM, bg=self.CARD, anchor="w")
        self._qr_url = None

        tk.Label(tab, text="Log", font=f_b, fg=self.DIM, bg=self.BG, anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        self.log = tk.Text(tab, height=10, bg=self.CARD, fg=self.TEXT, font=("Consolas", 9), relief="flat",
                           borderwidth=0, highlightthickness=0, wrap="word", padx=8, pady=6)
        self.log.pack(fill="both", expand=True, padx=16)
        self.log.configure(state="disabled")

        def button(text, cmd, side="left"):
            b = tk.Button(btns, text=text, command=cmd, font=f, fg=self.TEXT, bg=self.CARD,
                          activebackground="#2b323c", activeforeground=self.TEXT, relief="flat",
                          borderwidth=0, padx=12, pady=5, cursor="hand2")
            b.pack(side=side, padx=(0, 8) if side == "left" else (8, 0))

        if self.app.ops:
            self._style_widgets(style)
            self._build_work()
            self._build_config()
        button("Abrir pasta", self.app.open_folder)
        button("Ver log completo", self.app.open_log)
        button("Fechar", root.destroy, "right")
        button("Sair do servidor", self.app.quit, "right")
        button("Reiniciar", self.app.restart, "right")

        root.protocol("WM_DELETE_WINDOW", root.destroy)
        self._log_mtime = None
        self.refresh()
        self.bring_to_front()
        root.mainloop()

    # ------------------------------------------------------------------ peças comuns das abas
    def _style_widgets(self, style):
        f = self.f
        flat = {"bordercolor": self.CARD, "lightcolor": self.CARD, "darkcolor": self.CARD}
        style.configure("Treeview", background=self.CARD, fieldbackground=self.CARD, foreground=self.TEXT,
                        rowheight=22, font=f, borderwidth=0, **flat)
        style.map("Treeview", background=[("selected", "#36404c")], foreground=[("selected", self.TEXT)])
        style.configure("Treeview.Heading", background="#2b323c", foreground=self.DIM, font=f, relief="flat", **flat)
        style.map("Treeview.Heading", background=[("active", "#36404c")])
        style.configure("TCombobox", fieldbackground=self.CARD, background="#2b323c", foreground=self.TEXT,
                        arrowcolor=self.TEXT, **flat)
        style.map("TCombobox", fieldbackground=[("readonly", self.CARD)], foreground=[("readonly", self.TEXT)],
                  selectbackground=[("readonly", self.CARD)], selectforeground=[("readonly", self.TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", self.CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", self.TEXT)

    def _btn(self, parent, text, cmd):
        return tk_mod().Button(parent, text=text, command=cmd, font=self.f, fg=self.TEXT, bg="#2b323c",
                               activebackground="#36404c", activeforeground=self.TEXT, relief="flat",
                               borderwidth=0, padx=10, pady=4, cursor="hand2")

    def _label(self, parent, text="", bold=False, dim=False, bg=None):
        return tk_mod().Label(parent, text=text, font=self.f_b if bold else self.f, fg=self.DIM if dim else self.TEXT,
                              bg=bg or self.BG, anchor="w", justify="left")

    def _listbox(self, parent, height):
        return tk_mod().Listbox(parent, height=height, bg=self.CARD, fg=self.TEXT, font=self.f, relief="flat",
                                borderwidth=0, highlightthickness=0, selectbackground="#36404c",
                                selectforeground=self.TEXT, activestyle="none", exportselection=False)

    def _call(self, name, *args):
        """Chama o servidor; erro de validação vira aviso na tela. Devolve None se falhou."""
        from tkinter import messagebox
        try:
            return self.app.ops[name](*args)
        except ValueError as e:
            messagebox.showerror("Companion PC", str(e), parent=self.root)
        except Exception as e:  # noqa: BLE001
            log.warning("painel %s: %s", name, type(e).__name__)
            messagebox.showerror("Companion PC", f"Não deu certo ({type(e).__name__}). Veja o log.", parent=self.root)
        return None

    # ------------------------------------------------------------------ aba Trabalho
    # Cliente e job vêm das pastas (work.py): aqui só as pastas-raiz de clientes, a ausência e o relatório.
    UNID_FG = "#f1c40f"  # "Não identificado" no mesmo amarelo do estado "aguardando"

    def _build_work(self):
        tk = tk_mod()
        from tkinter import ttk
        tab = self.work_tab = tk.Frame(self.nb, bg=self.BG)
        self.nb.add(tab, text="Trabalho")
        self.w_data, self.w_week, self.w_off, self.w_open = None, None, 0, set()
        pad = {"padx": 16}

        self._label(tab, "Pastas de clientes  (Cliente\\Job\\arquivo — cliente e job saem dos nomes das pastas)",
                    bold=True, dim=True).pack(fill="x", pady=(12, 2), **pad)
        rf = tk.Frame(tab, bg=self.BG)
        rf.pack(fill="x", **pad)
        self.w_roots = self._listbox(rf, 3)
        self.w_roots.pack(side="left", fill="x", expand=True)
        rb = tk.Frame(rf, bg=self.BG)
        rb.pack(side="left", padx=(10, 0), anchor="n")
        for t, c in (("Adicionar…", self._w_add_root), ("Remover", self._w_remove_root),
                     ("Abrir no Explorer", self._w_open_root)):
            self._btn(rb, t, c).pack(fill="x", pady=(0, 4))
        self.w_index = self._label(tab, dim=True)
        self.w_index.pack(fill="x", **pad)

        top = tk.Frame(tab, bg=self.BG)
        top.pack(fill="x", pady=(8, 0), **pad)
        self._label(top, "Parar de contar depois de", dim=True).pack(side="left")
        self.w_idle = tk.Spinbox(top, from_=1, to=30, width=3, font=self.f, bg=self.CARD, fg=self.TEXT,
                                 buttonbackground="#2b323c", relief="flat", insertbackground=self.TEXT, justify="center")
        self.w_idle.pack(side="left", padx=6)
        self._label(top, "min parado", dim=True).pack(side="left")
        self._btn(top, "Salvar", self._w_save_idle).pack(side="left", padx=(8, 0))
        self.w_now = self._label(tab)
        self.w_now.pack(fill="x", pady=(8, 0), **pad)

        rep = tk.Frame(tab, bg=self.BG)
        rep.pack(fill="x", pady=(12, 4), **pad)
        self._label(rep, "Relatório", bold=True, dim=True).pack(side="left")
        self._btn(rep, "‹", lambda: self._w_week_move(-1)).pack(side="left", padx=(12, 4))
        self.w_range = self._label(rep)
        self.w_range.pack(side="left", padx=4)
        self.w_next = self._btn(rep, "›", lambda: self._w_week_move(1))
        self.w_next.pack(side="left", padx=4)
        self._btn(rep, "Exportar CSV…", self._w_csv).pack(side="right")
        self._btn(rep, "Reclassificar não identificados…", self._w_reclass).pack(side="right", padx=(0, 8))
        self.w_days = self._label(tab, dim=True)
        self.w_days.pack(fill="x", **pad)
        self.w_tree = ttk.Treeview(tab, columns=("a", "t"), show="tree headings", selectmode="browse")
        self.w_tree.heading("#0", text="Cliente › Job › Arquivo")
        self.w_tree.heading("a", text="Programa")
        self.w_tree.heading("t", text="Tempo")
        self.w_tree.column("a", width=110, stretch=False)
        self.w_tree.column("t", width=90, anchor="e", stretch=False)
        self.w_tree.tag_configure("unid", foreground=self.UNID_FG)
        self.w_tree.tag_configure("client", font=self.f_b)
        self.w_tree.pack(fill="both", expand=True, pady=(6, 0), **pad)
        self.w_tree.bind("<<TreeviewOpen>>", lambda _e: self._w_track_open(True))
        self.w_tree.bind("<<TreeviewClose>>", lambda _e: self._w_track_open(False))
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._w_load(True) if self._tab_is(self.work_tab) else None)
        self._w_tick = 0

    def _tab_is(self, frame):
        return str(self.nb.select()) == str(frame)

    def _w_load(self, week=False):
        d = self._call("work_get")
        if d is None:
            return
        self.w_data = d
        if week or self.w_week is None:
            w = self._call("work_week", self.w_off)
            if w is not None:
                self.w_week = w
        self._w_render()

    def _w_render(self):
        d = self.w_data
        sel = self.w_roots.curselection()
        keep = self.w_roots.get(sel[0]) if sel else None
        self.w_roots.delete(0, "end")
        for r in d["roots"]:
            self.w_roots.insert("end", r)
            if r == keep:
                self.w_roots.selection_set("end")
        ix = d.get("index") or {}
        self.w_index.configure(text="Nenhuma pasta de clientes: todo o tempo fica como \"Não identificado\"." if not d["roots"]
                               else f"Photoshop: {ix.get('files', 0)} imagens indexadas pelo nome"
                                    + (" (limite atingido)" if ix.get("truncated") else ""))
        if self.root.focus_get() is not self.w_idle:
            self.w_idle.delete(0, "end")
            self.w_idle.insert(0, str(d["idle_min"]))
        n = d["now"]
        if not n.get("app"):
            txt, fg = "Nenhum programa da Adobe em foco.", self.DIM
        else:
            who = f"{n['client']} › {n['job']}" if n.get("identified") else n["client"]
            txt = f"{'Contando agora' if n['counting'] else 'Parado (ninguém mexendo no PC)'}: {n['app']} · {who} · {n['file']}"
            fg = self.TEXT if n.get("identified") else self.UNID_FG
        self.w_now.configure(text=f"{txt}   —   hoje: {fmt_dur(d['today_s'])}", fg=fg)
        w = self.w_week
        if not w:
            return
        dm = lambda s: s[8:10] + "/" + s[5:7]  # noqa: E731
        self.w_range.configure(text=("Esta semana" if w["offset"] == 0 else "") +
                               f"  {dm(w['start'])} a {dm(w['end'])}   total {fmt_dur(w['total_s'])}")
        self.w_next.configure(state="disabled" if w["offset"] >= 0 else "normal")
        self.w_days.configure(text="   ".join(f"{x['d']} {fmt_dur(x['s'])}" for x in w["days"]))
        t = self.w_tree
        t.delete(*t.get_children())
        for c in w["clients"]:
            tags = ("client", "unid") if c["unidentified"] else ("client",)
            cid = t.insert("", "end", text=c["client"], values=("", fmt_dur(c["s"])), tags=tags,
                           open=("c", c["client"]) in self.w_open)
            for j in c["jobs"]:
                jid = cid
                if not c["unidentified"]:  # "Não identificado" mostra os arquivos direto
                    jid = t.insert(cid, "end", text=j["job"], values=("", fmt_dur(j["s"])),
                                   open=("j", c["client"], j["job"]) in self.w_open)
                for f in j["files"]:
                    t.insert(jid, "end", text=f["file"], values=(f["app"], fmt_dur(f["s"])),
                             tags=("unid",) if c["unidentified"] else ())
        if not w["clients"]:
            t.insert("", "end", text="Nenhum tempo nesta semana", values=("", ""))

    def _w_track_open(self, is_open):
        t = self.w_tree
        item = t.focus()
        if not item:
            return
        parent = t.parent(item)
        key = ("c", t.item(item, "text")) if not parent else ("j", t.item(parent, "text"), t.item(item, "text"))
        (self.w_open.add if is_open else self.w_open.discard)(key)

    def _w_after(self, d):
        if d is not None:
            self.w_data = d
            self._w_load(True)

    def _w_save_idle(self):
        r = self._call("config_save", {"idle_min": self.w_idle.get()})
        if r is not None:
            self.root.focus_set()
            self._w_load()

    def _w_add_root(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Pasta onde ficam as pastas dos clientes", mustexist=True, parent=self.root)
        if folder:
            self._w_after(self._call("work_add_root", os.path.normpath(folder)))

    def _w_selected_root(self):
        s = self.w_roots.curselection()
        return self.w_roots.get(s[0]) if s else None

    def _w_remove_root(self):
        from tkinter import messagebox
        r = self._w_selected_root()
        if r and messagebox.askyesno("Remover pasta", f"Parar de usar\n{r}\npara achar cliente e job?\n"
                                     "O tempo já registrado continua no relatório.", parent=self.root):
            self._w_after(self._call("work_remove_root", r))

    def _w_open_root(self):
        r = self._w_selected_root()
        if r and os.path.isdir(r):
            os.startfile(r)  # só abre a pasta escolhida no Explorer

    def _w_week_move(self, d):
        new = self.w_off + d
        if -104 <= new <= 0:
            self.w_off = new
            self._w_load(True)

    def _w_csv(self):
        from tkinter import filedialog, messagebox
        got = self._call("work_csv", self.w_off)
        if got is None:
            return
        name, data = got
        path = filedialog.asksaveasfilename(title="Salvar CSV da semana", initialfile=name, defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.root)
        if path:
            try:
                Path(path).write_bytes(data)
            except OSError as e:
                messagebox.showerror("Companion PC", f"Não consegui salvar: {e}", parent=self.root)

    def _w_reclass(self):
        """Mostra o resumo (arquivo → cliente › job, tempo) e só aplica se confirmar."""
        tk = tk_mod()
        from tkinter import messagebox, ttk
        p = self._call("work_reclass_preview")
        if p is None:
            return
        if not p["items"]:
            messagebox.showinfo("Reclassificar", "Nada a reclassificar: nenhum \"Não identificado\" tem um arquivo "
                                "que agora é achado nas pastas de clientes.\n(\"sem título\" nunca muda.)",
                                parent=self.root)
            return
        win = tk.Toplevel(self.root, bg=self.BG)
        win.title("Reclassificar não identificados")
        win.transient(self.root)
        win.geometry("760x420")
        self._label(win, f"Vai sair de \"Não identificado\" ({fmt_dur(p['s'])} no total). "
                         "O total de horas não muda; antes é feito um backup do histórico.",
                    bold=True).pack(fill="x", padx=16, pady=(12, 6))
        tv = ttk.Treeview(win, columns=("a", "d", "t"), show="headings")
        for c, txt, w in (("a", "Arquivo", 260), ("d", "→ Cliente › Job", 260), ("t", "Tempo", 90)):
            tv.heading(c, text=txt)
            tv.column(c, width=w, anchor="e" if c == "t" else "w", stretch=c != "t")
        for it in p["items"]:
            tv.insert("", "end", values=(f"{it['file']}  ({it['app']})", f"{it['client']} › {it['job']}",
                                         fmt_dur(it["s"])))
        tv.pack(fill="both", expand=True, padx=16)
        bf = tk.Frame(win, bg=self.BG)
        bf.pack(fill="x", padx=16, pady=10)

        def apply():
            r = self._call("work_reclass_apply")
            win.destroy()
            if r is not None:
                messagebox.showinfo("Reclassificar", f"{r['moved']} registro(s) reclassificado(s), {fmt_dur(r['s'])}.\n"
                                    f"Total antes {fmt_dur(r['total_before_s'])}, depois {fmt_dur(r['total_after_s'])}.\n"
                                    f"Backup: {r['backup']}", parent=self.root)
                self._w_load(True)

        self._btn(bf, "Cancelar", win.destroy).pack(side="right")
        self._btn(bf, "Aplicar", apply).pack(side="right", padx=(0, 8))
        win.grab_set()

    # ------------------------------------------------------------------ aba Configurações
    CFG_KEYS = ("upload_dir", "downloads_dir", "allow_power", "shutdown_delay_s", "notif_ignore", "shortcuts")

    def _build_config(self):
        tk = tk_mod()
        from tkinter import ttk
        tab = self.cfg_tab = tk.Frame(self.nb, bg=self.BG)
        self.nb.add(tab, text="Configurações")
        pad = {"padx": 16}
        self.c_orig, self.c_vals, self.c_labels = None, None, {}

        self._label(tab, "Pastas", bold=True, dim=True).pack(fill="x", pady=(12, 2), **pad)
        for key, text in (("upload_dir", "Arquivos enviados pelo celular"), ("downloads_dir", "Pasta vigiada (download concluído)")):
            row = tk.Frame(tab, bg=self.BG)
            row.pack(fill="x", pady=2, **pad)
            self._label(row, text, dim=True).pack(side="left")
            self._btn(row, "Escolher…", lambda k=key: self._c_pick_dir(k)).pack(side="right")
            self.c_labels[key] = self._label(row)
            self.c_labels[key].pack(side="left", padx=8, fill="x", expand=True)

        self._label(tab, "Energia", bold=True, dim=True).pack(fill="x", pady=(10, 2), **pad)
        row = tk.Frame(tab, bg=self.BG)
        row.pack(fill="x", **pad)
        self.c_power = tk.BooleanVar()
        tk.Checkbutton(row, text="Permitir desligar, reiniciar e suspender pelo celular", variable=self.c_power,
                       command=lambda: self._c_set("allow_power", bool(self.c_power.get())), font=self.f,
                       fg=self.TEXT, bg=self.BG, selectcolor=self.CARD, activebackground=self.BG,
                       activeforeground=self.TEXT, highlightthickness=0).pack(side="left")
        self._label(row, "   atraso", dim=True).pack(side="left")
        self.c_delay = tk.Spinbox(row, from_=0, to=600, increment=5, width=4, font=self.f, bg=self.CARD, fg=self.TEXT,
                                  buttonbackground="#2b323c", relief="flat", insertbackground=self.TEXT, justify="center",
                                  command=self._c_delay_changed)
        self.c_delay.bind("<KeyRelease>", lambda _e: self._c_delay_changed())
        self.c_delay.pack(side="left", padx=6)
        self._label(row, "s", dim=True).pack(side="left")

        cols = tk.Frame(tab, bg=self.BG)
        cols.pack(fill="both", expand=True, pady=(10, 0), **pad)
        cols.columnconfigure(0, weight=2, uniform="k")
        cols.columnconfigure(1, weight=5, uniform="k")
        cols.rowconfigure(1, weight=1)
        self._label(cols, "Notificações ignoradas", bold=True, dim=True).grid(row=0, column=0, sticky="w")
        self._label(cols, "Programas da página \"Programas\"", bold=True, dim=True).grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.c_notif = self._listbox(cols, 6)
        self.c_notif.grid(row=1, column=0, sticky="nsew", pady=4)
        self.c_progs = ttk.Treeview(cols, columns=("id", "open"), show="tree headings", selectmode="browse", height=6)
        self.c_progs.heading("#0", text="Nome")
        self.c_progs.heading("id", text="id")
        self.c_progs.heading("open", text="Abre")
        self.c_progs.column("#0", width=140, stretch=False)
        self.c_progs.column("id", width=90, stretch=False)
        self.c_progs.grid(row=1, column=1, sticky="nsew", pady=4, padx=(12, 0))
        nb = tk.Frame(cols, bg=self.BG)
        nb.grid(row=2, column=0, sticky="we")
        self.c_notif_entry = tk.Entry(nb, font=self.f, bg=self.CARD, fg=self.TEXT, relief="flat",
                                      insertbackground=self.TEXT, width=14)
        self.c_notif_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.c_notif_entry.bind("<Return>", lambda _e: self._c_notif_add())
        self._btn(nb, "+", self._c_notif_add).pack(side="left", padx=(4, 0))
        self._btn(nb, "−", self._c_notif_remove).pack(side="left", padx=(4, 0))
        pb = tk.Frame(cols, bg=self.BG)
        pb.grid(row=2, column=1, sticky="w", padx=(12, 0))
        for t, c in (("Adicionar…", self._c_prog_add), ("Editar…", self._c_prog_edit), ("Remover", self._c_prog_remove)):
            self._btn(pb, t, c).pack(side="left", padx=(0, 6))

        self._label(tab, "Rede (só leitura)", bold=True, dim=True).pack(fill="x", pady=(10, 2), **pad)
        self.c_net = self._label(tab)
        self.c_net.pack(fill="x", **pad)

        bar = tk.Frame(tab, bg=self.BG)
        bar.pack(fill="x", pady=(10, 4), **pad)
        self._btn(bar, "Salvar alterações", self._c_save).pack(side="left")
        self._btn(bar, "Desfazer", lambda: self._c_load()).pack(side="left", padx=6)
        self.c_msg = self._label(bar, dim=True)
        self.c_msg.pack(side="left", padx=8)
        self.c_restart = self._btn(bar, "Reiniciar agora", self.app.restart)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab, add="+")

    def _on_tab(self, _e=None):
        if self._tab_is(self.cfg_tab) and self.c_vals is None:
            self._c_load()

    def _c_load(self):
        import copy
        d = self._call("config_get")
        if d is None:
            return
        self.c_orig = {k: d[k] for k in self.CFG_KEYS}
        self.c_vals = copy.deepcopy(self.c_orig)
        n = d["net"]
        self.c_net.configure(text=f"IP {n['ip'] or '?'}   ·   porta {n['port']}   ·   {n['mode']}")
        self.c_msg.configure(text="")
        self._c_render()

    def _c_render(self):
        v = self.c_vals
        for k in ("upload_dir", "downloads_dir"):
            self.c_labels[k].configure(text=v[k] or "")
        self.c_power.set(bool(v["allow_power"]))
        if self.root.focus_get() is not self.c_delay:
            self.c_delay.delete(0, "end")
            self.c_delay.insert(0, str(v["shutdown_delay_s"]))
        self.c_notif.delete(0, "end")
        for a in v["notif_ignore"]:
            self.c_notif.insert("end", a)
        self.c_progs.delete(*self.c_progs.get_children())
        for i, s in enumerate(v["shortcuts"]):
            self.c_progs.insert("", "end", iid=str(i), text=s.get("name", ""),
                                values=(s.get("id", ""), s.get("open", "") + (f"  {s['args']}" if s.get("args") else "")))
        dirty = [k for k in self.CFG_KEYS if v[k] != self.c_orig[k]]
        if dirty:
            self.c_msg.configure(text="Alterações não salvas", fg=self.TEXT)

    def _c_set(self, key, value):
        if self.c_vals is None:
            return
        self.c_vals[key] = value
        self._c_render()

    def _c_delay_changed(self):
        try:
            self._c_set("shutdown_delay_s", int(self.c_delay.get()))
        except ValueError:
            pass

    def _c_pick_dir(self, key):
        from tkinter import filedialog
        cur = os.path.expandvars(self.c_vals[key] or "")
        d = filedialog.askdirectory(title="Escolher pasta", initialdir=cur if os.path.isdir(cur) else None,
                                    mustexist=True, parent=self.root)
        if d:
            self._c_set(key, os.path.normpath(d))

    def _c_notif_add(self):
        name = self.c_notif_entry.get().strip()
        if name and name not in self.c_vals["notif_ignore"]:
            self.c_vals["notif_ignore"].append(name)
            self.c_notif_entry.delete(0, "end")
            self._c_render()

    def _c_notif_remove(self):
        s = self.c_notif.curselection()
        if s:
            del self.c_vals["notif_ignore"][s[0]]
            self._c_render()

    @staticmethod
    def _slug(name, taken):
        import unicodedata
        base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        base = "-".join("".join(c if c.isalnum() else " " for c in base).split())[:36] or "programa"
        sid, n = base, 2
        while sid in taken:
            sid, n = f"{base}-{n}", n + 1
        return sid

    def _c_prog_add(self):
        from tkinter import filedialog, simpledialog
        path = filedialog.askopenfilename(title="Programa para a página Programas", parent=self.root,
                                          filetypes=[("Programas e atalhos", "*.exe *.lnk")])
        if not path:
            return
        path = os.path.normpath(path)
        if not path.lower().endswith((".exe", ".lnk")):
            return
        name = simpledialog.askstring("Nome do programa", "Nome que aparece no botão:",
                                      initialvalue=Path(path).stem[:60], parent=self.root)
        if not name or not name.strip():
            return
        args = simpledialog.askstring("Argumentos", "Argumentos (opcional, deixe vazio se não souber):", parent=self.root)
        taken = {s.get("id") for s in self.c_vals["shortcuts"]}
        s = {"id": self._slug(name.strip(), taken), "name": name.strip(), "open": path}
        if args and args.strip():
            s["args"] = args.strip()
        self.c_vals["shortcuts"].append(s)
        self._c_render()

    def _c_prog_sel(self):
        s = self.c_progs.selection()
        return int(s[0]) if s else None

    def _c_prog_edit(self):
        from tkinter import simpledialog
        i = self._c_prog_sel()
        if i is None:
            return
        s = self.c_vals["shortcuts"][i]
        name = simpledialog.askstring("Editar programa", "Nome que aparece no botão:", initialvalue=s.get("name", ""),
                                      parent=self.root)
        if name is None or not name.strip():
            return
        args = simpledialog.askstring("Editar programa", "Argumentos (opcional):", initialvalue=s.get("args", ""),
                                      parent=self.root)
        s["name"] = name.strip()
        if args and args.strip():
            s["args"] = args.strip()
        else:
            s.pop("args", None)
        self._c_render()

    def _c_prog_remove(self):
        from tkinter import messagebox
        i = self._c_prog_sel()
        if i is not None and messagebox.askyesno(
                "Remover programa", f'Remover "{self.c_vals["shortcuts"][i].get("name")}" da página Programas?',
                parent=self.root):
            del self.c_vals["shortcuts"][i]
            self._c_render()

    def _c_save(self):
        if self.c_vals is None:
            return
        self._c_delay_changed()
        changes = {k: self.c_vals[k] for k in self.CFG_KEYS if self.c_vals[k] != self.c_orig[k]}
        if not changes:
            self.c_msg.configure(text="Nada para salvar", fg=self.DIM)
            return
        r = self._call("config_save", changes)
        if r is None:
            return
        self._c_load()
        if r.get("restart"):
            self.c_msg.configure(text="Salvo. As pastas só mudam depois de reiniciar o servidor.", fg="#f1c40f")
            if not self.c_restart.winfo_ismapped():
                self.c_restart.pack(side="right")
        else:
            self.c_msg.configure(text="Salvo.", fg="#2ecc71")

    def _open_phone(self):
        err = self.app.open_on_phone()
        self.phone_msg.configure(text=err or "Abrindo no celular…", fg="#e74c3c" if err else self.DIM)

    def _refresh_phone(self, phone):
        links = phone.get("links") or {}
        if not phone.get("web"):
            self.phone_wifi.configure(text=f"App do celular desligado (porta {phone.get('port')} em uso?)")
        elif "wifi" in links:
            self.phone_wifi.configure(text="Wi-Fi: aponte a câmera do celular para o QR code\n"
                                           f"ou abra {links['wifi'].split('#')[0]} e digite o token.")
        else:
            self.phone_wifi.configure(text="Wi-Fi desativado (web.host = 127.0.0.1). Use o cabo.")
        if phone.get("adb"):
            self.phone_cable.configure(text="Cabo: celular conectado. Abra o app ou use o botão.", fg="#2ecc71")
            if not self.phone_btn.winfo_ismapped():
                self.phone_btn.pack(anchor="w", pady=(8, 0))
                self.phone_msg.pack(fill="x", pady=(4, 0))
        else:
            self.phone_cable.configure(text="Cabo: ligue o celular com 'Depuração USB' ativa "
                                            f"e abra http://localhost:{phone.get('port')}", fg=self.DIM)
            if self.phone_btn.winfo_ismapped():
                self.phone_btn.pack_forget()
                self.phone_msg.pack_forget()
        url = links.get("wifi")
        if url != self._qr_url:
            self._qr_url = url
            self._qr_img = None
            if url:
                try:
                    import qrcode
                    from PIL import ImageTk
                    q = qrcode.QRCode(border=2, box_size=4)
                    q.add_data(url)
                    self._qr_img = ImageTk.PhotoImage(q.make_image(fill_color="black", back_color="white").get_image())
                except Exception:  # noqa: BLE001  (qrcode não instalado)
                    log.info("qrcode indisponível - painel sem QR")
            self.qr.configure(image=self._qr_img or "")
            if not self._qr_img:
                self.qr.pack_forget()
            elif not self.qr.winfo_ismapped():
                self.qr.pack(side="left", padx=10, pady=10, before=self.qr.master.winfo_children()[1])

    def bring_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        ack_panel()  # confirma para o atalho que pediu o painel

    def refresh(self):
        try:
            while True:
                if self.app.panel_q.get_nowait() == "raise":
                    self.bring_to_front()
        except queue.Empty:
            pass

        st = self.app.status_fn()
        self.dot.itemconfigure(self.dot_id, fill=self.STATE_HEX[st["state"]])
        self.status.configure(text=st["text"])
        self.game.configure(text=f"Jogando: {st['game']}" if st.get("game") else "Nenhum jogo detectado")
        if "phone" in st:
            self._refresh_phone(st["phone"])

        try:
            mtime = self.app.log_path.stat().st_mtime
            if mtime != self._log_mtime:
                self._log_mtime = mtime
                with open(self.app.log_path, "rb") as fh:
                    fh.seek(0, 2)
                    fh.seek(max(0, fh.tell() - 8000))
                    lines = fh.read().decode("utf-8", "replace").splitlines()[-60:]
                lines = [ln.split(" ", 1)[1] if ln[:4].isdigit() else ln for ln in lines]  # tira a data
                self.log.configure(state="normal")
                self.log.delete("1.0", "end")
                self.log.insert("end", "\n".join(lines))
                self.log.see("end")
                self.log.configure(state="disabled")
        except OSError:
            pass
        # aba Trabalho aberta: acompanha o que o celular muda (job ativo, jobs, tempo)
        if self.app.ops and self._tab_is(self.work_tab):
            self._w_tick += 1
            if self._w_tick % 5 == 0:
                try:
                    self.w_data = self.app.ops["work_get"]()
                    if self._w_tick % 30 == 0:
                        self.w_week = self.app.ops["work_week"](self.w_off)
                    self._w_render()
                except Exception as e:  # noqa: BLE001
                    log.warning("painel trabalho: %s", type(e).__name__)
        self.root.after(1000, self.refresh)
