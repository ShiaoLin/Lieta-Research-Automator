import json
import logging
import os
import tkinter as tk
from logging import FileHandler, Handler, LogRecord

from . import config # Import the config module

# --- Configuration ---
# Use the absolute path from the config module
LOG_FILE_PATH = os.path.join(config.BASE_DIR, "log.jsonl")
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

import queue

# --- Custom Tkinter Handler ---
class TkinterLogHandler(Handler):
    """
    A logging handler that puts log records into a thread-safe queue.
    The GUI thread is responsible for pulling records from the queue and
    displaying them.
    """
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: LogRecord):
        """
        Puts the log record into the queue.
        No direct GUI manipulation happens here to ensure thread safety.
        """
        self.log_queue.put(record)

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
