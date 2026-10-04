"""Atomic per-run progress; a fresh run still fetches fresh intraday data."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import uuid

from . import config
from .storage import read_bytes, replace_file


class RunJournal:
    @classmethod
    def read_only(cls, path):
        journal = cls.__new__(cls)
        journal.path = Path(path)
        journal.data = json.loads(journal.path.read_text(encoding='utf-8-sig'))
        return journal

    def __init__(self, tickers, model, destination, resume_path=None):
        if resume_path:
            self.path = Path(resume_path)
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if (self.data["model"] != model or self.data["tickers"] != tickers
                    or os.path.normcase(os.path.abspath(self.data["destination"]))
                    != os.path.normcase(os.path.abspath(destination))):
                raise ValueError("續跑紀錄與本次 ticker、模型或儲存路徑不符。")
        else:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            self.path = Path(config.BASE_DIR) / "runs" / f"{run_id}.json"
            self.data = {"model": model, "tickers": tickers,
                         "destination": os.path.abspath(destination), "items": {}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save()

    def completed(self, ticker):
        item = self.data["items"].get(ticker, {})
        path = Path(item.get("path", ""))
        if item.get("status") != "complete" or not path.is_file():
            return False
        try:
            content = read_bytes(path)
            if item.get("content"):
                return contains_block(content.decode("utf-8"), item["content"])
            return hashlib.sha256(content).hexdigest() == item.get("sha256")
        except (OSError, UnicodeError):
            return False

    def record(self, ticker, *, path=None, error=None, content=None):
        self.data["items"][ticker] = {
            "status": "failed" if error else "complete", "path": str(path or ""),
            "error": str(error or ""), "updated": datetime.now().isoformat(timespec="seconds"),
            "content": content,
            "sha256": hashlib.sha256(read_bytes(path)).hexdigest() if path else None,
        }
        self._save()

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(temporary, self.path)


def contains_block(existing, text):
    """Exact complete lines, including multi-line TV Code blocks."""
    haystack = existing.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    needle = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return bool(needle) and "\n" + needle + "\n" in "\n" + haystack + "\n"
