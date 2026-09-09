import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config, settings
from .logger import TkinterLogHandler, logger
from .batch import BatchRunner
from .dashboard import build_dashboard, COLORS


class TickerApp:
    """
    The main GUI for the application.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("Lieta Automator 1.1.2 · 模型下載工作台")

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
        build_dashboard(self)

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
            self.validate_inputs()
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
        self.tk_log_handler = tkinter_handler
        logger.addHandler(tkinter_handler)
        self.root.bind("<Destroy>", self._remove_log_handler, add=True)
        self.root.after(100, self._process_log_queue)

    def _remove_log_handler(self, event):
        if event.widget == self.root:
            logger.removeHandler(self.tk_log_handler)

    def _process_log_queue(self):
        try:
            # Yield regularly so a busy batch cannot starve buttons or repainting.
            for _ in range(100):
                record = self.log_queue.get_nowait()
                msg = self.log_formatter.format(record)
                
                if self.log_text.winfo_exists():
                    self.log_text.config(state="normal")
                    self.log_text.insert(tk.END, msg + "\n")
                    if int(self.log_text.index("end-1c").split(".")[0]) > 1500:
                        self.log_text.delete("1.0", "101.0")
                    self.log_text.see(tk.END)
                    self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self._process_log_queue)

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
        selected = sum(var.get() for var in self.selected_models.values())
        missing = []
        if not self.tickers:
            missing.append("Ticker 清單")
        if not selected:
            missing.append("模型")
        if not has_dest:
            missing.append("儲存資料夾")
        self.input_hint.config(text="請選擇：" + "、".join(missing) if missing else
                               f"{len(set(self.tickers))} 個 ticker × {selected} 個模型")
        self.start_button.config(state="normal" if not missing and not self.automation_running and not self.closing else "disabled")
        self.open_dest_button.config(state="normal" if has_dest else "disabled")
        if not self.automation_running:
            mode = "四模型獨立視窗" if self.user_settings.get("enable_multi_window", True) else "單視窗依序執行"
            self.mode_label.config(text=f"{mode} · 等待上限 1")

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
        if self.runner and not self.runner.done.is_set():
            return
        try:
            selected = [m for m, var in self.selected_models.items() if var.get()]
            current = settings.load_settings()
            current["last_selected_models"] = selected
            settings.save_settings(current)
            self.runner = BatchRunner(self.tickers.copy(), selected, self.destination_path,
                                      multi=current.get("enable_multi_window", True), resume=resume)
            if resume:
                # A resumed batch owns its destination and selection, not the form's previous values.
                self.tickers = self.runner.tickers.copy()
                self.tickers_path = ""
                self.file_label.config(text=f"續跑清單：{len(self.tickers)} 個 ticker（取自批次紀錄）")
                self.destination_path = self.runner.destination
                self.dest_label.config(text=self.destination_path)
                for model, var in self.selected_models.items():
                    var.set(model in self.runner.models)
            self.automation_running = True
            self._set_summary('批次執行中；結束後列出每個模型的成功數與未完成項目。')
            self.validate_inputs()
            self.toggle_ui_state(False)
            self.resume_button.config(state="disabled")
            threading.Thread(target=self.runner.run, daemon=False).start()
        except Exception as exc:
            self.runner = None
            self.automation_running = False
            self.toggle_ui_state(True)
            messagebox.showerror("無法開始", str(exc))

    def stop_batch(self):
        if self.runner and not self.runner.done.is_set():
            self.runner.request_stop()
            self.stop_button.config(state="disabled")
            self.overall_label.config(text="正在停止並保存…")
            for _, button in self.model_status.values():
                button.config(state="disabled")

    def _resume_batch(self):
        path = filedialog.askopenfilename(title="選擇批次或單模型續跑紀錄", filetypes=[("JSON", "*.json")],
                                          initialdir=os.path.join(config.BASE_DIR, "runs"))
        if path:
            self._start_batch(path)

    def _set_summary(self, text):
        self.summary_text.configure(state='normal')
        self.summary_text.delete('1.0', 'end')
        self.summary_text.insert('1.0', text)
        self.summary_text.configure(state='disabled')

    def _retry_failed(self):
        if self.automation_running or self.closing:
            return
        if self.runner:
            if not self.runner.done.is_set():
                return
            self._start_batch(str(self.runner.path))
        else:
            # After restarting the app, an older batch can be chosen without its original txt.
            self._resume_batch()

    def _poll_batch(self):
        if self.runner:
            states, dispatch = self.runner.snapshot()
            self.cooldown_label.config(text=f"共用冷卻：{dispatch['cooldown']:.0f} 秒；等待結果 {dispatch['active']}/{dispatch['limit']}")
            for model, (label, button) in self.model_status.items():
                state = states.get(model)
                if state:
                    status = state['state']
                    color = COLORS['warning'] if status == '等待登入' else COLORS['error'] if '錯誤' in status or '失敗' in status else COLORS['accent'] if status == '完成' else COLORS['muted']
                    label.config(text=f"{status}  {state['ticker']}", foreground=color)
                    self.model_counts[model].config(text=f"完成 {state['success']} / {state['success'] + state['pending']} · 待補抓 {state.get('deferred', 0)}")
                    self.model_progress[model].config(maximum=max(1, state['success'] + state['pending']), value=state['success'])
                    button.config(state="normal" if status == "等待登入" and not self.closing and not self.runner.dispatch.stop.is_set() else "disabled")
                else:
                    label.config(text="本批次未選擇", foreground=COLORS['muted'])
                    button.config(state="disabled")
                    self.model_counts[model].config(text="完成 0 / 0 · 待補抓 0")
                    self.model_progress[model].config(value=0, maximum=1)
            success = sum(s['success'] for s in states.values())
            total = sum(s['success'] + s['pending'] for s in states.values())
            waiting = sum(s['state'] == '等待登入' for s in states.values())
            summary = f"已完成 {success} / {total}"
            if self.runner.done.is_set():
                summary = "全部完成" if self.runner.data.get('status') == 'complete' else f"已保存進度 · 未完成 {total - success}"
            elif self.runner.dispatch.stop.is_set():
                summary = "正在停止並保存…"
            elif waiting:
                summary += f" · {waiting} 個等待登入"
            self.overall_label.config(text=summary)
            self.mode_label.config(text=("獨立視窗" if self.runner.multi else "單視窗") + f" · 提交間隔至少 5 秒 · 等待上限 {dispatch['limit']}")
            if self.runner.done.is_set() and self.automation_running:
                self.automation_running = False
                report = self.runner.data.get('summary')
                if report:
                    self._set_summary(report['text'])
                else:
                    self._set_summary('結束摘要未能產生，請查看執行紀錄與批次 JSON。')
                if not self.closing:
                    self.toggle_ui_state(True)
                    self.resume_button.config(state="normal")
                    remaining = report['failed'] if report else total - success
                    self.retry_button.config(state='normal' if remaining else 'disabled')
            if self.closing and self.runner.done.is_set():
                self.root.destroy()
                return
        self.root.after(250, self._poll_batch)

    def toggle_ui_state(self, is_enabled):
        state = "normal" if is_enabled else "disabled"
        
        self.start_button.config(state=state)
        self.load_button.config(state=state)
        self.dest_button.config(state=state)
        
        self.settings_button.config(state=state)
        self.resume_button.config(state=state)
        self.retry_button.config(state=state)
        for cb in self.model_checks:
            cb.config(state=state)
        self.stop_button.config(state="disabled" if is_enabled else "normal")

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
            self.stop_button.config(state="disabled")
            self.retry_button.config(state="disabled")
        else:
            self.root.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, on_close_callback):
        super().__init__(parent)
        self.parent = parent
        self.on_close_callback = on_close_callback
        self.transient(parent)
        self.title("設定")
        self.geometry("540x290")
        self.configure(background=COLORS['background'])
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
        ttk.Label(general_frame, text="每個模型使用自己的視窗，最多 1 個請求等待結果。\n所有視窗共用至少 5 秒提交間隔；遇到錯誤一起冷卻。",
                  style="CardMuted.TLabel", justify="left").pack(anchor="w", pady=(12, 0))

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(side="bottom", fill="x", pady=(20, 0))

        self.save_button = ttk.Button(button_frame, text="儲存並關閉", command=self.save_and_close)
        self.save_button.pack(side="right", padx=5)

        self.cancel_button = ttk.Button(button_frame, text="取消", command=self.cancel_and_close)
        self.cancel_button.pack(side="right")

        self.grab_set()
        self.cancel_button.focus_set()
        self.bind("<Escape>", lambda event: self.cancel_and_close())
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
