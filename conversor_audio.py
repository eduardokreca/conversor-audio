import json
import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

WAV_EXTS = {".wav"}
MP3_EXTS = {".mp3"}
AUDIO_EXTS = WAV_EXTS | MP3_EXTS

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".conversor_audio_config.json")

MP3_BITRATES = ["128", "192", "256", "320"]
WAV_BITDEPTHS = ["16", "24", "32"]
WAV_SAMPLERATES = ["44100", "48000", "96000"]
WAV_CODEC_BY_DEPTH = {"16": "pcm_s16le", "24": "pcm_s24le", "32": "pcm_f32le"}

DEFAULT_CONFIG = {"mp3_bitrate": "320", "wav_bitdepth": "24", "wav_samplerate": "48000"}


def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return config


def save_config(config):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f)
    except OSError:
        pass


IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
DEFAULT_MP3_ENCODER = "mp3_mf" if IS_WINDOWS else "libmp3lame"


def resource_path(filename):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def ffmpeg_path():
    ffmpeg_name = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
    local = resource_path(ffmpeg_name)
    if os.path.isfile(local):
        return local
    return ffmpeg_name


class ConversorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Conversor WAV ⇄ MP3")
        self.root.geometry("640x540")
        self.root.minsize(560, 460)
        try:
            if IS_WINDOWS:
                self.root.iconbitmap(resource_path("app_icon.ico"))
            else:
                icon_img = tk.PhotoImage(file=resource_path("app_icon.png"))
                self.root.iconphoto(True, icon_img)
        except (tk.TclError, FileNotFoundError):
            pass

        self.output_dir = tk.StringVar(value="")
        self.mode = tk.StringVar(value="auto")

        config = load_config()
        self.mp3_bitrate = tk.StringVar(value=config["mp3_bitrate"])
        self.wav_bitdepth = tk.StringVar(value=config["wav_bitdepth"])
        self.wav_samplerate = tk.StringVar(value=config["wav_samplerate"])

        self._build_ui()
        self.file_queue = []

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Pasta de saída:").pack(side="left")
        self.output_entry = ttk.Entry(top, textvariable=self.output_dir)
        self.output_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Escolher...", command=self.choose_output).pack(side="left")

        mode_frame = ttk.Frame(self.root, padding=(10, 0))
        mode_frame.pack(fill="x")
        ttk.Label(mode_frame, text="Modo:").pack(side="left")
        for text, value in [("Automática (WAV↔MP3)", "auto"), ("Forçar MP3", "mp3"), ("Forçar WAV", "wav")]:
            ttk.Radiobutton(mode_frame, text=text, value=value, variable=self.mode).pack(side="left", padx=6)

        settings_frame = ttk.Frame(self.root, padding=(10, 6))
        settings_frame.pack(fill="x")
        ttk.Button(settings_frame, text="⚙ MP3", command=self.open_mp3_settings).pack(side="left")
        ttk.Button(settings_frame, text="⚙ WAV", command=self.open_wav_settings).pack(side="left", padx=6)
        self.settings_label = ttk.Label(settings_frame, foreground="gray")
        self.settings_label.pack(side="left", padx=6)
        self._refresh_settings_label()

        drop_frame = ttk.Frame(self.root, padding=10)
        drop_frame.pack(fill="both", expand=True)

        self.drop_area = tk.Listbox(drop_frame, selectmode="extended", activestyle="none")
        self.drop_area.pack(fill="both", expand=True, side="left")
        scrollbar = ttk.Scrollbar(drop_frame, command=self.drop_area.yview)
        scrollbar.pack(side="right", fill="y")
        self.drop_area.config(yscrollcommand=scrollbar.set)

        if HAS_DND:
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind("<<Drop>>", self.on_drop)
        else:
            self.drop_area.insert("end", "(arraste-e-solte indisponível: instale tkinterdnd2)")

        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Adicionar arquivos...", command=self.add_files).pack(side="left")
        ttk.Button(btn_frame, text="Adicionar pasta...", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Limpar lista", command=self.clear_list).pack(side="left")
        self.convert_btn = ttk.Button(btn_frame, text="Converter", command=self.start_conversion)
        self.convert_btn.pack(side="right")

        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 6))

        log_frame = ttk.Frame(self.root, padding=(10, 0, 10, 0))
        log_frame.pack(fill="both")
        self.log_text = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

        footer = ttk.Label(
            self.root, text="Freeware by @eduardokreca", foreground="gray", anchor="e"
        )
        footer.pack(fill="x", padx=10, pady=(2, 8))

    def _refresh_settings_label(self):
        self.settings_label.config(
            text=f"MP3: {self.mp3_bitrate.get()} kbps    |    "
                 f"WAV: {self.wav_bitdepth.get()} bit / {self.wav_samplerate.get()} Hz"
        )

    def _save_settings(self):
        save_config({
            "mp3_bitrate": self.mp3_bitrate.get(),
            "wav_bitdepth": self.wav_bitdepth.get(),
            "wav_samplerate": self.wav_samplerate.get(),
        })
        self._refresh_settings_label()

    def _center_dialog(self, dialog):
        dialog.update_idletasks()
        w = dialog.winfo_reqwidth()
        h = dialog.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dialog.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def open_mp3_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Configurações MP3")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack()

        ttk.Label(frame, text="Bitrate:").grid(row=0, column=0, sticky="w", pady=4)
        combo = ttk.Combobox(frame, values=MP3_BITRATES, textvariable=self.mp3_bitrate, state="readonly", width=10)
        combo.grid(row=0, column=1, padx=6)
        ttk.Label(frame, text="kbps").grid(row=0, column=2, sticky="w")
        codec_label = "Fraunhofer (mp3_mf)" if IS_WINDOWS else "LAME (libmp3lame)"
        ttk.Label(frame, text=f"Codec: {codec_label}, 44.1 kHz estéreo", foreground="gray").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )

        def confirm():
            self._save_settings()
            dialog.destroy()

        ttk.Button(frame, text="OK", command=confirm).grid(row=2, column=0, columnspan=3, pady=(12, 0))

        self._center_dialog(dialog)

    def open_wav_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Configurações WAV")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack()

        ttk.Label(frame, text="Bit depth:").grid(row=0, column=0, sticky="w", pady=4)
        depth_combo = ttk.Combobox(
            frame, values=WAV_BITDEPTHS, textvariable=self.wav_bitdepth, state="readonly", width=10
        )
        depth_combo.grid(row=0, column=1, padx=6)
        ttk.Label(frame, text="bit").grid(row=0, column=2, sticky="w")

        ttk.Label(frame, text="Sample rate:").grid(row=1, column=0, sticky="w", pady=4)
        rate_combo = ttk.Combobox(
            frame, values=WAV_SAMPLERATES, textvariable=self.wav_samplerate, state="readonly", width=10
        )
        rate_combo.grid(row=1, column=1, padx=6)
        ttk.Label(frame, text="Hz").grid(row=1, column=2, sticky="w")

        def confirm():
            self._save_settings()
            dialog.destroy()

        ttk.Button(frame, text="OK", command=confirm).grid(row=2, column=0, columnspan=3, pady=(12, 0))

        self._center_dialog(dialog)

    def log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def choose_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_dir.set(folder)

    def add_files(self):
        paths = filedialog.askopenfilenames(
            filetypes=[("Áudio", "*.wav *.mp3"), ("Todos os arquivos", "*.*")]
        )
        for p in paths:
            self._add_path(p)

    def add_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        for root_dir, _dirs, files in os.walk(folder):
            for f in files:
                self._add_path(os.path.join(root_dir, f))

    def clear_list(self):
        self.drop_area.delete(0, "end")
        self.file_queue = []

    def on_drop(self, event):
        for path in self.root.tk.splitlist(event.data):
            if os.path.isdir(path):
                for root_dir, _dirs, files in os.walk(path):
                    for f in files:
                        self._add_path(os.path.join(root_dir, f))
            else:
                self._add_path(path)

    def _add_path(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext not in AUDIO_EXTS:
            return
        if path in self.file_queue:
            return
        self.file_queue.append(path)
        self.drop_area.insert("end", path)

    def start_conversion(self):
        if not self.file_queue:
            self.log("Nenhum arquivo na lista.")
            return
        out_dir = self.output_dir.get().strip()
        if not out_dir:
            self.log("Escolha uma pasta de saída antes de converter.")
            return
        os.makedirs(out_dir, exist_ok=True)
        self.convert_btn.config(state="disabled")
        self.progress.config(maximum=len(self.file_queue), value=0)
        threading.Thread(target=self._convert_all, args=(list(self.file_queue), out_dir), daemon=True).start()

    def _convert_all(self, files, out_dir):
        for path in files:
            self._convert_one(path, out_dir)
            self.root.after(0, self._advance_progress)
        self.root.after(0, lambda: self.log("Concluído."))
        self.root.after(0, lambda: self.convert_btn.config(state="normal"))

    def _advance_progress(self):
        self.progress.step(1)

    def _target_format(self, path):
        ext = os.path.splitext(path)[1].lower()
        mode = self.mode.get()
        if mode == "mp3":
            return "mp3"
        if mode == "wav":
            return "wav"
        if ext in WAV_EXTS:
            return "mp3"
        if ext in MP3_EXTS:
            return "wav"
        return None

    def _convert_one(self, path, out_dir):
        target = self._target_format(path)
        if target is None:
            self.root.after(0, lambda: self.log(f"Ignorado (formato desconhecido): {path}"))
            return

        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(out_dir, f"{base}.{target}")

        if target == "mp3":
            ok = self._encode_mp3(path, out_path, encoder=DEFAULT_MP3_ENCODER)
            if not ok and DEFAULT_MP3_ENCODER != "libmp3lame":
                self.root.after(0, lambda: self.log(f"{DEFAULT_MP3_ENCODER} falhou, tentando libmp3lame para: {path}"))
                ok = self._encode_mp3(path, out_path, encoder="libmp3lame")
            if ok:
                self.root.after(0, lambda: self.log(f"OK: {path} -> {out_path}"))
            else:
                self.root.after(0, lambda: self.log(f"ERRO ao converter: {path}"))
        else:
            ok = self._encode_wav(path, out_path)
            if ok:
                self.root.after(0, lambda: self.log(f"OK: {path} -> {out_path}"))
            else:
                self.root.after(0, lambda: self.log(f"ERRO ao converter: {path}"))

    def _encode_mp3(self, src, dst, encoder):
        bitrate = self.mp3_bitrate.get()
        cmd = [
            ffmpeg_path(), "-y", "-i", src,
            "-vn", "-ar", "44100", "-ac", "2",
            "-c:a", encoder, "-b:a", f"{bitrate}k",
        ]
        if encoder == "mp3_mf":
            cmd += ["-rtbufsize", "128M"]
        cmd.append(dst)
        return self._run_ffmpeg(cmd)

    def _encode_wav(self, src, dst):
        codec = WAV_CODEC_BY_DEPTH[self.wav_bitdepth.get()]
        cmd = [
            ffmpeg_path(), "-y", "-i", src,
            "-vn", "-ar", self.wav_samplerate.get(), "-ac", "2",
            "-c:a", codec,
            dst,
        ]
        return self._run_ffmpeg(cmd)

    def _run_ffmpeg(self, cmd):
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                tail = result.stdout.decode(errors="ignore")[-500:] if result.stdout else ""
                self.root.after(0, lambda: self.log(tail))
                return False
            return True
        except FileNotFoundError:
            self.root.after(0, lambda: self.log("ffmpeg não encontrado. Coloque-o na mesma pasta do programa."))
            return False


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    ConversorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
