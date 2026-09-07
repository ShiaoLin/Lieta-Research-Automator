import os
from pathlib import Path
import shutil
import time
from datetime import datetime
import uuid

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeDriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from . import config
from .journal import RunJournal, contains_block
from .logger import logger
from .notice_monitor import install_notice_monitor, stop_notice_monitor
from .page_state import contains_ticker, read_page_state
from .request_flow import LoginRequired, SHARED_COORDINATOR, fetch_result
from .storage import replace_file


class LietaScraper:
    """One browser adapter. All adapters share request pacing and cooldowns."""

    def __init__(self, download_path, port, coordinator=None):
        self.download_path = os.path.abspath(download_path)
        self.port = port
        self.driver = None
        self.failed_tickers = []
        self.coordinator = coordinator or SHARED_COORDINATOR
        self.journal = None
        self.stop_check = lambda: None

    def setup_driver(self):
        try:
            logger.info(f"[Port {self.port}] 正在連接到 Chrome 瀏覽器...")
            options = Options()
            options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.port}")
            self.driver = ChromeDriver(service=ChromeService(), options=options)
            self.driver.set_page_load_timeout(30)
            # Hidden/background Chrome can suspend Radix animations and ignore
            # native typing. Keep this automation tab active without stealing focus.
            self.driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
            try:
                offset = (self.port - config.REMOTE_DEBUGGING_PORTS[0]) * 50
                self.driver.set_window_size(1200, 800)
                self.driver.set_window_position(offset, offset)
            except Exception as exc:
                logger.warning(f"[Port {self.port}] 無法調整視窗: {exc}")
            return True
        except Exception:
            logger.exception(f"[Port {self.port}] 無法連接 Chrome。")
            return False

    def _wait(self, timeout=None):
        return WebDriverWait(self.driver, timeout or config.SELENIUM_TIMEOUT,
                             poll_frequency=0.3,
                             ignored_exceptions=(StaleElementReferenceException,))

    def check_login_status(self):
        try:
            self.driver.get(config.LIETA_AUTOMATION_URL)
            self._wait().until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, 'input[placeholder="Ticker"]')))
            return True
        except Exception:
            logger.warning(f"[Port {self.port}] 找不到模型表單，請確認已登入 Lieta。")
            return False

    def _select_model(self, model, *, refresh=False):
        # Navigation also discards any late response from an abandoned ticker.
        if refresh:
            self.driver.refresh()
        else:
            self.driver.get(config.LIETA_AUTOMATION_URL)
        install_notice_monitor(self.driver)
        wait = self._wait(timeout=30)
        try:
            button = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'button[role="combobox"]')))
        except Exception as exc:
            if not read_page_state(self.driver, "", model).authenticated:
                raise LoginRequired("無法開啟模型表單，請確認登入狀態。") from exc
            raise RuntimeError("模型表單未在期限內載入。") from exc
        # The server-rendered button can appear before its click handler is ready.
        # Verify the popup opened, and retry without toggling an already-open menu.
        for attempt in range(3):
            try:
                button = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'button[role="combobox"]')))
                if button.text.strip() == model:
                    return
                if button.get_attribute("aria-expanded") != "true":
                    # Keep the original app's DOM click and verify the UI response.
                    self.driver.execute_script("arguments[0].click();", button)
                option = self._wait(timeout=5).until(EC.element_to_be_clickable(
                    (By.XPATH, f"//*[@role='option' and normalize-space(.)='{model}']")))
                self.driver.execute_script("arguments[0].click();", option)
                self._wait(timeout=5).until(lambda driver: driver.find_element(
                    By.CSS_SELECTOR, 'button[role="combobox"]').text.strip() == model)
                return
            except (TimeoutException, StaleElementReferenceException):
                logger.warning(f"模型選單尚未完成互動，重新確認 ({attempt + 1}/3)。")
        raise RuntimeError(f"無法選擇模型 {model}。")

    def _wait_for_slot(self):
        remaining = self.coordinator.next_allowed - time.monotonic()
        if remaining > 0:
            logger.info(f"[Port {self.port}] 切換前等待 {remaining:.1f} 秒。")
        while True:
            remaining = self.coordinator.next_allowed - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))

    def _fill_ticker(self, ticker):
        def fill(driver):
            element = driver.find_element(By.CSS_SELECTOR, 'input[placeholder="Ticker"]')
            if not element.is_displayed() or not element.is_enabled():
                return False
            element.clear()
            element.send_keys(ticker)
            return element.get_attribute("value").strip().upper() == ticker
        try:
            self._wait().until(fill)
        except TimeoutException as exc:
            if not read_page_state(self.driver, ticker, "").authenticated:
                raise LoginRequired("已回到登入頁，請重新登入 Lieta 後續跑。") from exc
            raise

    def _restore_session(self, ticker, model):
        self._select_model(model, refresh=True)
        self._fill_ticker(ticker)

    def _submit(self):
        self._wait().until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'button[type="submit"]'))).click()

    def run_automation(self, tickers, model, destination_path, resume_path=None):
        if model not in ("Gamma", "Term", "Smile", "TV Code"):
            raise ValueError(f"不支援的模型: {model}")
        tickers = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
        if any(ticker in (".", "..") or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.^_-"
                   for c in ticker) for ticker in tickers):
            raise ValueError("Ticker 含有不支援的字元。")
        self.failed_tickers = []
        self.journal = RunJournal(tickers, model, destination_path, resume_path)
        logger.info(f"--- [{model}] 開始；續跑紀錄: {self.journal.path} ---")
        pending = [ticker for ticker in tickers if not self.journal.completed(ticker)]
        reset_page = True
        # A second pass revisits only failures after the other tickers have run.
        for pass_number in range(2):
            failed = []
            for index, ticker in enumerate(pending):
                started = time.monotonic()
                try:
                    with self.coordinator.request_slot():
                        self._wait_for_slot()
                        if reset_page:
                            self._select_model(model)
                            reset_page = False
                        self._fill_ticker(ticker)
                        logger.info(f"[{model}|{ticker}] ({index + 1}/{len(pending)}) 開始獲取。")
                        state = fetch_result(
                            lambda: read_page_state(self.driver, ticker, model),
                            self._submit, self.coordinator,
                            lambda message: logger.info(f"[{model}|{ticker}] {message}"),
                            recover_session=lambda: self._restore_session(ticker, model),
                        )
                        if model == "TV Code":
                            path = self._save_tv_code(ticker, state.text, destination_path)
                        else:
                            path = self._download_html(ticker, model, destination_path, state.result_key)
                        self.journal.record(ticker, path=path,
                                            content=state.text if model == "TV Code" else None)
                    logger.info(f"成功: [{model}|{ticker}] {time.monotonic() - started:.1f} 秒，{path}")
                except LoginRequired as exc:
                    logger.error(f"{exc} 未完成項目已保留，續跑紀錄: {self.journal.path}")
                    failed = [item for item in tickers if not self.journal.completed(item)]
                    for remaining in failed:
                        self.journal.record(remaining, error=exc)
                    self.failed_tickers = [f"{item} ({model})" for item in failed]
                    return self.failed_tickers
                except Exception as exc:
                    logger.exception(f"失敗: [{model}|{ticker}] {exc}")
                    self.journal.record(ticker, error=exc)
                    failed.append(ticker)
                    reset_page = True
            pending = failed
            if not pending:
                break
            if pass_number == 0:
                reset_page = True
                logger.info(f"[{model}] 第一輪完成，補抓 {len(pending)} 個失敗項目。")
        self.failed_tickers = [f"{ticker} ({model})" for ticker in pending]
        logger.info(f"--- [{model}] 完成，仍失敗 {len(pending)} 項 ---")
        return self.failed_tickers

    def _download_html(self, ticker, model, destination, expected_key):
        state = read_page_state(self.driver, ticker, model)
        if not state.authenticated:
            raise LoginRequired("下載前已回到登入頁，請重新登入 Lieta 後續跑。")
        if state.session_expired:
            # Never save a chart beside an authorization error. The failed item
            # will be revisited with a clean page by the existing second pass.
            raise RuntimeError("下載前出現 Unauthorized，拒絕儲存，將重新載入頁面補抓。")
        if not state.ready or not state.matches or state.busy or state.result_key != expected_key:
            raise RuntimeError("下載前圖表已變更或尚未就緒，拒絕儲存可能錯置的資料。")
        # Isolate each download so an unrelated/late file cannot be selected.
        folder = Path(self.download_path) / uuid.uuid4().hex
        folder.mkdir(parents=True)
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow", "downloadPath": str(folder),
        })
        self._wait().until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(., '下載') or contains(., 'Download')]"))).click()
        source = self._wait_for_download(folder)
        self._validate_html(source, ticker)
        target_dir = Path(destination) / model / ticker
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H;%M")
        target = target_dir / f"{timestamp}_{ticker}_{model}.html"
        temporary = target.with_suffix(".html.partial")
        # copy2 works even while Windows scanning retains the downloaded file.
        shutil.copy2(source, temporary)
        replace_file(temporary, target, on_retry=logger.info)
        return target

    def _wait_for_download(self, folder, timeout=90):
        deadline = time.monotonic() + timeout
        last = None
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            self.stop_check()
            files = list(folder.glob("*.html"))
            if len(files) == 1 and not list(folder.glob("*.crdownload")):
                try:
                    signature = (files[0], files[0].stat().st_size)
                    if signature == last and signature[1] > 0:
                        if time.monotonic() - stable_since >= 2:
                            return files[0]
                    else:
                        last, stable_since = signature, time.monotonic()
                except OSError:
                    last = None
            else:
                last = None
            time.sleep(0.3)
        raise TimeoutError("下載未完成，未將檔案列為成功。")

    @staticmethod
    def _validate_html(path, ticker):
        content = Path(path).read_text(encoding="utf-8-sig")
        lower = content.lower()
        if "<html" not in lower or "</html>" not in lower or not contains_ticker(content, ticker):
            raise ValueError(f"下載的 HTML 不完整或找不到 ticker {ticker}。")

    @staticmethod
    def _save_tv_code(ticker, text, destination):
        if not text.strip() or not contains_ticker(text, ticker):
            raise ValueError("TV Code 內容與 ticker 不符。")
        folder = Path(destination) / "TV Code"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{datetime.now():%Y%m%d}_TV Code.txt"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        # Retries/resume do not append a second copy of exactly the same code.
        if not contains_block(existing, text):
            temporary = target.with_suffix(".txt.partial")
            temporary.write_text(existing.rstrip("\n") + ("\n" if existing else "")
                                 + text.strip() + "\n", encoding="utf-8")
            replace_file(temporary, target, on_retry=logger.info)
        return target

    def close_driver(self):
        if self.driver:
            try:
                stop_notice_monitor(self.driver)
            except Exception:
                pass
            try:
                self.driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": False})
            except Exception:
                pass  # The browser may already be closed; still release WebDriver.
            try:
                self.driver.quit()
            except Exception:
                logger.exception(f"[Port {self.port}] 關閉 WebDriver 失敗。")
            finally:
                self.driver = None
