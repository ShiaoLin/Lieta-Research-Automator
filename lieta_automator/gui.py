import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import Toplevel, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import config, chrome_launcher, settings
from .logger import TkinterLogHandler, logger
from .scraper import LietaScraper
from .batch import BatchRunner, MODELS


class TickerApp:
    """
    The main GUI for the application.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("Lieta Research 自動化工具 v1.1.0")
        self.root.geometry("760x850")

        self.user_settings = settings.load_settings()
        self.tickers = []
        self.tickers_path = self.user_settings.get("last_ticker_path", "")
        self.destination_path = self.user_settings.get("last_destination_path", "")
        
        self.temp_download_path_base = config.TEMP_DOWNLOAD_DIR_NAME
        self._prepare_temp_dir()

        self.log_queue = queue.Queue()
        self.scrapers = []
        self.automation_running = False
        self.runner = None
        self.closing = False
        self.log_formatter = logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S')

        self._setup_ui()
        self._setup_logging()
        self._load_initial_state()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _prepare_temp_dir(self):
        os.makedirs(self.temp_download_path_base, exist_ok=True)

    def _setup_ui(self):
        style = ttk.Style()
        style.theme_use('vista')
        style.configure("TLabel", font=("Helvetica", 9))
        style.configure("TButton", font=("Helvetica", 9))
        style.configure("TCheckbutton", font=("Helvetica", 9))
        style.configure("TLabelframe.Label", font=("Helvetica", 10, "bold"))

        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill="x", padx=10, pady=(5, 0))
        
        try:
            icon_path = os.path.join(getattr(sys, '_MEIPASS', config.BASE_DIR), "settings.png")
            self.settings_icon = ImageTk.PhotoImage(Image.open(icon_path).resize((24, 24), Image.Resampling.LANCZOS))
            settings_button = ttk.Button(top_frame, image=self.settings_icon, command=self._open_settings_window)
            settings_button.pack(side="right")
        except Exception:
            settings_button = ttk.Button(top_frame, text="設定", command=self._open_settings_window)
            settings_button.pack(side="right")


        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        self._create_file_selection_frame(main_frame)
        self._create_model_selection_frame(main_frame)
        self._create_destination_path_frame(main_frame)

        self.start_button = ttk.Button(main_frame, text="開始自動化", command=self.start_automation_thread, state="disabled")
        self.start_button.pack(pady=15, ipadx=10, ipady=5)

        self.resume_button = ttk.Button(main_frame, text="續跑批次", command=self._resume_batch)
        self.resume_button.pack()
        self.cooldown_label = ttk.Label(main_frame, text="共用冷卻：0 秒")
        self.cooldown_label.pack()
        self.model_status = {}
        for model in MODELS:
            row = ttk.Frame(main_frame)
            row.pack(fill="x")
            label = ttk.Label(row, text=f"{model}：未開始")
            label.pack(side="left")
            button = ttk.Button(row, text="登入後繼續", state="disabled",
                                command=lambda m=model: self.runner.continue_model(m) if self.runner else None)
            button.pack(side="right")
            self.model_status[model] = (label, button)
        self.root.after(250, self._poll_batch)
        self._create_log_display_frame(main_frame)

    def _open_settings_window(self):
        # Pass a callback function to the dialog
        dialog = SettingsDialog(self.root, self._handle_settings_close)
        dialog.wait_window()

    def _handle_settings_close(self, new_settings):
        """Callback executed when SettingsDialog is closed."""
        if new_settings is None:
            logger.info("設定視窗已取消。")
            return
        try:
            self.user_settings.update(new_settings)
            settings.save_settings(self.user_settings)
            logger.info("設定已儲存至 user_settings.json")
        except (IOError, OSError) as e:
            error_msg = f"無法儲存設定檔: {e}"
            logger.error(error_msg, exc_info=True)
            self.log_message("ERROR", error_msg)
        except Exception as e:
            error_msg = f"處理設定時發生未預期錯誤: {e}"
            logger.error(error_msg, exc_info=True)
            self.log_message("ERROR", error_msg)

    def log_message(self, level, message):
        """Helper to log messages from other dialogs to the main UI."""
        numeric_level = logging.getLevelName(level.upper())
        logger.log(numeric_level, message)

    def _setup_logging(self):
        tkinter_handler = TkinterLogHandler(self.log_queue)
        logger.addHandler(tkinter_handler)
        self.root.after(100, self._process_log_queue)

    def _process_log_queue(self):
        try:
            while not self.log_queue.empty():
                record = self.log_queue.get_nowait()
                msg = self.log_formatter.format(record)
                
                if self.log_text.winfo_exists():
                    self.log_text.config(state="normal")
                    self.log_text.insert(tk.END, msg + "\n")
                    self.log_text.see(tk.END)
                    self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self._process_log_queue)

    def _create_file_selection_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="1. 選擇 Ticker 檔案 (.txt)", padding=(10, 5))
        frame.pack(fill="x", padx=5, pady=5)
        self.file_label = ttk.Label(frame, text="尚未選擇檔案", wraplength=450, justify="left")
        self.file_label.pack(side="left", fill="x", expand=True, padx=5)
        self.load_button = ttk.Button(frame, text="瀏覽...", command=self.load_ticker_list)
        self.load_button.pack(side="right")

    def _create_model_selection_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="2. 選擇模型", padding=(10, 5))
        frame.pack(fill="x", padx=5, pady=5)
        
        self.models = ["Gamma", "Term", "Smile", "TV Code"]
        self.selected_models = {}
        
        last_selected = self.user_settings.get("last_selected_models", [])
        
        for i, model in enumerate(self.models):
            var = tk.BooleanVar(value=(model in last_selected))
            cb = ttk.Checkbutton(frame, text=model, variable=var, command=self.validate_inputs)
            cb.grid(row=i // 4, column=i % 4, sticky="w", padx=5, pady=2)
            self.selected_models[model] = var

    def _create_destination_path_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="3. 選擇儲存目的地", padding=(10, 5))
        frame.pack(fill="x", padx=5, pady=5)
        
        self.dest_label = ttk.Label(frame, text="尚未選擇路徑", wraplength=380, justify="left")
        self.dest_label.pack(side="left", fill="x", expand=True, padx=5)

        self.open_dest_button = ttk.Button(frame, text="打開資料夾", command=self.open_destination_folder, state="disabled")
        self.open_dest_button.pack(side="right", padx=(0, 5))
        
        self.dest_button = ttk.Button(frame, text="瀏覽...", command=self.select_destination_path)
        self.dest_button.pack(side="right")

    def _create_log_display_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="進度日誌", padding=(10, 5))
        frame.pack(fill="both", expand=True, padx=5, pady=5)
        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(frame, height=10, state="disabled", wrap="word", yscrollcommand=scrollbar.set, font=("Courier New", 9))
        self.log_text.pack(fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

    def _load_initial_state(self):
        if self.tickers_path and os.path.exists(self.tickers_path):
            self._load_tickers_from_path(self.tickers_path)
        
        if self.destination_path and os.path.isdir(self.destination_path):
            self.dest_label.config(text=self.destination_path)
            logger.info(f"已載入上次儲存的路徑: {self.destination_path}")
        
        self.validate_inputs()

    def _load_tickers_from_path(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                self.tickers = [line.strip().upper() for line in f if line.strip()]
            if not self.tickers:
                logger.warning(f"Ticker 檔案 {file_path} 為空。")
                return
            self.tickers_path = file_path
            self.file_label.config(text=file_path)
            logger.info(f"已載入 Ticker 檔案: {len(self.tickers)} 個 Tickers。")
        except Exception as e:
            logger.error(f"無法載入 Ticker 檔案 {file_path}: {e}", exc_info=True)
            self.tickers_path = ""

    def load_ticker_list(self):
        initial_dir = os.path.dirname(self.tickers_path) if self.tickers_path else "/"
        file_path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")], initialdir=initial_dir)
        if file_path:
            self._load_tickers_from_path(file_path)
            current_settings = settings.load_settings()
            current_settings["last_ticker_path"] = file_path
            settings.save_settings(current_settings)
            self.validate_inputs()

    def select_destination_path(self):
        initial_dir = self.destination_path if self.destination_path else "/"
        path = filedialog.askdirectory(initialdir=initial_dir)
        if path:
            self.destination_path = path
            self.dest_label.config(text=path)
            logger.info(f"設定儲存路徑: {path}")
            current_settings = settings.load_settings()
            current_settings["last_destination_path"] = path
            settings.save_settings(current_settings)
            self.validate_inputs()

    def open_destination_folder(self):
        if not self.destination_path or not os.path.isdir(self.destination_path):
            logger.error("嘗試開啟無效的目的地資料夾。")
            messagebox.showwarning("警告", "選擇的目的地資料夾不存在或無效。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(self.destination_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", self.destination_path], check=True)
            else:
                subprocess.run(["xdg-open", self.destination_path], check=True)
            logger.info(f"已在檔案總管中開啟: {self.destination_path}")
        except Exception as e:
            logger.error(f"無法開啟資料夾: {e}", exc_info=True)
            messagebox.showerror("錯誤", f"無法開啟資料夾.\n錯誤: {e}")

    def validate_inputs(self):
        has_dest = bool(self.destination_path and os.path.isdir(self.destination_path))
        self.start_button.config(state="normal")
        self.open_dest_button.config(state="normal" if has_dest else "disabled")

    def start_automation_thread(self):
        if self.automation_running:
            return
        
        has_tickers = bool(self.tickers)
        has_dest = bool(self.destination_path and os.path.isdir(self.destination_path))
        selected_models = [model for model, var in self.selected_models.items() if var.get()]
        has_models = bool(selected_models)

        if not all([has_tickers, has_dest, has_models]):
            if not has_tickers: logger.error("自動化中止：未選擇 Ticker 檔案。")
            if not has_dest: logger.error("自動化中止：未選擇有效的儲存目的地。")
            if not has_models: logger.error("自動化中止：未選擇任何模型。")
            return

        self.automation_running = True
        self.toggle_ui_state(False)
        
        self._start_batch()

    def run_automation_task(self):
        # Kept as a compatibility entry; snapshot Tk values on the UI thread.
        self.root.after(0, self._start_batch)

    def _start_batch(self, resume=None):
        try:
            selected = [m for m, var in self.selected_models.items() if var.get()]
            current = settings.load_settings()
            current["last_selected_models"] = selected
            settings.save_settings(current)
            self.runner = BatchRunner(self.tickers.copy(), selected, self.destination_path,
                                      multi=current.get("enable_multi_window", True), resume=resume)
            self.automation_running = True
            self.toggle_ui_state(False)
            self.resume_button.config(state="disabled")
            threading.Thread(target=self.runner.run, daemon=False).start()
        except Exception as exc:
            self.runner = None
            self.automation_running = False
            self.toggle_ui_state(True)
            messagebox.showerror("無法開始", str(exc))

    def _resume_batch(self):
        path = filedialog.askopenfilename(title="選擇批次或單模型續跑紀錄", filetypes=[("JSON", "*.json")],
                                          initialdir=os.path.join(config.BASE_DIR, "runs"))
        if path:
            self._start_batch(path)

    def _poll_batch(self):
        if self.runner:
            states, dispatch = self.runner.snapshot()
            self.cooldown_label.config(text=f"共用冷卻：{dispatch['cooldown']:.0f} 秒；等待結果 {dispatch['active']}/{dispatch['limit']}")
            for model, (label, button) in self.model_status.items():
                state = states.get(model)
                if state:
                    label.config(text=f"{model}：{state['state']} {state['ticker']}　成功 {state['success']}／待補抓 {state.get('deferred', 0)}／未完成 {state['pending']}")
                    button.config(state="normal" if state['state'] == "等待登入" and not self.closing else "disabled")
            if self.runner.done.is_set() and self.automation_running:
                self.automation_running = False
                if not self.closing:
                    self.toggle_ui_state(True)
                    self.resume_button.config(state="normal")
            if self.closing and self.runner.done.is_set():
                self.root.destroy()
                return
        self.root.after(250, self._poll_batch)

    def toggle_ui_state(self, is_enabled):
        state = "normal" if is_enabled else "disabled"
        
        self.start_button.config(state=state)
        self.load_button.config(state=state)
        self.dest_button.config(state=state)
        
        try:
            self.root.winfo_children()[0].winfo_children()[0].config(state=state)
            model_frame = self.root.winfo_children()[1].winfo_children()[1]
            for cb in model_frame.winfo_children():
                if isinstance(cb, ttk.Checkbutton):
                    cb.config(state=state)
        except (IndexError, tk.TclError):
            pass

        if is_enabled:
            self.validate_inputs()
        else:
            self.start_button.config(state="disabled")
        
        self.open_dest_button.config(state="normal" if self.destination_path and os.path.isdir(self.destination_path) else "disabled")

    def on_closing(self, force_close=False):
        if self.runner and not self.runner.done.is_set():
            self.closing = True
            self.runner.request_stop()
            self.cooldown_label.config(text="正在停止新請求並保存進度…")
            self.start_button.config(state="disabled")
            self.resume_button.config(state="disabled")
        else:
            self.root.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, on_close_callback):
        super().__init__(parent)
        self.parent = parent
        self.on_close_callback = on_close_callback
        self.transient(parent)
        self.title("設定")
        self.geometry("450x180")
        self.resizable(False, False)

        self.settings = settings.load_settings()

        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(expand=True, fill="both")

        # --- General Settings ---
        general_frame = ttk.LabelFrame(main_frame, text="通用設定", padding=10)
        general_frame.pack(fill="x", pady=5)

        self.multi_window_var = tk.BooleanVar(value=self.settings.get("enable_multi_window", False))
        multi_window_cb = ttk.Checkbutton(general_frame, text="使用多視窗（各模型依序請求，共用冷卻時間）", variable=self.multi_window_var)
        multi_window_cb.pack(anchor="w")

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(side="bottom", fill="x", pady=(20, 0))

        self.save_button = ttk.Button(button_frame, text="儲存並關閉", command=self.save_and_close)
        self.save_button.pack(side="right", padx=5)

        self.cancel_button = ttk.Button(button_frame, text="取消", command=self.cancel_and_close)
        self.cancel_button.pack(side="right")

        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.cancel_and_close)

    def save_and_close(self):
        """Collects data from UI and passes it to the callback."""
        new_settings = {
            'enable_multi_window': self.multi_window_var.get(),
        }
        self.on_close_callback(new_settings)
        self.destroy()

    def cancel_and_close(self):
        """Closes the window and signals no changes were made."""
        self.on_close_callback(None)
        self.destroy()
