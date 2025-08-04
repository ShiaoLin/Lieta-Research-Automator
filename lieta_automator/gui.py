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


class TickerApp:
    """
    The main GUI for the application.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("Lieta Research 自動化工具")
        self.root.geometry("600x600") # Increased height for settings button

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
        """
        Safely prepares the main temporary download directory.
        It creates the base directory if it doesn't exist and clears any
        subdirectories from previous runs.
        """
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
            messagebox.showerror("嚴重錯誤", f"無法準備暫存資料夾，請檢查權限。\n\n{e}")
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
            self.settings_icon = ImageTk.PhotoImage(Image.open("setting.png").resize((24, 24), Image.Resampling.LANCZOS))
            settings_button = ttk.Button(top_frame, image=self.settings_icon, command=self._open_settings_window)
            settings_button.pack(side="right")
        except FileNotFoundError:
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
        settings_win = Toplevel(self.root)
        settings_win.title("設定")
        settings_win.geometry("350x200")
        settings_win.transient(self.root)
        settings_win.grab_set()

        frame = ttk.Frame(settings_win, padding=15)
        frame.pack(fill="both", expand=True)

        multi_window_var = tk.BooleanVar(value=self.user_settings.get("enable_multi_window", False))
        multi_window_cb = ttk.Checkbutton(frame, text="啟用多視窗下載", variable=multi_window_var)
        multi_window_cb.pack(anchor="w", pady=5)

        scheduler_var = tk.BooleanVar(value=self.user_settings.get("enable_scheduler", False))
        scheduler_cb = ttk.Checkbutton(frame, text="啟用自動排程", variable=scheduler_var)
        scheduler_cb.pack(anchor="w", pady=5)

        button_frame = ttk.Frame(frame)
        button_frame.pack(side="bottom", fill="x", pady=(20, 0))

        def save_and_close():
            new_multi_window_setting = multi_window_var.get()
            was_multi_window_enabled = self.user_settings.get("enable_multi_window", False)

            # If multi-window is being disabled, clean up old profiles
            if was_multi_window_enabled and not new_multi_window_setting:
                logger.info("偵測到停用多視窗模式，正在清理舊的設定檔...")
                # Start from the second port, as the first one is the default
                for port in config.REMOTE_DEBUGGING_PORTS[1:]:
                    profile_dir_to_delete = config.get_chrome_user_data_dir(port)
                    if os.path.isdir(profile_dir_to_delete):
                        try:
                            shutil.rmtree(profile_dir_to_delete)
                            logger.info(f"已成功刪除設定檔資料夾: {os.path.basename(profile_dir_to_delete)}")
                        except Exception as e:
                            logger.error(f"刪除資料夾 {os.path.basename(profile_dir_to_delete)} 時發生錯誤: {e}", exc_info=True)

            settings.save_setting("enable_multi_window", new_multi_window_setting)
            
            if scheduler_var.get():
                messagebox.showinfo("提示", "「自動排程」功能尚在規劃中，本次設定將不會儲存。", parent=settings_win)
                settings.save_setting("enable_scheduler", False)
            else:
                settings.save_setting("enable_scheduler", False)

            self.user_settings = settings.load_settings()
            logger.info("設定已儲存。")
            settings_win.destroy()

        save_button = ttk.Button(button_frame, text="儲存", command=save_and_close)
        save_button.pack(side="right", padx=5)

        cancel_button = ttk.Button(button_frame, text="取消", command=settings_win.destroy)
        cancel_button.pack(side="right")

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
        
        if not self.automation_running:
            self.start_button.config(state="normal" if is_valid else "disabled")
        
        self.open_dest_button.config(state="normal" if has_dest else "disabled")

    def start_automation_thread(self):
        if self.automation_running:
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
                self.root.after(0, lambda: messagebox.showerror("嚴重錯誤", f"自動化過程中發生嚴重錯誤，請查看 log.jsonl。\n\n{e}"))
        finally:
            if self.root.winfo_exists():
                self.automation_running = False
                self.toggle_ui_state(True)

    def _run_multi_window_task(self):
        selected_models = [model for model, var in self.selected_models.items() if var.get()]
        settings.save_setting("last_selected_models", selected_models)

        if len(selected_models) > len(config.REMOTE_DEBUGGING_PORTS):
            msg = f"選擇的模型數量 ({len(selected_models)}) 超過可用埠號數量 ({len(config.REMOTE_DEBUGGING_PORTS)})。"
            logger.error(msg)
            self.root.after(0, lambda: messagebox.showerror("錯誤", msg))
            return

        logger.info("--- 自動化開始 (多視窗模式) ---")
        
        # --- Phase 1: Prepare all profiles BEFORE launching any Chrome instances ---
        logger.info("階段 1: 準備並同步所有 Chrome 設定檔...")
        profiles_to_launch = []
        for i, model in enumerate(selected_models):
            port = config.REMOTE_DEBUGGING_PORTS[i]
            user_data_dir = config.get_chrome_user_data_dir(port)
            profiles_to_launch.append({'port': port, 'user_data_dir': user_data_dir, 'model': model})
            
            # This will create the directory and copy files from the main profile if it's the first time.
            # We do this for all profiles before any Chrome process is started to avoid file locks.
            chrome_launcher._sync_profile_if_new(port, user_data_dir)
        logger.info("所有設定檔準備完成。")


        # --- Phase 2: Launch Chrome instances and run automation tasks ---
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
            time.sleep(1) # Stagger the launch slightly

        for thread in threads:
            thread.join()

        logger.info("--- 所有線程執行完畢 ---")
        
        all_failed_tickers = []
        for scraper in self.scrapers:
            all_failed_tickers.extend(scraper.failed_tickers)
        
        total_tasks = len(selected_models) * len(self.tickers)
        
        if self.root.winfo_exists():
            self.show_summary(total_tasks, all_failed_tickers)

    def _run_single_window_task(self):
        selected_models = [model for model, var in self.selected_models.items() if var.get()]
        settings.save_setting("last_selected_models", selected_models)
        
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
                self.root.after(0, lambda: messagebox.showerror("需要登入", "請先登入 Lieta Research 網站後再開始自動化。"))
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
            if self.root.winfo_exists():
                self.root.after(0, lambda msg=str(e): messagebox.showerror("錯誤", f"自動化執行失敗: {msg}"))
        finally:
            if scraper.driver:
                scraper.close_driver()

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
                     self.root.after(0, lambda: messagebox.showerror("需要登入", "請先登入 Lieta Research 網站後再開始自動化。"))
                raise Exception(f"[Port {port}] 使用者未登入。")

            logger.info(f"[Port {port}] WebDriver 設定成功，開始執行任務。")
            scraper.run_automation(tickers, model, dest_path)

        except Exception as e:
            logger.error(f"處理模型 {model} 時發生錯誤: {e}", exc_info=True)
            scraper.failed_tickers.extend([f"{t} ({model})" for t in tickers])
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
        self.root.after(0, lambda: messagebox.showinfo("任務總結", summary_msg))

    def cleanup(self):
        try:
            if os.path.exists(self.temp_download_path_base):
                shutil.rmtree(self.temp_download_path_base)
                logger.info(f"已清除暫存資料夾: {self.temp_download_path_base}")
        except OSError as e:
            logger.warning(f"無法自動刪除暫存資料夾: {e}", exc_info=True)

    def _kill_chrome_processes(self):
        if sys.platform != "win32":
            return
        
        logger.info("正在嘗試關閉由本程式啟動的 Chrome 偵錯視窗...")
        ports_to_check = [scraper.port for scraper in self.scrapers if scraper.port]
        if not ports_to_check:
            return

        try:
            # Find PIDs for all relevant ports first
            pids_to_kill = set()
            cmd = "netstat -aon"
            result = subprocess.check_output(cmd, shell=True, text=True, encoding='utf-8', errors='ignore')
            
            for port in ports_to_check:
                # Regex to find a line with the listening port and capture the PID
                match = re.search(r'TCP\s+127\.0\.0\.1:' + str(port) + r'\s+.*?\s+LISTENING\s+(\d+)', result)
                if match:
                    pid = match.group(1)
                    pids_to_kill.add(pid)
                    logger.info(f"找到 Port {port} 對應的 PID: {pid}")

            # Kill all found PIDs
            if not pids_to_kill:
                logger.info("未找到需要關閉的 Chrome 程序。")
                return

            for pid in pids_to_kill:
                try:
                    # Use capture_output to prevent taskkill output from polluting the console
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, check=True, capture_output=True)
                    logger.info(f"已成功終止 PID: {pid}")
                except subprocess.CalledProcessError as e:
                    # This might happen if the process was already closed, which is fine.
                    logger.warning(f"終止 PID {pid} 失敗 (可能已關閉): {e.stderr.decode('cp950', errors='ignore').strip()}")

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"執行系統指令時發生錯誤: {e}")
        except Exception as e:
            logger.error(f"關閉 Chrome 程序時發生未預期錯誤: {e}", exc_info=True)


    def on_closing(self, force_close=False):
        if self.automation_running:
            if not messagebox.askokcancel("警告", "自動化正在執行中，確定要強制關閉程式嗎?"):
                return
        
        if force_close or messagebox.askokcancel("結束", "確定要關閉程式嗎?"):
            logger.info("正在關閉應用程式...")
            self.automation_running = False
            
            # First, try to gracefully quit drivers
            for scraper in self.scrapers:
                if scraper.driver:
                    scraper.close_driver()
            
            # Then, forcefully kill any remaining Chrome processes we started
            self._kill_chrome_processes()

            self.cleanup()
            self.root.destroy()