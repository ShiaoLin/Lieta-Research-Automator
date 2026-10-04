import argparse
import json
from pathlib import Path
import sys

from . import config


def _self_check(output):
    """Validate the frozen distribution without opening Chrome or fetching data."""
    import PIL
    import selenium
    import tkinter
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    from .gui import TickerApp
    from .scraper import LietaScraper

    manager = SeleniumManager()._get_binary()
    icon = Path(getattr(sys, "_MEIPASS", config.BASE_DIR)) / "settings.png"
    from .agent_control import ControlHost
    from .agent_plan import AgentPlan
    report = {"version": "1.3.0", "agent_protocol": 1, "models": list(config.MODELS), "python": sys.version,
              "selenium": selenium.__version__, "pillow": PIL.__version__,
              "tk": tkinter.TkVersion, "tcl": tkinter.Tcl().eval("info patchlevel"),
              "selenium_manager": str(manager),
              "manager_exists": manager.is_file(), "icon_exists": icon.is_file()}
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["manager_exists"] and report["icon_exists"] else 1


def _run_automated(args):
    from . import settings
    from .logger import logger
    from .batch import BatchRunner
    from .instance_lock import InstanceLock

    runner = None
    try:
        with InstanceLock():
            saved = settings.load_settings()
            resume = args.resume_batch or args.resume
            tickers = None
            if not resume:
                ticker_file = args.tickers_file or saved.get("last_ticker_path")
                if not ticker_file:
                    raise ValueError("請提供 ticker 清單。")
                tickers = Path(ticker_file).read_text(encoding="utf-8-sig").splitlines()
            multi = args.multi_window if args.multi_window is not None else saved.get("enable_multi_window", True)
            runner = BatchRunner(tickers, args.models or saved.get("last_selected_models"),
                                 args.destination or saved.get("last_destination_path"),
                                 multi=multi, limit=args.max_inflight, resume=resume)
            return runner.run()
    except KeyboardInterrupt:
        if runner:
            runner.request_stop()
            for thread in runner.threads:
                thread.join()
        return 1
    except Exception:
        logger.exception("背景下載無法完成。")
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lieta Research 自動化工具 v1.3.0")
    parser.add_argument('--agent', choices=['start', 'status', 'resume', 'retry', 'stop', 'continue'])
    parser.add_argument('--plan', help='AI 任務清單 JSON')
    parser.add_argument('--task-id', help='指定 AI 任務 ID')
    parser.add_argument('--request-id', help='控制請求 ID；同一操作重送時保持不變')
    parser.add_argument('--output', help='控制指令結果 JSON 的完整路徑')
    parser.add_argument('--agent-host', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument("--run-automated", action="store_true", help="使用儲存的設定執行背景下載")
    parser.add_argument("--continue-batch", metavar="BATCH_JSON", help="通知執行中的批次重新檢查指定模型登入")
    parser.add_argument("--continue-model", choices=config.MODELS)
    parser.add_argument("--resume-batch", metavar="BATCH_JSON", help="續跑整批模型紀錄")
    parser.add_argument("--max-inflight", type=int, choices=[1, 2], default=1, help="同時等待結果上限；預設 1")
    parser.add_argument("--multi-window", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--resume", metavar="RUN_JSON", help="續跑指定的 runs/*.json 紀錄")
    parser.add_argument("--tickers-file", help="背景執行使用的 ticker 清單")
    parser.add_argument("--destination", help="背景執行使用的儲存目錄")
    parser.add_argument("--models", nargs="+", choices=config.MODELS)
    parser.add_argument("--self-check", metavar="REPORT_JSON", help="驗證打包內容並輸出報告")
    args = parser.parse_args(argv)
    if args.agent:
        if not args.output or not Path(args.output).is_absolute():
            parser.error('--agent 必須提供 --output 完整路徑，供 AI 讀取確認結果。')
        if args.run_automated or args.resume or args.resume_batch or args.continue_batch or args.self_check:
            parser.error('--agent 不可混用舊版背景執行參數。')
        if args.tickers_file or args.destination or args.models or args.multi_window is not None or args.max_inflight != 1:
            parser.error('AI 模式的清單、路徑及模型請寫入 --plan，等待上限固定為 1。')
        from .agent_control import run_client
        return run_client(args)
    if args.plan or args.task_id or args.request_id or args.output:
        parser.error('AI 控制參數必須搭配 --agent。')
    if args.self_check:
        return _self_check(args.self_check)
    if args.continue_batch or args.continue_model:
        if not args.continue_batch or not args.continue_model:
            parser.error("請同時提供 --continue-batch 與 --continue-model。")
        from .batch import MODELS
        path = Path(args.continue_batch).resolve()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("status") != "running" or data.get("states", {}).get(args.continue_model, {}).get("state") != "等待登入":
            parser.error("該批次模型目前沒有等待登入。")
        path.with_name(path.stem + f".continue-{MODELS.index(args.continue_model)}").touch()
        return 0
    if args.resume and args.resume_batch:
        parser.error("--resume 與 --resume-batch 不可同時使用。")
    if (args.resume or args.resume_batch) and (args.tickers_file or args.destination or args.models):
        parser.error("--resume 使用原紀錄的設定，不能與其他資料來源參數併用。")
    if args.run_automated or args.resume or args.resume_batch:
        return _run_automated(args)
    if args.tickers_file or args.destination or args.models:
        parser.error("請加上 --run-automated 執行背景下載。")

    import tkinter as tk
    from tkinter import messagebox
    from .gui import TickerApp
    from .logger import logger

    try:
        logger.info("正在啟動 GUI...")
        root = tk.Tk()
        from .instance_lock import InstanceLock
        with InstanceLock():
            app = TickerApp(root)
            from .agent_control import ControlHost
            host = ControlHost(app)
            try:
                root.mainloop()
            finally:
                host.close()
        logger.info("GUI 已關閉。")
        return 0
    except Exception as exc:
        logger.exception("應用程式發生無法處理的錯誤。")
        if not args.agent_host:
            messagebox.showerror("嚴重錯誤", f"請查看程式資料夾內最新的 log_*.jsonl。\n\n{exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
