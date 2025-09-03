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

from . import config, chrome_launcher, settings, scheduler
from .logger import TkinterLogHandler, logger
from .scraper import LietaScraper


class TickerApp:
    """
    The main GUI for the application.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("Lieta Research 自動化工具 v1.0.4") # Version Bump
        self.root.geometry("600x650")

        self.user_settings = settings.load_settings()
        self.tickers = []
        self.tickers_path = self.user_settings.get("last_ticker_path", "")
        self.destination_path = self.user_settings.get("last_destination_path", "")
        
        self.temp_download_path_base = config.TEMP_DOWNLOAD_DIR_NAME
        self._prepare_temp_dir()

        self.log_queue = queue.Queue()
        self.scrapers = []
        self.automation_running = False
        self.log_formatter = logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S')

        self._setup_ui()
        self._setup_logging()
        self._load_initial_state()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _prepare_temp_dir(self):
        try:
            os.makedirs(self.temp_download_path_base, exist_ok=True)
            for item in os.listdir(self.temp_download_path_base):
                item_path = os.path.join(self.temp_download_path_base, item)
                try:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    elif os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                except Exception as e:
                    logger.warning(f"無法刪除暫存項目 {item_path}: {e}")
        except Exception as e:
            logger.error(f"無法建立或存取暫存資料夾 {self.temp_download_path_base}: {e}", exc_info=True)
            messagebox.showerror("嚴重錯誤", f"無法準備暫存資料夾，請檢查權限.\n\n{e}")
            self.root.destroy()

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
            icon_path = os.path.join(config.BASE_DIR, "settings.png")
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

        self._create_log_display_frame(main_frame)

    def _open_settings_window(self):
        # Pass a callback function to the dialog
        dialog = SettingsDialog(self.root, self._handle_settings_close)
        dialog.wait_window()

    def _handle_settings_close(self, new_settings):
        """
        Callback function that is executed when the SettingsDialog is closed.
        All logic for saving and scheduling is now handled here.
        """
        if new_settings is None: # Window was closed without saving
            logger.info("設定視窗已取消。")
            return

        try:
            # 1. Update and save settings
            self.user_settings.update(new_settings)
            settings.save_settings(self.user_settings)
            logger.info("設定已儲存至 user_settings.json")

            # 2. Handle scheduling
            schedule_enabled = self.user_settings.get('schedule_enabled', False)
            schedule_time = self.user_settings.get('schedule_time', '17:00')
            schedule_type = self.user_settings.get('schedule_type', 'DAILY')

            if schedule_enabled:
                if not scheduler.is_admin():
                    raise PermissionError("需要系統管理員權限才能設定排程。")
                
                success, message = scheduler.create_or_update_task(schedule_time, schedule_type)
                if not success:
                    raise RuntimeError(f"排程設定失敗。原因: {message}")
                logger.info(message)
            else:
                if scheduler.is_task_scheduled():
                    if not scheduler.is_admin():
                        raise PermissionError("需要系統管理員權限才能刪除排程。")
                    success, message = scheduler.delete_task()
                    if not success:
                        raise RuntimeError(f"取消排程失敗。原因: {message}")
                    logger.info(message)

        except (PermissionError, RuntimeError) as e:
            error_msg = str(e)
            logger.error(error_msg)
            self.log_message("ERROR", f"{error_msg} (請確認您是以系統管理員身分執行本程式)")
        
        except (IOError, OSError) as e:
            error_msg = f"無法儲存設定檔: {e}"
            logger.error(error_msg, exc_info=True)
            self.log_message("ERROR", f"{error_msg} (請檢查程式是否有權限寫入 user_settings.json)")
        
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
        
        thread = threading.Thread(target=self.run_automation_task, daemon=True)
        thread.start()

    def run_automation_task(self):
        try:
            use_multi_window = self.user_settings.get("enable_multi_window", False)
            if use_multi_window:
                self._run_multi_window_task()
            else:
                self._run_single_window_task()
        except Exception as e:
            logger.critical(f"自動化過程中發生未預期的嚴重錯誤: {e}", exc_info=True)
            if self.root.winfo_exists():
                self.root.after(0, lambda: messagebox.showerror("嚴重錯誤", f"自動化過程中發生嚴重錯誤，請查看 log.jsonl.\n\n{e}"))
        finally:
            if self.root.winfo_exists():
                self.automation_running = False
                self.toggle_ui_state(True)

    def _run_multi_window_task(self):
        selected_models = [model for model, var in self.selected_models.items() if var.get()]
        current_settings = settings.load_settings()
        current_settings["last_selected_models"] = selected_models
        settings.save_settings(current_settings)

        if len(selected_models) > len(config.REMOTE_DEBUGGING_PORTS):
            msg = f"選擇的模型數量 ({len(selected_models)}) 超過可用埠號數量 ({len(config.REMOTE_DEBUGGING_PORTS)})。"
            logger.error(msg)
            # self.root.after(0, lambda: messagebox.showerror("錯誤", msg)) # This line is removed
            return

        logger.info("--- 自動化開始 (多視窗模式) ---")
        
        logger.info("階段 1: 準備並同步所有 Chrome 設定檔...")
        profiles_to_launch = []
        for i, model in enumerate(selected_models):
            port = config.REMOTE_DEBUGGING_PORTS[i]
            user_data_dir = config.get_chrome_user_data_dir(port)
            profiles_to_launch.append({'port': port, 'user_data_dir': user_data_dir, 'model': model})
            chrome_launcher._sync_profile_if_new(port, user_data_dir)
        logger.info("所有設定檔準備完成。")

        logger.info("階段 2: 啟動 Chrome 實例並執行自動化任務...")
        self.scrapers = []
        threads = []
        
        for profile in profiles_to_launch:
            temp_path_for_port = config.get_temp_download_path_for_port(profile['port'])
            scraper = LietaScraper(download_path=temp_path_for_port, port=profile['port'])
            self.scrapers.append(scraper)
            
            thread = threading.Thread(
                target=self._run_single_model_task,
                args=(scraper, self.tickers.copy(), profile['model'], self.destination_path, profile['port'], profile['user_data_dir']),
                daemon=True
            )
            threads.append(thread)
            thread.start()
            time.sleep(1)

        for thread in threads:
            thread.join()

        logger.info("--- 所有線程執行完畢 ---")
        
        all_failed_tickers = []
        for scraper in self.scrapers:
            all_failed_tickers.extend(scraper.failed_tickers)
        
        total_tasks = len(selected_models) * len(self.tickers)
        
        if self.root.winfo_exists():
            self.show_summary(total_tasks, all_failed_tickers)

    def _run_single_model_task(self, scraper, tickers, model, dest_path, port, user_data_dir):
        try:
            if not chrome_launcher.launch_chrome_in_debug_mode(port, user_data_dir):
                raise Exception(f"[Port {port}] 無法啟動 Chrome 偵錯實例。")
            
            logger.info(f"[Port {port}] 等待 Chrome 啟動...")
            time.sleep(5)

            if not scraper.setup_driver():
                raise Exception(f"[Port {port}] 無法連接到 WebDriver。")

            if not scraper.check_login_status():
                if port == config.REMOTE_DEBUGGING_PORTS[0]:
                     logger.error("使用者未登入。請先登入 Lieta Research 網站後再開始自動化。")
                raise Exception(f"[Port {port}] 使用者未登入。")

            logger.info(f"[Port {port}] WebDriver 設定成功，開始執行任務。")
            scraper.run_automation(tickers, model, dest_path)

        except Exception as e:
            logger.error(f"處理模型 {model} 時發生錯誤: {e}", exc_info=True)
            scraper.failed_tickers.extend([f"{t} ({model})" for t in tickers])
        finally:
            if scraper.driver:
                scraper.close_driver()

    def _run_single_window_task(self):
        selected_models = [model for model, var in self.selected_models.items() if var.get()]
        current_settings = settings.load_settings()
        current_settings["last_selected_models"] = selected_models
        settings.save_settings(current_settings)
        
        logger.info("--- 自動化開始 (單視窗模式) ---")
        
        port = config.REMOTE_DEBUGGING_PORTS[0]
        user_data_dir = config.get_chrome_user_data_dir(port)
        temp_path_for_port = config.get_temp_download_path_for_port(port)
        
        scraper = LietaScraper(download_path=temp_path_for_port, port=port)
        self.scrapers = [scraper]

        try:
            if not chrome_launcher.launch_chrome_in_debug_mode(port, user_data_dir):
                raise Exception("無法啟動 Chrome 偵錯實例。")
            
            time.sleep(5)

            if not scraper.setup_driver():
                raise Exception("無法連接到 WebDriver。")

            if not scraper.check_login_status():
                logger.error("使用者未登入。請先登入 Lieta Research 網站後再開始自動化。")
                raise Exception("使用者未登入。")

            all_failed_tickers = []
            for model in selected_models:
                failed = scraper.run_automation(self.tickers.copy(), model, self.destination_path)
                all_failed_tickers.extend(failed)
            
            total_tasks = len(selected_models) * len(self.tickers)
            if self.root.winfo_exists():
                self.show_summary(total_tasks, all_failed_tickers)

        except Exception as e:
            logger.error(f"單視窗模式執行失敗: {e}", exc_info=True)
            # The following messagebox is removed as the error is already logged.
            # if self.root.winfo_exists():
            #     self.root.after(0, lambda msg=str(e): messagebox.showerror("錯誤", f"自動化執行失敗: {msg}"))
        finally:
            if scraper.driver:
                scraper.close_driver()

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

    def show_summary(self, total_tasks, failed_tickers):
        success_count = total_tasks - len(failed_tickers)
        summary_msg = f"任務完成！\n\n總計: {total_tasks}\n成功: {success_count}\n失敗: {len(failed_tickers)}"
        if failed_tickers:
            unique_failures = sorted(list(set(failed_tickers)))
            summary_msg += f"\n\n失敗的項目 ({len(unique_failures)} 個):\n" + "\n".join(unique_failures)
        logger.info(f"任務總結: {summary_msg.replace('任務完成！', '').strip()}")

    def cleanup(self):
        try:
            if os.path.exists(self.temp_download_path_base):
                shutil.rmtree(self.temp_download_path_base)
                logger.info(f"已清除暫存資料夾: {self.temp_download_path_base}")
        except OSError as e:
            logger.warning(f"無法自動刪除暫存資料夾: {e}", exc_info=True)

    def _kill_chrome_processes(self):
        if sys.platform != "win32": return
        logger.info("正在嘗試關閉由本程式啟動的 Chrome 偵錯視窗...")
        ports_to_check = [scraper.port for scraper in self.scrapers if scraper.port]
        if not ports_to_check: return

        try:
            pids_to_kill = set()
            cmd = "netstat -aon"
            result = subprocess.check_output(cmd, shell=True, text=True, encoding='utf-8', errors='ignore')
            
            for port in ports_to_check:
                match = re.search(r'TCP\s+127\.0\.0\.1:' + str(port) + r'\s+.*?\s+LISTENING\s+(\d+)', result)
                if match:
                    pids_to_kill.add(match.group(1))
            
            if not pids_to_kill: return

            for pid in pids_to_kill:
                try:
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, check=True, capture_output=True)
                    logger.info(f"已成功終止 PID: {pid}")
                except subprocess.CalledProcessError:
                    pass
        except Exception as e:
            logger.error(f"關閉 Chrome 程序時發生未預期錯誤: {e}", exc_info=True)

    def on_closing(self, force_close=False):
        if self.automation_running and not messagebox.askokcancel("警告", "自動化正在執行中，確定要強制關閉程式嗎？"):
            return
        
        if force_close or messagebox.askokcancel("結束", "確定要關閉程式嗎？"):
            logger.info("正在關閉應用程式...")
            self.automation_running = False
            for scraper in self.scrapers:
                if scraper.driver: scraper.close_driver()
            self._kill_chrome_processes()
            self.cleanup()
            self.root.destroy()

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, on_close_callback):
        super().__init__(parent)
        self.parent = parent
        self.on_close_callback = on_close_callback
        self.transient(parent)
        self.title("設定")
        self.geometry("450x400")
        self.resizable(False, False)

        self.settings = settings.load_settings()

        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(expand=True, fill="both")

        # --- General Settings ---
        general_frame = ttk.LabelFrame(main_frame, text="通用設定", padding=10)
        general_frame.pack(fill="x", pady=5)

        self.multi_window_var = tk.BooleanVar(value=self.settings.get("enable_multi_window", False))
        multi_window_cb = ttk.Checkbutton(general_frame, text="啟用多視窗下載 (實驗性功能)", variable=self.multi_window_var)
        multi_window_cb.pack(anchor="w")

        # --- Scheduler Settings ---
        scheduler_frame = ttk.LabelFrame(main_frame, text="自動排程設定", padding=10)
        scheduler_frame.pack(fill="x", pady=10)

        self.schedule_enabled_var = tk.BooleanVar()
        self.schedule_check = ttk.Checkbutton(scheduler_frame, text="啟用自動執行", variable=self.schedule_enabled_var, command=self.toggle_schedule_widgets)
        self.schedule_check.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        
        admin_warning_label = ttk.Label(scheduler_frame, text="(需要系統管理員權限)", foreground="gray")
        admin_warning_label.grid(row=0, column=1, columnspan=2, sticky="w", padx=(100, 0))

        self.schedule_type_var = tk.StringVar()
        self.daily_radio = ttk.Radiobutton(scheduler_frame, text="每日", variable=self.schedule_type_var, value="DAILY")
        self.weekdays_radio = ttk.Radiobutton(scheduler_frame, text="週一至週五", variable=self.schedule_type_var, value="WEEKDAYS")
        self.daily_radio.grid(row=1, column=0, sticky="w", padx=(15, 0))
        self.weekdays_radio.grid(row=1, column=1, sticky="w")

        time_label = ttk.Label(scheduler_frame, text="執行時間 (24小時制):")
        time_label.grid(row=2, column=0, sticky="w", pady=(10, 0), padx=(15,0))

        self.hour_var = tk.StringVar()
        self.hour_combo = ttk.Combobox(scheduler_frame, textvariable=self.hour_var, values=[f"{h:02d}" for h in range(24)], width=5, state="readonly")
        self.hour_combo.grid(row=3, column=0, sticky="w", padx=(15,0))

        colon_label = ttk.Label(scheduler_frame, text=":")
        colon_label.grid(row=3, column=1, sticky="w", padx=5)

        self.minute_var = tk.StringVar()
        self.minute_combo = ttk.Combobox(scheduler_frame, textvariable=self.minute_var, values=[f"{m:02d}" for m in range(60)], width=5, state="readonly")
        self.minute_combo.grid(row=3, column=2, sticky="w")

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(side="bottom", fill="x", pady=(20, 0))

        self.save_button = ttk.Button(button_frame, text="儲存並關閉", command=self.save_and_close)
        self.save_button.pack(side="right", padx=5)

        self.cancel_button = ttk.Button(button_frame, text="取消", command=self.cancel_and_close)
        self.cancel_button.pack(side="right")
        
        self.load_settings_to_ui()
        self.toggle_schedule_widgets()
        
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.cancel_and_close)

    def load_settings_to_ui(self):
        self.multi_window_var.set(self.settings.get('enable_multi_window', False))
        self.schedule_enabled_var.set(self.settings.get('schedule_enabled', False))
        schedule_time = self.settings.get('schedule_time', '17:00')
        hour, minute = schedule_time.split(':')
        self.hour_var.set(hour)
        self.minute_var.set(minute)
        self.schedule_type_var.set(self.settings.get('schedule_type', 'DAILY'))

    def toggle_schedule_widgets(self):
        is_enabled = self.schedule_enabled_var.get()
        state = "normal" if is_enabled else "disabled"
        self.hour_combo.config(state=state if is_enabled else "readonly")
        self.minute_combo.config(state=state if is_enabled else "readonly")
        self.daily_radio.config(state=state)
        self.weekdays_radio.config(state=state)

    def save_and_close(self):
        """Collects data from UI and passes it to the callback."""
        new_settings = {
            'enable_multi_window': self.multi_window_var.get(),
            'schedule_enabled': self.schedule_enabled_var.get(),
            'schedule_time': f"{self.hour_var.get()}:{self.minute_var.get()}",
            'schedule_type': self.schedule_type_var.get()
        }
        self.on_close_callback(new_settings)
        self.destroy()

    def cancel_and_close(self):
        """Closes the window and signals no changes were made."""
        self.on_close_callback(None)
        self.destroy()