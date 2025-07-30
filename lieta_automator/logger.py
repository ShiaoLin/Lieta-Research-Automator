import json
import logging
import os
import tkinter as tk
from logging import FileHandler, Handler, LogRecord

# --- Configuration ---
LOG_FILE_PATH = "log.jsonl"  # Use .jsonl extension for JSON Lines
LOG_LEVEL = logging.INFO

# --- Custom JSON Formatter ---
class JsonFormatter(logging.Formatter):
    """
    Formats log records as a JSON string (JSONL format).
    """
    def format(self, record: LogRecord) -> str:
        # Create a clean copy of the record's dict to avoid modifying the original
        log_object = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "source": {
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            },
        }
        # Include exception info if it exists
        if record.exc_info:
            log_object["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_object, ensure_ascii=False)

# --- Custom Tkinter Handler ---
class TkinterLogHandler(Handler):
    """
    A logging handler that emits records to a Tkinter Text widget.
    """
    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self.text_widget = text_widget
        # Use a simple, human-readable format for the GUI
        self.formatter = logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S')

    def emit(self, record: LogRecord):
        if not self.text_widget.winfo_exists():
            return
            
        msg = self.format(record)
        
        def append():
            if not self.text_widget.winfo_exists():
                return
            self.text_widget.config(state="normal")
            self.text_widget.insert(tk.END, msg + "\n")
            self.text_widget.see(tk.END)
            self.text_widget.config(state="disabled")
        
        self.text_widget.after(0, append)

# --- Setup Function ---
def setup_logging():
    """
    Configures the root logger for the application.
    - Clears existing handlers.
    - Adds a JSON file handler.
    """
    # Clear previous log file if it exists
    if os.path.exists(LOG_FILE_PATH):
        os.remove(LOG_FILE_PATH)

    logger = logging.getLogger()
    logger.setLevel(LOG_LEVEL)

    # Clear any existing handlers to prevent duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create and add the JSON file handler
    file_handler = FileHandler(LOG_FILE_PATH, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    # The Tkinter handler is added from the GUI module
    return logger

# --- Global Logger Instance ---
# This ensures that any module importing 'logger' gets the same pre-configured instance.
logger = setup_logging()
