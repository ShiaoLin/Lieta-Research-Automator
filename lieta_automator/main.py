import tkinter as tk
from tkinter import messagebox

# Setup logging first, so it's available everywhere.
from .logger import logger
from .gui import TickerApp

def main():
    """
    Main entry point for the application.
    Initializes and runs the Tkinter GUI.
    The GUI is now responsible for launching and managing Chrome instances.
    """
    try:
        logger.info("正在啟動 GUI...")
        root = tk.Tk()
        app = TickerApp(root)
        root.mainloop()
        logger.info("GUI 已關閉。")
    except Exception as e:
        logger.critical(f"應用程式發生無法處理的嚴重錯誤: {e}", exc_info=True)
        messagebox.showerror("嚴重錯誤", f"應用程式發生無法處理的錯誤，請查看 log.jsonl。\n\n{e}")

if __name__ == "__main__":
    main()