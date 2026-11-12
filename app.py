from __future__ import annotations

import os
import queue
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import core
import gta_pipeline
import toolchain

APP_TITLE = "NFS MW 2012 | GTA V Car Converter"
FONT = ("Tahoma", 10)
SMALL = ("Tahoma", 9)
TOOL_LABELS = {
    "blender_4": "Blender 4+ (برای Sollumz)",
    "blender_36": "Blender 3.6 (برای خروجی NFS)",
    "blender_34": "Blender 3.4 (مسیر قدیمی NFSMW12VMT)",
    "codewalker": "CodeWalker",
    "noesis": "Noesis",
    "seven_zip": "7-Zip",
    "nvcompress": "NVIDIA Texture Tools / nvcompress",
    "nfs_library": "کتابخانه منابع NFS (پوشه NFSMW_Library_PC)",
}
TOOL_FILTERS = {
    "blender_4": [("Blender", "blender.exe"), ("All files", "*.*")],
    "blender_36": [("Blender", "blender.exe"), ("All files", "*.*")],
    "blender_34": [("Blender", "blender.exe"), ("All files", "*.*")],
    "codewalker": [("CodeWalker", "CodeWalker.exe"), ("Executable", "*.exe")],
    "noesis": [("Noesis", "Noesis.exe"), ("Executable", "*.exe")],
    "seven_zip": [("7-Zip", "7z.exe"), ("Executable", "*.exe")],
    "nvcompress": [("nvcompress", "nvcompress.exe"), ("Executable", "*.exe")],
    "nfs_library": [],
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x820")
        self.minsize(900, 700)
        self.q: queue.Queue = queue.Queue()
        self.game = tk.StringVar(value=core.detect_game_dir())
        self.mod = tk.StringVar()
        self.mode = tk.StringVar(value="replace")
        self.gta_source = tk.StringVar()
        self.workspace = tk.StringVar()
        self.model_folder = tk.StringVar()
        self.output_bndl = tk.StringVar()
        self.target_vehicle = tk.StringVar()
        self.tool_paths: dict[str, tk.StringVar] = {}
        self.mods: list[dict] = []
        self.tool_info = {}
        self._build()
        self.refresh()
        self.refresh_vehicle_choices()
        self.refresh_tools()
        self.after(100, self._pump)

    def _row(self, parent, label, var, buttons=(), width=21):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", padx=10, pady=4)
        ttk.Label(frame, text=label, font=FONT, width=width, anchor="e").pack(side="right")
        for text, command in buttons:
            ttk.Button(frame, text=text, command=command).pack(side="left", padx=2)
        ttk.Entry(frame, textvariable=var, justify="left").pack(side="left", fill="x", expand=True, padx=4)
        return frame

    def _build(self):
        top = ttk.LabelFrame(self, text="مسیر بازی")
        top.pack(fill="x", padx=10, pady=(10, 4))
        self._row(top, "پوشه NFS MW 2012:", self.game,
                  [("انتخاب پوشه…", self.pick_game), ("بررسی خودروها", self.refresh_vehicle_choices)])

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=7)
        self.manager_tab = ttk.Frame(self.tabs)
        self.converter_tab = ttk.Frame(self.tabs)
        self.tools_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.converter_tab, text="تبدیل خودروهای GTA V")
        self.tabs.add(self.manager_tab, text="مدیریت مودهای NFS")
        self.tabs.add(self.tools_tab, text="ابزارهای موردنیاز")
        self._build_converter()
        self._build_manager()
        self._build_tools()

        status = ttk.LabelFrame(self, text="گزارش عملیات")
        status.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.log = tk.Text(status, height=9, font=("Consolas", 9), state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_converter(self):
        parent = self.converter_tab
        intro = ttk.LabelFrame(parent, text="۱) انتخاب مود GTA V و خودرو جایگزین")
        intro.pack(fill="x", padx=10, pady=8)
        self._row(intro, "پوشه / آرشیو مود:", self.gta_source,
                  [("پوشه…", self.pick_gta_dir), ("فایل…", self.pick_gta_archive)])
        row = ttk.Frame(intro)
        row.pack(fill="x", padx=10, pady=4)
        ttk.Label(row, text="خودروی مقصد در NFS:", font=FONT, width=21, anchor="e").pack(side="right")
        self.vehicle_combo = ttk.Combobox(row, textvariable=self.target_vehicle, state="readonly")
        self.vehicle_combo.pack(side="right", fill="x", expand=True, padx=4)
        ttk.Button(row, text="بازخوانی", command=self.refresh_vehicle_choices).pack(side="right", padx=2)
        actions = ttk.Frame(intro)
        actions.pack(fill="x", padx=10, pady=6)
        ttk.Button(actions, text="ساخت فضای تبدیل و استخراج مود", command=self.prepare_conversion).pack(side="right", padx=3)
        ttk.Button(actions, text="باز کردن فضای کاری", command=self.open_workspace).pack(side="right", padx=3)
        ttk.Button(actions, text="باز کردن راهنمای فارسی", command=self.open_guide).pack(side="right", padx=3)

        stage_a = ttk.LabelFrame(parent, text="۲) استخراج و واردکردن مدل GTA V")
        stage_a.pack(fill="x", padx=10, pady=8)
        self._row(stage_a, "فضای کاری فعلی:", self.workspace, [])
        ttk.Label(stage_a,
                  text="ابتدا با CodeWalker فایل YFT/RPF و تکسچرهای مربوط را به XML و تصاویر صادر کنید."
                       " سپس XML را با Sollumz در Blender 4+ وارد کنید. Sollumz فایل باینری خام YFT را مستقیماً وارد نمی‌کند.",
                  font=SMALL, justify="right", wraplength=950).pack(fill="x", padx=12, pady=(0, 6), anchor="e")
        row_a = ttk.Frame(stage_a); row_a.pack(fill="x", padx=10, pady=4)
        ttk.Button(row_a, text="اجرای CodeWalker", command=lambda: self.launch_tool("codewalker")).pack(side="right", padx=3)
        ttk.Button(row_a, text="اجرای Blender 4+", command=lambda: self.launch_tool("blender_4")).pack(side="right", padx=3)
        ttk.Button(row_a, text="باز کردن پوشه XML", command=self.open_codewalker_folder).pack(side="right", padx=3)
        ttk.Button(row_a, text="راهنمای Sollumz", command=lambda: webbrowser.open("https://docs.sollumz.org/support/faq")).pack(side="right", padx=3)

        stage_b = ttk.LabelFrame(parent, text="۳) بررسی بسته OBJ/MTL/DDS (مسیر قدیمی NFSMW12VMT)")
        stage_b.pack(fill="x", padx=10, pady=8)
        self._row(stage_b, "پوشه OBJ/MTL/DDS:", self.model_folder,
                  [("انتخاب پوشه…", self.pick_model_folder)])
        ttk.Button(stage_b, text="اعتبارسنجی بسته OBJ/MTL/DDS", command=self.validate_model).pack(anchor="e", padx=12, pady=5)
        ttk.Label(stage_b,
                  text="این بررسی مخصوص مسیر قدیمی NFSMW12VMT و بسته OBJ/MTL/DDS است. برای خروجی اصلی DGIorio،"
                       " مدل و متریال را در Blender 3.1 تا 3.6 با افزونه خودش صادر کنید. هندسه و اجزای خودرو باید با ساختار NFS تطبیق داده شوند.",
                  font=SMALL, justify="right", wraplength=950).pack(fill="x", padx=12, pady=(0, 7), anchor="e")
        row_b = ttk.Frame(stage_b); row_b.pack(fill="x", padx=10, pady=4)
        ttk.Button(row_b, text="اجرای Blender 3.6", command=lambda: self.launch_tool("blender_36")).pack(side="right", padx=3)
        ttk.Button(row_b, text="افزونه خروجی DGIorio", command=lambda: webbrowser.open("https://github.com/DGIorio/nfsmw_exporter")).pack(side="right", padx=3)
        ttk.Button(row_b, text="NFSMW12VMT قدیمی", command=lambda: webbrowser.open("https://github.com/NIVSAYZ/NFSMW12VMT")).pack(side="right", padx=3)

        stage_c = ttk.LabelFrame(parent, text="۴) نصب BNDL نهایی در بازی")
        stage_c.pack(fill="x", padx=10, pady=8)
        self._row(stage_c, "فایل BNDL خروجی:", self.output_bndl,
                  [("انتخاب BNDL…", self.pick_output_bndl)])
        ttk.Button(stage_c, text="نصب و جایگزینی خودرو", command=self.install_converted_vehicle).pack(anchor="e", padx=12, pady=6)
        ttk.Label(stage_c,
                  text="برنامه قبل از جایگزینی، فایل اصلی خودرو را داخل _ModManager\\backups ذخیره می‌کند."
                       " از زبانه مدیریت مود می‌توان خودرو را به حالت اصلی برگرداند.",
                  font=SMALL, justify="right", wraplength=950).pack(fill="x", padx=12, pady=(0, 7), anchor="e")

    def _build_manager(self):
        parent = self.manager_tab
        self._row(parent, "پوشه / ZIP مود:", self.mod,
                  [("پوشه…", self.pick_mod_dir), ("ZIP…", self.pick_mod_zip)])
        row = ttk.Frame(parent); row.pack(fill="x", padx=10, pady=4)
        ttk.Radiobutton(row, text="فقط فایل‌های جدید", variable=self.mode, value="add").pack(side="right", padx=7)
        ttk.Radiobutton(row, text="جایگزینی با بکاپ", variable=self.mode, value="replace").pack(side="right", padx=7)
        ttk.Button(parent, text="نصب مود معمولی NFS", command=self.install_regular).pack(anchor="e", padx=12, pady=6)
        ttk.Label(parent, text="مودهای نصب‌شده:", font=FONT, anchor="e").pack(fill="x", padx=10)
        self.lb = tk.Listbox(parent, height=14, font=FONT)
        self.lb.pack(fill="both", expand=True, padx=10, pady=5)
        ttk.Button(parent, text="حذف مود و بازگردانی فایل اصلی", command=self.uninstall).pack(anchor="e", padx=12, pady=6)

    def _build_tools(self):
        parent = self.tools_tab
        ttk.Label(parent,
                  text="مسیر ابزارها را خودکار پیدا می‌کنیم؛ در صورت پیدا نشدن، فایل اجرایی را دستی معرفی کنید.",
                  font=FONT, justify="right", wraplength=950).pack(fill="x", padx=12, pady=8, anchor="e")
        self.tool_rows = ttk.Frame(parent)
        self.tool_rows.pack(fill="both", expand=True, padx=8, pady=4)
        self.tool_status = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.tool_status, font=FONT, justify="right", wraplength=950).pack(fill="x", padx=12, pady=5, anchor="e")
        ttk.Button(parent, text="بررسی دوباره ابزارها", command=self.refresh_tools).pack(anchor="e", padx=12, pady=6)
        ttk.Label(parent,
                  text="نیازمندی‌های تبدیل: CodeWalker برای صادرکردن XML؛ Sollumz روی Blender 4+ برای واردکردن XML؛"
                       " Blender 3.1–3.6 و افزونه DGIorio برای مدل NFS؛ Noesis/Bundle Packer برای باز و بسته‌کردن BNDL."
                       " برای افزونه DGIorio پوشه NFSMW_Library_PC نیز لازم است. افزونه‌ها و ابزارهای بازی به‌صورت جداگانه نصب می‌شوند.",
                  font=SMALL, justify="right", wraplength=950).pack(fill="x", padx=12, pady=7, anchor="e")
        links = ttk.Frame(parent); links.pack(fill="x", padx=10, pady=7)
        ttk.Button(links, text="Noesis", command=lambda: self.launch_tool("noesis")).pack(side="right", padx=3)
        ttk.Button(links, text="Bundle Packer / Unpacker", command=lambda: webbrowser.open("https://github.com/DGIorio/bundle_packer_unpacker")).pack(side="right", padx=3)

    def refresh_tools(self):
        self.tool_info = toolchain.detect_tools()
        if not hasattr(self, "tool_rows"):
            return
        for child in self.tool_rows.winfo_children():
            child.destroy()
        for key, label in TOOL_LABELS.items():
            row = ttk.Frame(self.tool_rows); row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=38, anchor="e", font=SMALL).pack(side="right", padx=4)
            current = self.tool_info.get(key, {}).get("path", "")
            var = self.tool_paths.setdefault(key, tk.StringVar(value=current))
            var.set(current)
            ttk.Entry(row, textvariable=var, justify="left").pack(side="left", fill="x", expand=True, padx=4)
            ttk.Button(row, text="انتخاب…", command=lambda k=key: self.pick_tool(k)).pack(side="left", padx=2)
            ttk.Button(row, text="اجرا", command=lambda k=key: self.launch_tool(k)).pack(side="left", padx=2)
            found = self.tool_info.get(key, {}).get("found", False)
            ttk.Label(row, text="یافت شد" if found else "پیدا نشد",
                      foreground="#17823b" if found else "#a22", width=11).pack(side="left", padx=4)
        roles = toolchain.blender_role_status(self.tool_info)
        msg = ("Blender برای واردکردن GTA: آماده" if roles["gta_import"] else "Blender 4+ برای Sollumz پیدا نشد")
        msg += "  |  "
        msg += ("Blender برای خروجی NFS: آماده" if roles["nfs_export"] else "Blender 3.1 تا 3.6 برای خروجی NFS پیدا نشد")
        self.tool_status.set(msg)

    def pick_tool(self, key: str):
        if key == "nfs_library":
            path = filedialog.askdirectory(title=f"انتخاب {TOOL_LABELS[key]}")
        else:
            path = filedialog.askopenfilename(title=f"انتخاب {TOOL_LABELS[key]}", filetypes=TOOL_FILTERS[key])
        if not path:
            return
        try:
            toolchain.configure_tool(key, path)
        except Exception as exc:
            messagebox.showerror("خطا", str(exc)); return
        self.refresh_tools()
        self.write(f"مسیر {TOOL_LABELS[key]} ذخیره شد.")

    def launch_tool(self, key: str):
        info = self.tool_info.get(key, {})
        path = info.get("path") or self.tool_paths.get(key, tk.StringVar()).get()
        if key == "blender_36" and not path:
            path = self.tool_info.get("blender_34", {}).get("path", "")
        valid_path = Path(path).is_dir() if key == "nfs_library" and path else Path(path).is_file() if path else False
        if not path or not valid_path:
            messagebox.showwarning("ابزار پیدا نشد", f"ابتدا مسیر {TOOL_LABELS[key]} را در زبانه ابزارها معرفی کنید.")
            return
        try:
            os.startfile(path) if os.name == "nt" else webbrowser.open(Path(path).as_uri())
            self.write(f"اجرا شد: {TOOL_LABELS[key]}")
        except Exception as exc:
            messagebox.showerror("خطا در اجرا", str(exc))

    def write(self, message: str):
        self.q.put(("log", str(message)))

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "workspace":
                    self.workspace.set(payload["workspace"])
                    self._append_log(f"فضای کاری آماده شد: {payload['workspace']}")
                    scan = payload["scan"]
                    self._append_log(f"فایل‌های GTA: {len(scan['gta'])} | XML: {len(scan['xml'])} | مدل/تکسچر: {len(scan['models'])} | آرشیوهای داخلی: {len(scan['archives'])}")
                    if scan["gta"] and not scan["xml"]:
                        self._append_log("مرحله بعد: از CodeWalker خروجی XML بگیرید؛ Sollumz فایل خام YFT را وارد نمی‌کند.")
                    if scan["xml"]:
                        self._append_log("فایل XML پیدا شد؛ پس از بررسی ساختار و تکسچرها در Sollumz وارد Blender کنید.")
                    self._append_log(f"پوشه CodeWalker XML: {payload['codewalker_dir']}")
                    self._append_log(f"راهنمای کامل: {payload['guide']}")
                elif kind == "validation":
                    result = payload
                    self._append_log(f"نتیجه اعتبارسنجی: {'مجاز برای ادامه' if result['ok'] else 'خطا دارد'} | رأس: {result['vertices']:,} | Face: {result['faces']:,} | Mesh: {result['mesh_count']} | DDS: {result['dds_count']}")
                    for item in result["errors"]: self._append_log("خطا: " + item)
                    for item in result["warnings"]: self._append_log("هشدار: " + item)
                elif kind == "refresh":
                    self.refresh(); self.refresh_vehicle_choices()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _append_log(self, message: str):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pick_game(self):
        path = filedialog.askdirectory(title="پوشه نصب NFS Most Wanted 2012")
        if path:
            self.game.set(os.path.normpath(path))
            self.refresh(); self.refresh_vehicle_choices()

    def pick_mod_dir(self):
        path = filedialog.askdirectory(title="پوشه مود NFS")
        if path: self.mod.set(os.path.normpath(path))

    def pick_mod_zip(self):
        path = filedialog.askopenfilename(title="ZIP مود NFS", filetypes=[("ZIP", "*.zip")])
        if path: self.mod.set(os.path.normpath(path))

    def pick_gta_dir(self):
        path = filedialog.askdirectory(title="پوشه مود خودرو GTA V")
        if path: self.gta_source.set(os.path.normpath(path))

    def pick_gta_archive(self):
        path = filedialog.askopenfilename(title="آرشیو مود GTA V",
            filetypes=[("GTA V mod/archive", "*.zip *.rar *.7z *.rpf *.yft *.ytd *.ydr *.ydd"), ("All files", "*.*")])
        if path: self.gta_source.set(os.path.normpath(path))

    def pick_model_folder(self):
        path = filedialog.askdirectory(title="پوشه OBJ/MTL/DDS آماده‌شده برای NFS")
        if path: self.model_folder.set(os.path.normpath(path))

    def pick_output_bndl(self):
        path = filedialog.askopenfilename(title="فایل BNDL نهایی NFS",
                                          filetypes=[("NFS BNDL", "*.BNDL *.bndl")])
        if path: self.output_bndl.set(os.path.normpath(path))

    def refresh(self):
        if not hasattr(self, "lb"):
            return
        self.lb.delete(0, "end")
        folder = self.game.get()
        if folder and Path(folder).is_dir():
            try:
                self.mods = core.load_manifest(folder)["mods"]
            except Exception as exc:
                self.mods = []
                self.write(f"خطا در خواندن فهرست مودها: {exc}")
            for mod in self.mods:
                self.lb.insert("end", f"{mod.get('name', 'Mod')} — {mod.get('time', '?')} ({len(mod.get('added', []))} جدید / {len(mod.get('replaced', []))} جایگزین)")
        else:
            self.mods = []

    def refresh_vehicle_choices(self):
        folder = self.game.get()
        vehicles = gta_pipeline.list_vehicle_bundles(folder) if folder and Path(folder).is_dir() else []
        if hasattr(self, "vehicle_combo"):
            self.vehicle_combo["values"] = vehicles
            if self.target_vehicle.get() not in vehicles:
                self.target_vehicle.set(vehicles[0] if vehicles else "")
        if not vehicles:
            self.write("فایل BNDL خودرو در مسیر VEHICLES پیدا نشد؛ مسیر بازی را بررسی کنید.")

    def _game_ok(self) -> bool:
        folder = self.game.get()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("خطا", "پوشه بازی را انتخاب کنید."); return False
        if not core.is_game_dir(folder):
            if not messagebox.askyesno("هشدار", f"فایل {core.GAME_EXE} پیدا نشد. ادامه می‌دهید؟"):
                return False
        return True

    def _run(self, fn, *args):
        def worker():
            try:
                fn(*args, self.write)
            except PermissionError:
                self.write("خطای دسترسی؛ بازی و لانچر را ببندید و برنامه را با دسترسی مناسب اجرا کنید.")
            except Exception as exc:
                self.write(f"خطا: {exc}")
            self.q.put(("refresh", None))
        threading.Thread(target=worker, daemon=True).start()

    def install_regular(self):
        if not self._game_ok(): return
        source = self.mod.get()
        if not source or not Path(source).exists():
            messagebox.showerror("خطا", "پوشه یا فایل ZIP مود را انتخاب کنید."); return
        if Path(source).is_file() and Path(source).suffix.lower() != ".zip":
            messagebox.showerror("خطا", "برای نصب مود معمولی فعلاً ZIP یا پوشه را انتخاب کنید."); return
        self._run(core.install_mod, self.game.get(), source, self.mode.get())

    def uninstall(self):
        selection = self.lb.curselection()
        if not selection:
            messagebox.showinfo("راهنما", "ابتدا یک مود از فهرست انتخاب کنید."); return
        mod = self.mods[selection[0]]
        if not messagebox.askyesno("تأیید حذف", f"مود «{mod.get('name', '')}» حذف و فایل‌های پشتیبان بازگردانی شود؟"):
            return
        self._run(core.uninstall_mod, self.game.get(), mod["id"])

    def prepare_conversion(self):
        if not self._game_ok(): return
        source = self.gta_source.get()
        if not source or not Path(source).exists():
            messagebox.showerror("خطا", "مود GTA V را انتخاب کنید."); return
        if not self.target_vehicle.get():
            messagebox.showerror("خطا", "خودروی مقصد را انتخاب کنید."); return
        def worker():
            try:
                result = gta_pipeline.create_conversion_workspace(source, self.game.get(), self.target_vehicle.get(), self.write)
                self.q.put(("workspace", result))
            except Exception as exc:
                self.write(f"آماده‌سازی ناموفق بود: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def _open_path(self, path: str):
        if not path or not Path(path).exists():
            messagebox.showinfo("راهنما", "ابتدا فضای تبدیل را آماده کنید."); return
        try:
            os.startfile(path) if os.name == "nt" else webbrowser.open(Path(path).as_uri())
        except Exception as exc:
            messagebox.showerror("خطا", str(exc))

    def open_workspace(self):
        path = self.workspace.get()
        if path and Path(path).is_file(): path = str(Path(path).parent)
        self._open_path(path)

    def open_guide(self):
        if not self.workspace.get():
            messagebox.showinfo("راهنما", "ابتدا فضای تبدیل را آماده کنید."); return
        path = Path(self.workspace.get()) / "READ_ME_FA.txt"
        self._open_path(str(path))

    def open_codewalker_folder(self):
        if not self.workspace.get():
            messagebox.showinfo("راهنما", "ابتدا فضای تبدیل را آماده کنید."); return
        path = Path(self.workspace.get()) / "02_CodeWalker_XML_Export"
        self._open_path(str(path))

    def validate_model(self):
        folder = self.model_folder.get()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("خطا", "پوشه مدل را انتخاب کنید."); return
        def worker():
            try:
                result = gta_pipeline.validate_nfs_model_folder(folder)
                self.q.put(("validation", result))
            except Exception as exc:
                self.write(f"اعتبارسنجی انجام نشد: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def install_converted_vehicle(self):
        if not self._game_ok(): return
        source = self.output_bndl.get()
        target = self.target_vehicle.get()
        if not source or not Path(source).is_file():
            messagebox.showerror("خطا", "فایل BNDL نهایی را انتخاب کنید."); return
        if not target:
            messagebox.showerror("خطا", "خودروی مقصد را انتخاب کنید."); return
        if not messagebox.askyesno("تأیید نصب", f"خودروی {target} جایگزین شود؟\nفایل اصلی قبل از تغییر پشتیبان‌گیری خواهد شد."):
            return
        self._run(gta_pipeline.install_vehicle_bundle, self.game.get(), source, target)


if __name__ == "__main__":
    App().mainloop()
