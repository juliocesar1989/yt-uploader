"""Live Setup — prepara título, descrição, thumbnail, playlist etc. no YouTube e na Twitch."""
import copy
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from live_core import client_gen, runner, storage
from live_core.twitch_api import REDIRECT_URI, TwitchClient
from live_core.youtube_api import YouTubeClient

PRIVACY = {"Não alterar": "", "Pública": "public", "Não listada": "unlisted", "Privada": "private"}
PRIVACY_LABEL = {v: k for k, v in PRIVACY.items()}
NO_PLAYLIST = "(nenhuma)"
THUMB_COUNTER = {"{n} (episódio)": "n", "{s} (sessão)": "s"}
THUMB_COUNTER_LABEL = {v: k for k, v in THUMB_COUNTER.items()}
OK_COLOR = ("#1a7f37", "#4cc38a")
ERR_COLOR = ("#b42318", "#ff6b6b")
GRAY = ("gray40", "gray60")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("Live Setup — YouTube + Twitch")
        self.geometry("1180x800")
        self.minsize(1000, 700)

        self.settings = storage.load_settings()
        self.profiles = storage.load_profiles()
        if not self.profiles:
            self.profiles.append(storage.new_profile("Meu jogo"))
            storage.save_profiles(self.profiles)

        self.current = None
        self._ui_q = queue.Queue()
        self._preview_job = None
        self._settings_win = None
        self._client_gen = None
        self.job_running = False
        self.playlist_by_label = {}
        self.tw_results = {}

        self._build()
        self._prune_secrets()
        self.after(100, self._drain)
        last = next((p for p in self.profiles if p["id"] == self.settings.get("last_profile")), self.profiles[0])
        self._select_profile(last["id"], save_current=False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================= infraestrutura de threads =================
    def ui(self, fn):
        """Agenda fn para rodar na thread da interface."""
        self._ui_q.put(fn)

    def _drain(self):
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def run_bg(self, work, on_ok, on_err=None):
        def target():
            try:
                r = work()
            except Exception as e:  # noqa: BLE001
                self.ui(lambda e=e: (on_err or self._bg_error)(e))
            else:
                self.ui(lambda: on_ok(r))
        threading.Thread(target=target, daemon=True).start()

    def _bg_error(self, e):
        self.log(f"ERRO: {e}")
        messagebox.showerror("Erro", str(e), parent=self)

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def log_bg(self, msg):
        self.ui(lambda: self.log(msg))

    def _prune_secrets(self):
        """Mantém na pasta do programa só o client_secret em uso; os outros vão para a Lixeira."""
        try:
            removed = client_gen.prune_secrets(self.settings.get("youtube_client_secret", ""))
        except Exception as e:  # noqa: BLE001
            self.log(f"Não consegui limpar os client_secret antigos: {e}")
            return
        if removed:
            self.log(f"client_secret antigos enviados para a Lixeira: {', '.join(removed)}")

    def _yt(self):
        return YouTubeClient(self.settings.get("youtube_client_secret", ""))

    def _tw(self):
        return TwitchClient(self.settings.get("twitch_client_id", ""), self.settings.get("twitch_client_secret", ""))

    # ================= layout =================
    @staticmethod
    def _set_entry(entry, text):
        entry.delete(0, "end")
        entry.insert(0, text)

    @staticmethod
    def _set_text(box, text):
        box.delete("1.0", "end")
        box.insert("1.0", text)

    def _entry_row(self, parent, row, label, width=None):
        ctk.CTkLabel(parent, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        e = ctk.CTkEntry(parent, width=width or 200)
        e.grid(row=row, column=1, sticky="w" if width else "ew", padx=8, pady=6)
        return e

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- barra lateral: perfis ----
        side = ctk.CTkFrame(self, width=240, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(side, text="Perfis", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, padx=16, pady=(16, 8), sticky="w")
        self.profile_list = ctk.CTkScrollableFrame(side, width=210)
        self.profile_list.grid(row=1, column=0, sticky="nsew", padx=10)
        btns = ctk.CTkFrame(side, fg_color="transparent")
        btns.grid(row=2, column=0, pady=8)
        ctk.CTkButton(btns, text="Novo", width=66, command=self._new_profile).grid(row=0, column=0, padx=2)
        ctk.CTkButton(btns, text="Duplicar", width=66, command=self._dup_profile).grid(row=0, column=1, padx=2)
        ctk.CTkButton(btns, text="Excluir", width=66, fg_color="#8b2c2c", hover_color="#6e2222",
                      command=self._delete_profile).grid(row=0, column=2, padx=2)
        ctk.CTkButton(side, text="⚙  Contas e configurações", command=self._open_settings).grid(
            row=3, column=0, padx=10, pady=(4, 16), sticky="ew")

        # ---- área principal ----
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=3)
        main.grid_rowconfigure(2, weight=1)

        self.tabs = ctk.CTkTabview(main)
        self.tabs.grid(row=0, column=0, sticky="nsew")
        for name in ("Geral", "Fila de títulos", "YouTube", "Twitch"):
            self.tabs.add(name)
        self._build_general(self.tabs.tab("Geral"))
        self._build_queue(self.tabs.tab("Fila de títulos"))
        self._build_youtube(self.tabs.tab("YouTube"))
        self._build_twitch(self.tabs.tab("Twitch"))

        bar = ctk.CTkFrame(main)
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        bar.grid_columnconfigure(0, weight=1)
        info = ctk.CTkFrame(bar, fg_color="transparent")
        info.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        ctk.CTkLabel(info, text="Próxima live", font=ctk.CTkFont(weight="bold")).pack(anchor="w")
        self.preview_yt = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=620)
        self.preview_yt.pack(anchor="w")
        self.preview_tw = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=620)
        self.preview_tw.pack(anchor="w")
        self.preview_thumb = ctk.CTkLabel(info, text="", anchor="w", justify="left", wraplength=620)
        self.preview_thumb.pack(anchor="w")
        cnt = ctk.CTkFrame(info, fg_color="transparent")
        cnt.pack(anchor="w", pady=(2, 0))
        self.preview_src = ctk.CTkLabel(cnt, text="", anchor="w", text_color=GRAY)
        self.preview_src.pack(side="left")
        ctk.CTkButton(cnt, text="−1", width=40, height=24, fg_color="#555555", hover_color="#444444",
                      command=lambda: self._bump_counters(-1)).pack(side="left", padx=(12, 4))
        ctk.CTkButton(cnt, text="+1", width=40, height=24, fg_color="#555555", hover_color="#444444",
                      command=lambda: self._bump_counters(1)).pack(side="left")

        ctk.CTkButton(bar, text="Salvar perfil", width=120, command=self._save_current).grid(row=0, column=1, padx=4)
        self.start_btn = ctk.CTkButton(bar, text="▶  START", width=170, height=50,
                                       font=ctk.CTkFont(size=17, weight="bold"),
                                       fg_color="#1f8f4e", hover_color="#17733e", command=self._start)
        self.start_btn.grid(row=0, column=3, padx=(4, 12), pady=8)

        self.log_box = ctk.CTkTextbox(main, height=150, state="disabled", wrap="word")
        self.log_box.grid(row=2, column=0, sticky="nsew")

    def _build_general(self, t):
        t.grid_columnconfigure(1, weight=1)
        t.grid_rowconfigure(5, weight=1)
        self.e_name = self._entry_row(t, 0, "Nome do perfil / jogo")
        self.e_title = self._entry_row(t, 1, "Modelo de título")
        self.e_tw_title = self._entry_row(t, 2, "Título da Twitch (opcional)")
        ctk.CTkLabel(t, text="Próximos contadores", anchor="w").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        cf = ctk.CTkFrame(t, fg_color="transparent")
        cf.grid(row=3, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(cf, text="{n}").pack(side="left", padx=(0, 6))
        self.e_counter = ctk.CTkEntry(cf, width=80)
        self.e_counter.pack(side="left")
        ctk.CTkLabel(cf, text="{s}").pack(side="left", padx=(24, 6))
        self.e_counter2 = ctk.CTkEntry(cf, width=80)
        self.e_counter2.pack(side="left")
        ctk.CTkLabel(cf, text="(os dois sobem +1 a cada START bem-sucedido)", text_color=GRAY).pack(
            side="left", padx=12)
        ctk.CTkLabel(
            t, anchor="w", justify="left", text_color=GRAY,
            text="Variáveis: {n} e {s} = contadores · {n:2} / {s:3} = com zeros à esquerda (05, 007) · "
                 "{jogo} = nome do perfil · {data} = data de hoje.\n"
                 "Valem também na fila e na descrição. Título da Twitch vazio = usa o mesmo do YouTube.",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        ctk.CTkLabel(t, text="Descrição padrão", anchor="w").grid(row=5, column=0, sticky="nw", padx=8, pady=6)
        self.t_desc = ctk.CTkTextbox(t, wrap="word")
        self.t_desc.grid(row=5, column=1, sticky="nsew", padx=8, pady=6)
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        self.v_apply_yt = tk.BooleanVar(value=True)
        self.v_apply_tw = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(row, text="Aplicar no YouTube", variable=self.v_apply_yt,
                        command=self._schedule_preview).pack(side="left", padx=(0, 20))
        ctk.CTkCheckBox(row, text="Aplicar na Twitch", variable=self.v_apply_tw,
                        command=self._schedule_preview).pack(side="left")
        for w in (self.e_name, self.e_title, self.e_tw_title, self.e_counter, self.e_counter2, self.t_desc):
            w.bind("<KeyRelease>", self._schedule_preview)

    def _build_queue(self, t):
        t.grid_columnconfigure(1, weight=1)
        t.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            t, anchor="w", text_color=GRAY,
            text="Títulos prontos, usados em ordem (um por live). Quando a fila acaba, volta para o modelo do perfil.",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 6))

        left = ctk.CTkFrame(t)
        left.grid(row=1, column=0, sticky="nsw", padx=(8, 6), pady=4)
        left.grid_rowconfigure(0, weight=1)
        self.q_list = tk.Listbox(left, width=42, activestyle="none", bg="#2b2b2b", fg="#e6e6e6",
                                 selectbackground="#1f6aa5", highlightthickness=0, borderwidth=0,
                                 font=("Segoe UI", 10), exportselection=False)
        self.q_list.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=6, pady=6)
        self.q_list.bind("<<ListboxSelect>>", self._q_on_select)
        ctk.CTkButton(left, text="↑", width=40, command=lambda: self._q_move(-1)).grid(row=1, column=0, padx=3, pady=6)
        ctk.CTkButton(left, text="↓", width=40, command=lambda: self._q_move(1)).grid(row=1, column=1, padx=3, pady=6)
        ctk.CTkButton(left, text="Remover", width=80, command=self._q_remove).grid(row=1, column=2, padx=3, pady=6)
        ctk.CTkButton(left, text="Limpar fila", width=90, fg_color="#8b2c2c", hover_color="#6e2222",
                      command=self._q_clear).grid(row=1, column=3, padx=3, pady=6)

        right = ctk.CTkFrame(t)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 8), pady=4)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(right, text="Título", anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        self.q_title = ctk.CTkEntry(right)
        self.q_title.grid(row=1, column=0, sticky="ew", padx=8)
        ctk.CTkLabel(right, text="Descrição (vazio = usa a descrição padrão)", anchor="w").grid(
            row=2, column=0, sticky="w", padx=8, pady=(8, 2))
        self.q_desc = ctk.CTkTextbox(right, wrap="word")
        self.q_desc.grid(row=3, column=0, sticky="nsew", padx=8)
        rb = ctk.CTkFrame(right, fg_color="transparent")
        rb.grid(row=4, column=0, sticky="w", padx=8, pady=8)
        ctk.CTkButton(rb, text="Adicionar à fila", command=self._q_add).pack(side="left", padx=(0, 6))
        ctk.CTkButton(rb, text="Salvar alteração", command=self._q_update).pack(side="left", padx=6)
        ctk.CTkButton(rb, text="Novo item", fg_color="#555555", hover_color="#444444",
                      command=self._q_clear_editor).pack(side="left", padx=6)
        ctk.CTkButton(rb, text="Adicionar vários…", fg_color="#555555", hover_color="#444444",
                      command=self._q_bulk).pack(side="left", padx=6)

    def _build_youtube(self, t):
        t.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(t, text="Thumbnail", anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        f = ctk.CTkFrame(t, fg_color="transparent")
        f.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        f.grid_columnconfigure(0, weight=1)
        self.e_thumb = ctk.CTkEntry(f)
        self.e_thumb.grid(row=0, column=0, sticky="ew")
        self.e_thumb.bind("<KeyRelease>", self._schedule_preview)
        ctk.CTkButton(f, text="Escolher pasta…", width=120, command=self._pick_thumb_folder).grid(
            row=0, column=1, padx=(6, 0))
        ctk.CTkButton(f, text="Arquivo…", width=80, fg_color="#555555", hover_color="#444444",
                      command=self._pick_thumb).grid(row=0, column=2, padx=(6, 0))
        ctk.CTkButton(f, text="Tirar", width=60, fg_color="#555555", hover_color="#444444",
                      command=lambda: (self._set_entry(self.e_thumb, ""), self._refresh_preview())
                      ).grid(row=0, column=3, padx=(6, 0))
        tf = ctk.CTkFrame(t, fg_color="transparent")
        tf.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        self.thumb_holder = ctk.CTkFrame(tf, width=256, height=144)
        self.thumb_holder.grid(row=0, column=0, rowspan=3, sticky="nw")
        self.thumb_holder.grid_propagate(False)
        self.thumb_label = None
        self._thumb_shown = None
        ctk.CTkLabel(
            tf, anchor="nw", justify="left", text_color=GRAY, wraplength=430,
            text="Escolha a PASTA das thumbs: o programa pega sozinho o arquivo cujo nome termina "
                 "com o número da vez (ex.: \"Episódio 05.png\", \"ep5.jpg\"). Aceita JPG/PNG de até 50 MB.",
        ).grid(row=0, column=1, sticky="nw", padx=12)
        cf = ctk.CTkFrame(tf, fg_color="transparent")
        cf.grid(row=1, column=1, sticky="nw", padx=12, pady=(6, 0))
        ctk.CTkLabel(cf, text="Número no nome do arquivo segue:").pack(side="left")
        self.om_thumb_counter = ctk.CTkOptionMenu(cf, values=list(THUMB_COUNTER), width=170,
                                                  command=lambda _v: self._refresh_preview())
        self.om_thumb_counter.pack(side="left", padx=8)
        self.lbl_thumb_resolved = ctk.CTkLabel(tf, text="", anchor="nw", justify="left", wraplength=430)
        self.lbl_thumb_resolved.grid(row=2, column=1, sticky="nw", padx=12, pady=(8, 0))

        ctk.CTkLabel(t, text="Playlist", anchor="w").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        pf = ctk.CTkFrame(t, fg_color="transparent")
        pf.grid(row=2, column=1, sticky="w", padx=8, pady=6)
        self.om_playlist = ctk.CTkOptionMenu(pf, values=[NO_PLAYLIST], width=380, command=self._on_playlist_pick)
        self.om_playlist.pack(side="left")
        ctk.CTkButton(pf, text="Carregar playlists", width=140, command=self._load_playlists).pack(side="left", padx=6)

        self.e_yt_tags = self._entry_row(t, 3, "Tags (vírgula; vazio = mantém)")
        ctk.CTkLabel(t, text="Privacidade", anchor="w").grid(row=4, column=0, sticky="w", padx=8, pady=6)
        prf = ctk.CTkFrame(t, fg_color="transparent")
        prf.grid(row=4, column=1, sticky="w", padx=8, pady=6)
        self.om_privacy = ctk.CTkOptionMenu(prf, values=list(PRIVACY), width=150)
        self.om_privacy.pack(side="left")
        self.v_no_synth = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(prf, variable=self.v_no_synth,
                        text='"Conteúdo alterado ou sintético" = Não (sem IA)').pack(side="left", padx=20)
        hf = ctk.CTkFrame(t, fg_color="transparent")
        hf.grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))
        ctk.CTkButton(hf, text="Verificar live pendente", width=170,
                      command=self._check_upcoming).pack(side="left")
        ctk.CTkLabel(
            hf, anchor="w", text_color=GRAY,
            text="O START não cria nem inicia live: só atualiza a live pendente do Studio (a do OBS).",
        ).pack(side="left", padx=12)

    def _build_twitch(self, t):
        t.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(t, text="Categoria", anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        gf = ctk.CTkFrame(t, fg_color="transparent")
        gf.grid(row=0, column=1, sticky="w", padx=8, pady=6)
        self.lbl_tw_game = ctk.CTkLabel(gf, text="(não alterar)", font=ctk.CTkFont(weight="bold"))
        self.lbl_tw_game.pack(side="left")
        ctk.CTkButton(gf, text="Não alterar", width=100, fg_color="#555555", hover_color="#444444",
                      command=lambda: self._set_tw_game("", "")).pack(side="left", padx=12)

        ctk.CTkLabel(t, text="Buscar categoria", anchor="w").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        sf = ctk.CTkFrame(t, fg_color="transparent")
        sf.grid(row=1, column=1, sticky="w", padx=8, pady=6)
        self.e_tw_search = ctk.CTkEntry(sf, width=260, placeholder_text="ex.: Elden Ring")
        self.e_tw_search.pack(side="left")
        self.e_tw_search.bind("<Return>", lambda _e: self._search_tw_game())
        ctk.CTkButton(sf, text="Buscar", width=80, command=self._search_tw_game).pack(side="left", padx=6)
        self.om_tw_results = ctk.CTkOptionMenu(t, values=["(busque acima e escolha aqui)"], width=380,
                                               command=self._on_tw_game_pick)
        self.om_tw_results.grid(row=2, column=1, sticky="w", padx=8, pady=6)

        self.e_tw_tags = self._entry_row(t, 3, "Tags (separadas por vírgula)")
        ctk.CTkLabel(t, anchor="w", text_color=GRAY,
                     text="Máx. 10 tags, só letras/números, sem espaço, até 25 caracteres. Vazio = mantém as atuais.").grid(
            row=4, column=1, sticky="w", padx=8)
        ctk.CTkLabel(t, anchor="w", text_color=GRAY,
                     text="A Twitch é atualizada assim que você clica em START (não precisa estar ao vivo).").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(16, 4))

    # ================= perfis =================
    def _find(self, pid):
        return next((p for p in self.profiles if p["id"] == pid), None)

    def _render_profile_list(self):
        for w in self.profile_list.winfo_children():
            w.destroy()
        for p in self.profiles:
            sel = self.current is not None and p["id"] == self.current["id"]
            ctk.CTkButton(self.profile_list, text=p["name"] or "(sem nome)", anchor="w",
                          fg_color="#1f6aa5" if sel else "transparent", hover_color="#2a4d6e",
                          command=lambda pid=p["id"]: self._select_profile(pid)).pack(fill="x", pady=2)

    def _select_profile(self, pid, save_current=True):
        if save_current and self.current:
            self._form_to_profile()
            storage.save_profiles(self.profiles)
        self.current = self._find(pid)
        self.settings["last_profile"] = pid
        storage.save_settings(self.settings)
        self._profile_to_form()
        self._render_profile_list()
        self._refresh_preview()

    def _new_profile(self):
        if self.current:
            self._form_to_profile()
        p = storage.new_profile("Novo perfil")
        self.profiles.append(p)
        storage.save_profiles(self.profiles)
        self._select_profile(p["id"], save_current=False)
        self.tabs.set("Geral")
        self.e_name.focus_set()

    def _dup_profile(self):
        self._form_to_profile()
        p = copy.deepcopy(self.current)
        p["id"] = storage.new_profile()["id"]
        p["name"] = f"{self.current['name']} (cópia)"
        self.profiles.append(p)
        storage.save_profiles(self.profiles)
        self._select_profile(p["id"], save_current=False)

    def _delete_profile(self):
        if len(self.profiles) == 1:
            messagebox.showinfo("Excluir", "Precisa existir pelo menos um perfil.", parent=self)
            return
        if not messagebox.askyesno("Excluir", f"Excluir o perfil \"{self.current['name']}\"?", parent=self):
            return
        self.profiles.remove(self.current)
        storage.save_profiles(self.profiles)
        self.current = None
        self._select_profile(self.profiles[0]["id"], save_current=False)

    def _save_current(self):
        self._form_to_profile()
        storage.save_profiles(self.profiles)
        self._render_profile_list()
        self._refresh_preview()
        self.log(f"Perfil \"{self.current['name']}\" salvo.")

    def _form_to_profile(self):
        p = self.current
        if p is None:
            return
        p["name"] = self.e_name.get().strip()
        p["title_template"] = self.e_title.get()
        p["twitch_title_template"] = self.e_tw_title.get()
        for key, entry in (("counter", self.e_counter), ("counter2", self.e_counter2)):
            try:
                p[key] = int(entry.get().strip())
            except ValueError:
                pass
        p["description_template"] = self.t_desc.get("1.0", "end-1c")
        p["apply_youtube"] = bool(self.v_apply_yt.get())
        p["apply_twitch"] = bool(self.v_apply_tw.get())
        p["thumbnail"] = self.e_thumb.get().strip()
        p["thumb_counter"] = THUMB_COUNTER.get(self.om_thumb_counter.get(), "n")
        p["yt_tags"] = runner.parse_tags(self.e_yt_tags.get())
        p["yt_privacy"] = PRIVACY.get(self.om_privacy.get(), "")
        p["declare_no_synthetic"] = bool(self.v_no_synth.get())
        p["twitch_tags"] = runner.parse_tags(self.e_tw_tags.get())
        # playlist, categoria da Twitch e fila são atualizados direto no perfil

    def _show_counters(self, p):
        self._set_entry(self.e_counter, str(p["counter"]))
        self._set_entry(self.e_counter2, str(p["counter2"]))

    def _bump_counters(self, delta):
        """Botões −1/+1: mudam {n} e {s} juntos, só para conferir título e thumbnail (não chama APIs)."""
        if self.job_running or not self.current:
            return
        self._form_to_profile()
        p = self.current
        n, s = int(p["counter"]) + delta, int(p["counter2"]) + delta
        if n < 0 or s < 0:  # não deixa negativo e mantém os dois andando juntos
            self.bell()
            return
        p["counter"], p["counter2"] = n, s
        self._show_counters(p)
        storage.save_profiles(self.profiles)
        self._refresh_preview()

    def _profile_to_form(self):
        p = self.current
        self._set_entry(self.e_name, p["name"])
        self._set_entry(self.e_title, p["title_template"])
        self._set_entry(self.e_tw_title, p["twitch_title_template"])
        self._show_counters(p)
        self._set_text(self.t_desc, p["description_template"])
        self.v_apply_yt.set(p["apply_youtube"])
        self.v_apply_tw.set(p["apply_twitch"])
        self._set_entry(self.e_thumb, p["thumbnail"])
        self.om_thumb_counter.set(THUMB_COUNTER_LABEL.get(p.get("thumb_counter", "n"), "{n} (episódio)"))
        self._set_entry(self.e_yt_tags, ", ".join(p["yt_tags"]))
        self.om_privacy.set(PRIVACY_LABEL.get(p["yt_privacy"], "Não alterar"))
        self.v_no_synth.set(p["declare_no_synthetic"])
        self._show_profile_playlist()
        self.lbl_tw_game.configure(text=p["twitch_game_name"] or "(não alterar)")
        self._set_entry(self.e_tw_tags, ", ".join(p["twitch_tags"]))
        self._refresh_queue_list()
        self._q_clear_editor()

    # ================= prévia =================
    def _schedule_preview(self, *_):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(300, self._refresh_preview)

    def _refresh_preview(self):
        self._preview_job = None
        if not self.current:
            return
        self._form_to_profile()
        p = self.current
        tx = runner.next_texts(p)
        self.preview_yt.configure(text=f"YouTube: {tx['title']}" if p["apply_youtube"] else "YouTube: (desligado)")
        self.preview_tw.configure(text=f"Twitch:  {tx['twitch_title']}" if p["apply_twitch"] else "Twitch:  (desligado)")
        # 1ª verificação da thumbnail: na tela, antes do START (a 2ª é no próprio START)
        self._update_thumb_preview(tx["thumbnail"], tx["thumbnail_error"])
        if p["apply_youtube"]:
            text, color = self._thumb_status(tx["thumbnail"], tx["thumbnail_error"])
            self.preview_thumb.configure(text=text, text_color=color)
        else:
            self.preview_thumb.configure(text="", text_color=GRAY)
        src = f"da fila (1 de {len(p['queue'])})" if tx["from_queue"] else "do modelo do perfil"
        text = f"{{n}} = {tx['n']} · {{s}} = {tx['s']} · título {src}"
        self.preview_src.configure(text=text)

    # ================= fila =================
    def _refresh_queue_list(self, select=None):
        self.q_list.delete(0, "end")
        for i, it in enumerate(self.current["queue"]):
            mark = "   • descrição própria" if it.get("description") else ""
            self.q_list.insert("end", f"{i + 1}. {it['title']}{mark}")
        if select is not None and 0 <= select < self.q_list.size():
            self.q_list.selection_set(select)
            self.q_list.see(select)

    def _q_selected(self):
        sel = self.q_list.curselection()
        return sel[0] if sel else None

    def _q_on_select(self, _e=None):
        i = self._q_selected()
        if i is None:
            return
        it = self.current["queue"][i]
        self._set_entry(self.q_title, it["title"])
        self._set_text(self.q_desc, it.get("description", ""))

    def _q_editor_item(self):
        title = self.q_title.get().strip()
        if not title:
            messagebox.showwarning("Fila", "Digite um título.", parent=self)
            return None
        return {"title": title, "description": self.q_desc.get("1.0", "end-1c").strip()}

    def _q_add(self):
        it = self._q_editor_item()
        if it:
            self.current["queue"].append(it)
            self._refresh_queue_list()
            self._q_clear_editor()
            self._refresh_preview()

    def _q_update(self):
        i = self._q_selected()
        if i is None:
            messagebox.showinfo("Fila", "Selecione um item da lista para alterar.", parent=self)
            return
        it = self._q_editor_item()
        if it:
            self.current["queue"][i] = it
            self._refresh_queue_list(select=i)
            self._refresh_preview()

    def _q_clear_editor(self):
        self.q_list.selection_clear(0, "end")
        self._set_entry(self.q_title, "")
        self._set_text(self.q_desc, "")

    def _q_remove(self):
        i = self._q_selected()
        if i is not None:
            self.current["queue"].pop(i)
            self._refresh_queue_list(select=min(i, len(self.current["queue"]) - 1))
            self._refresh_preview()

    def _q_move(self, delta):
        i = self._q_selected()
        q = self.current["queue"]
        if i is None or not 0 <= i + delta < len(q):
            return
        q[i], q[i + delta] = q[i + delta], q[i]
        self._refresh_queue_list(select=i + delta)
        self._refresh_preview()

    def _q_clear(self):
        if self.current["queue"] and messagebox.askyesno("Fila", "Apagar todos os itens da fila?", parent=self):
            self.current["queue"].clear()
            self._refresh_queue_list()
            self._refresh_preview()

    def _q_bulk(self):
        w = ctk.CTkToplevel(self)
        w.title("Adicionar vários títulos")
        w.geometry("600x420")
        w.transient(self)
        ctk.CTkLabel(w, text="Um título por linha. Todos usam a descrição padrão (dá para editar depois).").pack(
            anchor="w", padx=12, pady=(12, 4))
        tb = ctk.CTkTextbox(w, wrap="none")
        tb.pack(fill="both", expand=True, padx=12)

        def ok():
            lines = [ln.strip() for ln in tb.get("1.0", "end-1c").splitlines() if ln.strip()]
            self.current["queue"].extend({"title": ln, "description": ""} for ln in lines)
            self._refresh_queue_list()
            self._refresh_preview()
            w.destroy()

        ctk.CTkButton(w, text="Adicionar", command=ok).pack(pady=12)
        w.after(150, lambda: (w.lift(), tb.focus_set()))

    # ================= YouTube =================
    def _pick_thumb(self):
        path = filedialog.askopenfilename(parent=self, title="Escolher thumbnail",
                                          filetypes=[("Imagens", "*.jpg *.jpeg *.png"), ("Todos", "*.*")])
        if path:
            self._set_entry(self.e_thumb, str(Path(path)))
            self._refresh_preview()

    def _pick_thumb_folder(self):
        path = filedialog.askdirectory(parent=self, title="Pasta das thumbnails")
        if path:
            self._set_entry(self.e_thumb, str(Path(path)))
            self._refresh_preview()

    @staticmethod
    def _thumb_status(path, err):
        """Texto e cor do aviso de thumbnail (usado na aba YouTube e na barra de baixo)."""
        if not path and not err:
            return "Thumbnail: não será alterada (campo vazio)", GRAY
        name = Path(path).name if path else ""
        if err:
            return f"Thumbnail ✖ {name + ': ' if name else ''}{err}", ERR_COLOR
        kb = Path(path).stat().st_size / 1024
        return f"Thumbnail ✔ {name} ({kb:.0f} KB)", OK_COLOR

    def _update_thumb_preview(self, path, err=None):
        """Mostra a thumb que o START vai enviar, já verificada (extensão e tamanho)."""
        text, color = self._thumb_status(path, err)
        if path:
            text += f"\n{path}"
        self.lbl_thumb_resolved.configure(text=text, text_color=color)
        exists = bool(path) and Path(path).is_file()
        key = (path, exists, Path(path).stat().st_mtime if exists else None)
        if key == self._thumb_shown:
            return
        self._thumb_shown = key
        if self.thumb_label is not None:
            self.thumb_label.destroy()
        kwargs = {"text": "(sem thumbnail)" if not err else "(nenhuma imagem)"}
        if path:
            if exists:
                try:
                    img = Image.open(path)
                    img.thumbnail((256, 144))
                    kwargs = {"text": "", "image": ctk.CTkImage(light_image=img, dark_image=img, size=img.size)}
                except Exception as e:  # noqa: BLE001
                    kwargs = {"text": f"Não abriu a imagem: {e}"}
            else:
                kwargs = {"text": "(arquivo não encontrado)"}
        self.thumb_label = ctk.CTkLabel(self.thumb_holder, **kwargs)
        self.thumb_label.place(relx=0.5, rely=0.5, anchor="center")

    def _check_upcoming(self):
        if not storage.YOUTUBE_TOKEN_FILE.exists():
            messagebox.showinfo("YouTube", "Conecte o YouTube em Contas e configurações.", parent=self)
            return
        self.log("YouTube: procurando lives pendentes...")
        stream_id = self.settings.get("stream_id", "")

        def done(items):
            if not items:
                self.log("YouTube: nenhuma live pendente. Abra o YouTube Studio → Criar → Transmitir ao vivo "
                         "(aba Transmitir) para o YouTube preparar a próxima live.")
                return
            self.log(f"YouTube: {len(items)} live(s) pendente(s); o START atualiza a marcada com →")
            for line in runner.describe_upcoming(items, stream_id):
                self.log("   " + line)

        self.run_bg(lambda: self._yt().list_upcoming(), done)

    def _show_profile_playlist(self):
        p = self.current
        values = [NO_PLAYLIST] + list(self.playlist_by_label)
        label = NO_PLAYLIST
        if p["yt_playlist_id"]:
            label = next((lb for lb, pid in self.playlist_by_label.items() if pid == p["yt_playlist_id"]),
                         p["yt_playlist_name"] or p["yt_playlist_id"])
            if label not in values:
                values.append(label)
        self.om_playlist.configure(values=values)
        self.om_playlist.set(label)

    def _on_playlist_pick(self, label):
        if label == NO_PLAYLIST:
            self.current["yt_playlist_id"] = ""
            self.current["yt_playlist_name"] = ""
        elif label in self.playlist_by_label:
            self.current["yt_playlist_id"] = self.playlist_by_label[label]
            self.current["yt_playlist_name"] = label

    def _load_playlists(self):
        self.log("YouTube: carregando playlists...")

        def done(items):
            self.playlist_by_label = {}
            for pid, title in items:
                label = title if title not in self.playlist_by_label else f"{title} ({pid})"
                self.playlist_by_label[label] = pid
            self._show_profile_playlist()
            self.log(f"YouTube: {len(items)} playlists carregadas.")

        self.run_bg(lambda: self._yt().list_playlists(), done)

    # ================= Twitch =================
    def _set_tw_game(self, gid, name):
        self.current["twitch_game_id"] = gid
        self.current["twitch_game_name"] = name
        self.lbl_tw_game.configure(text=name or "(não alterar)")

    def _search_tw_game(self):
        q = self.e_tw_search.get().strip()
        if not q:
            return

        def done(items):
            self.tw_results = {name: gid for gid, name in items}
            values = list(self.tw_results) or ["(nada encontrado)"]
            self.om_tw_results.configure(values=values)
            self.om_tw_results.set("(escolha aqui)" if items else values[0])

        self.run_bg(lambda: self._tw().search_categories(q), done)

    def _on_tw_game_pick(self, name):
        if name in self.tw_results:
            self._set_tw_game(self.tw_results[name], name)

    # ================= START =================
    def _set_running(self, running):
        self.job_running = running
        self.start_btn.configure(state="disabled" if running else "normal",
                                 text="Preparando…" if running else "▶  START")

    def _account_errors(self, p):
        errs = []
        if p["apply_youtube"]:
            if not storage.YOUTUBE_TOKEN_FILE.exists():
                errs.append("Conecte o YouTube em Contas e configurações.")
        if p["apply_twitch"] and not storage.TWITCH_TOKEN_FILE.exists():
            errs.append("Conecte a Twitch em Contas e configurações.")
        return errs

    def _start(self):
        if self.job_running:
            return
        self._form_to_profile()
        storage.save_profiles(self.profiles)
        self._render_profile_list()
        p = self.current
        errs = runner.validate(runner.next_texts(p), p) + self._account_errors(p)
        if errs:
            messagebox.showerror("Não dá para iniciar", "\n".join(errs), parent=self)
            return
        self._set_running(True)
        self.log(f"===== START · {p['name']} =====")
        self._run_start(p["id"])

    def _start_failed(self, e):
        self._set_running(False)
        self._bg_error(e)

    def _run_start(self, pid):
        prof = self._find(pid)
        tx = runner.next_texts(prof)
        self.log(f"{{n}}={tx['n']} {{s}}={tx['s']}: {tx['title']}")
        snapshot = copy.deepcopy(prof)
        yt = self._yt() if prof["apply_youtube"] else None
        tw = self._tw() if prof["apply_twitch"] else None
        stream_id = self.settings.get("stream_id", "")

        def work():
            return runner.run_job(snapshot, tx, yt, tw, stream_id, self.log_bg)

        self.run_bg(work, lambda res: self._job_done(pid, tx, res), self._start_failed)

    def _job_done(self, pid, tx, res):
        """Depois da rotina de atualização: se tudo deu certo, incrementa {n}/{s} na hora."""
        self._set_running(False)
        prof = self._find(pid)
        if prof is None:
            return
        if res["failed"]:
            self.log(f"✖ Falhou: {', '.join(res['failed'])}. Contadores NÃO foram incrementados — "
                     "corrija e clique START de novo.")
            self._refresh_preview()
            return
        if self.current is prof:
            self._form_to_profile()
        runner.consume(prof, tx)
        storage.save_profiles(self.profiles)
        if self.current is prof:
            self._show_counters(prof)
            self._refresh_queue_list()
        self.log(f"Tudo pronto! Contadores incrementados → próximo START usa {{n}}={prof['counter']} "
                 f"{{s}}={prof['counter2']}.")
        if prof["apply_youtube"]:
            self.log("Agora é só iniciar a transmissão no OBS.")
        self.bell()
        self._refresh_preview()

    # ================= contas =================
    def _open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return
        w = ctk.CTkToplevel(self)
        self._settings_win = w
        w.title("Contas e configurações")
        w.geometry("760x680")
        w.transient(self)
        w.grid_columnconfigure(1, weight=1)
        bold = ctk.CTkFont(size=15, weight="bold")

        def section(row, text):
            ctk.CTkLabel(w, text=text, font=bold).grid(row=row, column=0, columnspan=3, sticky="w", padx=12,
                                                       pady=(14, 4))

        def entry(row, label, value, **kw):
            ctk.CTkLabel(w, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=12, pady=4)
            e = ctk.CTkEntry(w, **kw)
            e.insert(0, value)
            e.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
            return e

        section(0, "YouTube")
        e_cs = entry(1, "client_secret.json", self.settings.get("youtube_client_secret", ""))

        def pick_cs():
            path = filedialog.askopenfilename(parent=w, title="client_secret.json",
                                              filetypes=[("JSON", "*.json")])
            if path:
                self._set_entry(e_cs, path)

        ctk.CTkButton(w, text="Escolher…", width=90, command=pick_cs).grid(row=1, column=2, padx=(0, 12))
        yt_status = ctk.CTkLabel(w, anchor="w", text_color=GRAY,
                                 text="Conectado" if storage.YOUTUBE_TOKEN_FILE.exists() else "Não conectado")
        yt_status.grid(row=2, column=1, sticky="w", padx=6)

        # --- gerar o client_secret pela extensão yt-client-gen ---
        e_proj = entry(3, "Projeto Google Cloud",
                       self.settings.get("gcp_project")
                       or client_gen.project_id_from_secret(self.settings.get("youtube_client_secret", "")),
                       placeholder_text="ID do projeto, ex.: meu-projeto-123")
        profiles = client_gen.list_profiles()
        prof_labels = {f"{name} ({d})": d for d, name in profiles}
        prof_by_dir = {d: lb for lb, d in prof_labels.items()}
        ctk.CTkLabel(w, text="Perfil do Chrome", anchor="w").grid(row=4, column=0, sticky="w", padx=12, pady=4)
        pf = ctk.CTkFrame(w, fg_color="transparent")
        pf.grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=4)
        om_chrome = ctk.CTkOptionMenu(pf, values=list(prof_labels) or ["(nenhum perfil encontrado)"], width=300)
        om_chrome.set(prof_by_dir.get(self.settings.get("chrome_profile"), "(procurando a extensão…)"))
        om_chrome.pack(side="left")
        chrome_hint = ctk.CTkLabel(pf, text="", text_color=GRAY)
        chrome_hint.pack(side="left", padx=8)
        gen_status = ctk.CTkLabel(w, anchor="w", justify="left", text_color=GRAY, wraplength=420, text="")
        gen_status.grid(row=5, column=1, sticky="w", padx=6)

        def detected(dirs):
            if not chrome_hint.winfo_exists():
                return
            if not dirs:
                chrome_hint.configure(text="extensão não encontrada em nenhum perfil", text_color=ERR_COLOR)
                if om_chrome.get() not in prof_labels:
                    om_chrome.set("(escolha o perfil)")
                return
            if om_chrome.get() not in prof_labels and dirs[0] in prof_by_dir:
                om_chrome.set(prof_by_dir[dirs[0]])
            if prof_labels.get(om_chrome.get()) in dirs:
                chrome_hint.configure(text="✔ extensão instalada", text_color=OK_COLOR)
            else:
                where = ", ".join(prof_by_dir.get(d, d) for d in dirs)
                chrome_hint.configure(text=f"a extensão está em: {where}", text_color=ERR_COLOR)

        self.run_bg(client_gen.detect_extension_profiles, detected, lambda e: detected([]))

        def gen_secret():
            project = e_proj.get().strip()
            if not project:
                e_proj.focus_set()
                gen_status.configure(text="Informe o ID do projeto do Google Cloud.", text_color=ERR_COLOR)
                return
            chrome_dir = prof_labels.get(om_chrome.get())
            if not chrome_dir:
                gen_status.configure(text="Escolha o perfil do Chrome onde a extensão está instalada.",
                                     text_color=ERR_COLOR)
                return
            apply_settings()
            try:
                self._start_client_gen(project, chrome_dir, gen_status, gen_btn, e_cs)
            except Exception as e:  # noqa: BLE001
                gen_status.configure(text=str(e), text_color=ERR_COLOR)
                gen_btn.configure(state="normal")

        gen_btn = ctk.CTkButton(w, text="Gerar automaticamente", width=90, command=gen_secret)
        gen_btn.grid(row=3, column=2, padx=(0, 12), pady=4)
        if self._client_gen is not None and self._client_gen.running:
            gen_btn.configure(state="disabled")
            gen_status.configure(text="Geração em andamento no Chrome…")

        section(6, "Twitch")
        e_id = entry(7, "Client ID", self.settings.get("twitch_client_id", ""))
        e_sec = entry(8, "Client Secret", self.settings.get("twitch_client_secret", ""), show="•")
        e_redir = entry(9, "OAuth Redirect URL", REDIRECT_URI)
        e_redir.configure(state="readonly")
        ctk.CTkLabel(w, text="(cadastre exatamente essa URL no app da Twitch)", text_color=GRAY).grid(
            row=10, column=1, sticky="w", padx=6)
        tok = storage.read_json(storage.TWITCH_TOKEN_FILE, None)
        tw_status = ctk.CTkLabel(w, anchor="w", text_color=GRAY,
                                 text=f"Conectado: {tok.get('login')}" if tok else "Não conectado")
        tw_status.grid(row=11, column=1, sticky="w", padx=6)

        section(12, "Chave do OBS no YouTube (opcional: escolhe a live certa se houver várias pendentes)")
        streams = {}
        cur_label = self.settings.get("stream_label") or "(clique em Carregar chaves)"
        ctk.CTkLabel(w, text="Chave", anchor="w").grid(row=13, column=0, sticky="w", padx=12, pady=4)
        om_stream = ctk.CTkOptionMenu(w, values=[cur_label], width=360)
        om_stream.set(cur_label)
        om_stream.grid(row=13, column=1, sticky="w", padx=6, pady=4)

        def load_streams():
            def done(items):
                streams.clear()
                for sid, title, tail in items:
                    label = f"{title} (…{tail})" if tail else title
                    if label in streams:
                        label = f"{label} [{sid[-6:]}]"
                    streams[label] = sid
                values = list(streams) or ["(nenhuma chave encontrada)"]
                om_stream.configure(values=values)
                current = next((lb for lb, sid in streams.items() if sid == self.settings.get("stream_id")), None)
                om_stream.set(current or (values[0] if len(values) == 1 else "(escolha a chave)"))

            self.run_bg(lambda: self._yt().list_streams(), done)

        ctk.CTkButton(w, text="Carregar chaves", command=load_streams).grid(row=13, column=2, padx=(0, 12), pady=4)

        def apply_settings():
            self.settings["youtube_client_secret"] = e_cs.get().strip()
            self.settings["gcp_project"] = e_proj.get().strip()
            if om_chrome.get() in prof_labels:
                self.settings["chrome_profile"] = prof_labels[om_chrome.get()]
            self.settings["twitch_client_id"] = e_id.get().strip()
            self.settings["twitch_client_secret"] = e_sec.get().strip()
            label = om_stream.get()
            if label in streams:
                self.settings["stream_id"] = streams[label]
                self.settings["stream_label"] = label
            storage.save_settings(self.settings)
            self._prune_secrets()

        def connect_yt():
            apply_settings()
            yt_status.configure(text="Aguardando login no navegador…")

            def err(e):
                yt_status.configure(text="Não conectado")
                self._bg_error(e)

            self.run_bg(lambda: self._yt().login(),
                        lambda name: (yt_status.configure(text=f"Conectado: {name}"),
                                      self.log(f"YouTube conectado: {name}")), err)

        def connect_tw():
            apply_settings()
            tw_status.configure(text="Aguardando login no navegador…")

            def err(e):
                tw_status.configure(text="Não conectado")
                self._bg_error(e)

            self.run_bg(lambda: self._tw().login(),
                        lambda name: (tw_status.configure(text=f"Conectado: {name}"),
                                      self.log(f"Twitch conectada: {name}")), err)

        ctk.CTkButton(w, text="Conectar YouTube", command=connect_yt).grid(row=2, column=2, padx=(0, 12), pady=4)
        ctk.CTkButton(w, text="Conectar Twitch", command=connect_tw).grid(row=11, column=2, padx=(0, 12), pady=4)
        ctk.CTkLabel(w, text="Como criar as credenciais: veja o arquivo README.md na pasta do programa.",
                     text_color=GRAY).grid(row=14, column=0, columnspan=3, sticky="w", padx=12, pady=(16, 4))
        ctk.CTkButton(w, text="Salvar e fechar", command=lambda: (apply_settings(), w.destroy())).grid(
            row=15, column=0, columnspan=3, pady=12)
        w.after(150, w.lift)

    def _start_client_gen(self, project, chrome_dir, status_lbl, btn, e_cs):
        """Abre o Chrome no perfil da extensão yt-client-gen e espera o client_secret voltar.

        O servidor vive no App (não na janela de configurações): se a janela for fechada no meio,
        o resultado ainda é aplicado e aparece no log.
        """
        if self._client_gen is not None and self._client_gen.running:
            raise client_gen.ClientGenError("Já existe uma geração em andamento no Chrome.")

        def alive(widget):
            try:
                return widget.winfo_exists()
            except tk.TclError:
                return False

        def show(text, color=GRAY):
            if alive(status_lbl):
                status_lbl.configure(text=text, text_color=color)

        def on_status(msg, error):
            self.ui(lambda: (show(msg, ERR_COLOR if error else GRAY), self.log(f"client_secret: {msg}")))

        def on_done(path):
            def apply():
                self.settings["youtube_client_secret"] = str(path)
                storage.save_settings(self.settings)
                if alive(e_cs):
                    self._set_entry(e_cs, str(path))
                if alive(btn):
                    btn.configure(state="normal")
                show(f"✔ Salvo em {path.name}. Agora clique em Conectar YouTube.", OK_COLOR)
                self.log(f"client_secret gerado e movido para {path}")
            self.ui(apply)

        def on_error(e):
            def apply():
                if alive(btn):
                    btn.configure(state="normal")
                show(str(e), ERR_COLOR)
                self.log(f"client_secret: ERRO: {e}")
            self.ui(apply)

        btn.configure(state="disabled")
        show("Abrindo o Chrome…")
        bridge = client_gen.ClientGenBridge(on_status, on_done, on_error)
        bridge.start(project, "YouTube Desktop", chrome_dir)
        self._client_gen = bridge
        self.log(f"Gerando client_secret do projeto {project} pelo Chrome ({chrome_dir})…")

    # ================= fechar =================
    def _on_close(self):
        if self.job_running and not messagebox.askyesno(
                "Sair", "Ainda está preparando a live. Sair mesmo assim?", parent=self):
            return
        if self._client_gen is not None:
            self._client_gen.stop()
        self._form_to_profile()
        storage.save_profiles(self.profiles)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
