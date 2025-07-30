import logging
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config, settings
from .logger import TkinterLogHandler, logger
from .scraper import LietaScraper


class TickerApp:
    """
    The main GUI for the application.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("Lieta Research 自動化工具")
        self.root.geometry("600x550")

        self.user_settings = settings.load_settings()
        self.tickers = []
        self.tickers_path = self.user_settings.get("last_ticker_path", "")
        self.destination_path = self.user_settings.get("last_destination_path", "")
        
        # Use the absolute path for the temp download directory from config
        self.temp_download_path = config.TEMP_DOWNLOAD_DIR_NAME
        
        if os.path.exists(self.temp_download_path):
            shutil.rmtree(self.temp_download_path)
        os.makedirs(self.temp_download_path, exist_ok=True)

        self._setup_ui()
        self._setup_logging()
        self._load_initial_state()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_ui(self):
        style = ttk.Style()
        style.theme_use('vista')
        style.configure("TLabel", font=("Helvetica", 9))
        style.configure("TButton", font=("Helvetica", 9))
        style.configure("TCheckbutton", font=("Helvetica", 9))
        style.configure("TLabelframe.Label", font=("Helvetica", 10, "bold"))

        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        self._create_file_selection_frame(main_frame)
        self._create_model_selection_frame(main_frame)
        self._create_destination_path_frame(main_frame)

        self.start_button = ttk.Button(main_frame, text="開始自動化", command=self.start_automation_thread, state="disabled")
        self.start_button.pack(pady=15, ipadx=10, ipady=5)

        self._create_log_display_frame(main_frame)

    def _setup_logging(self):
        """Adds the Tkinter handler to the root logger."""
        tkinter_handler = TkinterLogHandler(self.log_text)
        logger.addHandler(tkinter_handler)

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
        
        default_models = {"Gamma", "Term", "Smile", "TV Code"}
        last_selected = self.user_settings.get("last_selected_models", list(default_models))
        
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
            with open(file_path, "r", encoding="utf-8") as f:
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
            settings.save_setting("last_ticker_path", file_path)
            self.validate_inputs()

    def select_destination_path(self):
        initial_dir = self.destination_path if self.destination_path else "/"
        path = filedialog.askdirectory(initialdir=initial_dir)
        if path:
            self.destination_path = path
            self.dest_label.config(text=path)
            logger.info(f"設定儲存路徑: {path}")
            settings.save_setting("last_destination_path", path)
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
        has_tickers = bool(self.tickers)
        has_dest = bool(self.destination_path and os.path.isdir(self.destination_path))
        has_models = any(var.get() for var in self.selected_models.values())
        is_valid = has_tickers and has_dest and has_models
        self.start_button.config(state="normal" if is_valid else "disabled")
        self.open_dest_button.config(state="normal" if has_dest else "disabled")

    def start_automation_thread(self):
        self.toggle_ui_state(False)
        thread = threading.Thread(target=self.run_automation_task, daemon=True)
        thread.start()

    def run_automation_task(self):
        selected_models = [model for model, var in self.selected_models.items() if var.get()]
        settings.save_setting("last_selected_models", selected_models)

        logger.info("--- 自動化開始 ---")
        scraper = LietaScraper(download_path=self.temp_download_path)
        failed_tickers, total_tasks = [], 0

        try:
            logger.info("正在設定 Selenium WebDriver...")
            if not scraper.setup_driver():
                logger.error("無法連接到偵錯模式的 Chrome，準備關閉程式。")
                self.root.after(0, self.show_shutdown_message_and_close)
                return

            if not scraper.check_login_status():
                logger.warning("自動化中止：使用者未登入。")
                self.root.after(0, lambda: messagebox.showerror("需要登入", "請先登入 Lieta Research 網站後再開始自動化。"))
                self.toggle_ui_state(True)
                scraper.close_driver()
                return

            logger.info("WebDriver 設定成功，開始執行任務。")
            failed_tickers, total_tasks = scraper.run_automation(self.tickers, selected_models, self.destination_path)
            
        except Exception as e:
            logger.critical(f"自動化過程中發生未預期的嚴重錯誤: {e}", exc_info=True)
            self.root.after(0, lambda: messagebox.showerror("嚴重錯誤", f"自動化過程中發生嚴重錯誤，請查看 log.jsonl。\n\n{e}"))
        finally:
            if scraper.driver:
                scraper.close_driver()
            
            if self.root.winfo_exists():
                logger.info("--- 自動化結束 ---")
                if total_tasks > 0:
                    self.show_summary(total_tasks, failed_tickers)
                self.toggle_ui_state(True)

    def show_shutdown_message_and_close(self):
        messagebox.showerror("偵錯連線失敗", "請勿關閉偵錯模式的 Chrome 視窗。\n\n程式即將關閉。")
        self.on_closing(force_close=True)

    def toggle_ui_state(self, is_enabled):
        state = "normal" if is_enabled else "disabled"
        # ... (rest of the method is the same)
        self.start_button.config(state=state)
        self.load_button.config(state=state)
        self.dest_button.config(state=state)
        self.open_dest_button.config(state=state)
        
        model_frame = self.root.winfo_children()[0].winfo_children()[1]
        for cb in model_frame.winfo_children():
            if isinstance(cb, ttk.Checkbutton):
                cb.config(state=state)
        if is_enabled:
            self.validate_inputs()

    def show_summary(self, total_tasks, failed_tickers):
        success_count = total_tasks - len(failed_tickers)
        summary_msg = f"任務完成！\n\n總計: {total_tasks}\n成功: {success_count}\n失敗: {len(failed_tickers)}"
        if failed_tickers:
            summary_msg += f"\n\n失敗的項目:\n" + "\n".join(failed_tickers)
        logger.info(f"任務總結: {summary_msg.replace('任務完成！', '').strip()}")
        self.root.after(0, lambda: messagebox.showinfo("任務總結", summary_msg))

    def cleanup(self):
        try:
            if os.path.exists(self.temp_download_path):
                shutil.rmtree(self.temp_download_path)
                logger.info(f"已清除暫存資料夾: {self.temp_download_path}")
        except OSError as e:
            logger.warning(f"無法自動刪除暫存資料夾: {e}", exc_info=True)

    def on_closing(self, force_close=False):
        if force_close or messagebox.askokcancel("結束", "確定要關閉程式嗎?"):
            logger.info("正在關閉應用程式...")
            self.cleanup()
            self.root.destroy()

