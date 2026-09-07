"""Explicit live two-run comparison. Run independently via a one-shot OS task."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def inspect_batch(path):
    batch = json.loads(path.read_text(encoding="utf-8"))
    complete, errors = 0, []
    for model, journal_path in batch["journals"].items():
        journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
        for ticker in batch["tickers"]:
            item = journal["items"].get(ticker, {})
            if item.get("status") != "complete":
                continue
            file = Path(item["path"])
            try:
                contents = file.read_bytes()
                if not file.resolve().is_relative_to(Path(batch["destination"]).resolve()):
                    raise ValueError("outside destination")
                if model == "TV Code":
                    text = contents.decode("utf-8").replace("\r\n", "\n").strip("\n")
                    if "\n" + item["content"].strip() + "\n" not in "\n" + text + "\n":
                        raise ValueError("TV Code missing")
                    if not re.fullmatch(r"\d{8}_TV Code\.txt", file.name):
                        raise ValueError("TV filename")
                else:
                    if hashlib.sha256(contents).hexdigest() != item["sha256"]:
                        raise ValueError("checksum")
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2};\d{2}_" + re.escape(ticker + "_" + model) + r"\.html", file.name):
                        raise ValueError("filename")
                    if "<html" not in contents.decode("utf-8-sig").lower():
                        raise ValueError("not HTML")
                complete += 1
            except Exception as exc:
                errors.append(f"{model}|{ticker}: {exc}")
    return batch, complete, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    exe, report = Path(args.exe).resolve(), Path(args.report).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    count = len(set(t.strip().upper() for t in Path(args.tickers).read_text(encoding="utf-8-sig").splitlines() if t.strip())) * 4
    status = {"status": "running", "started": datetime.now().isoformat(), "expected_per_run": count,
              "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
              "ticker_sha256": hashlib.sha256(Path(args.tickers).read_bytes()).hexdigest(), "runs": []}
    write(report, status)
    for limit in (1, 2):
        destination = output / f"limit-{limit}"
        destination.mkdir(exist_ok=False)
        before = set((exe.parent / "runs").glob("batch_*.json"))
        process = subprocess.Popen([str(exe), "--run-automated", "--multi-window", "--max-inflight", str(limit),
                                    "--tickers-file", args.tickers, "--destination", str(destination),
                                    "--models", "Gamma", "Term", "Smile", "TV Code"], cwd=exe.parent)
        row = {"limit": limit, "pid": process.pid, "destination": str(destination), "status": "starting"}
        status["runs"].append(row)
        write(report, status)
        batch_path = None
        while process.poll() is None:
            if batch_path is None:
                for candidate in set((exe.parent / "runs").glob("batch_*.json")) - before:
                    try:
                        value = json.loads(candidate.read_text(encoding="utf-8"))
                        if Path(value["destination"]).resolve() == destination:
                            batch_path = candidate
                            row["batch"] = str(candidate)
                            break
                    except (OSError, ValueError, KeyError):
                        pass
            if batch_path:
                try:
                    value = json.loads(batch_path.read_text(encoding="utf-8"))
                    row.update(status=value["status"], states=value["states"])
                    write(report, status)
                except (OSError, ValueError):
                    pass
            time.sleep(5)
        row["exit_code"] = process.returncode
        if batch_path is None:
            status["status"] = "failed_to_start"
            write(report, status)
            return 2
        value, complete, errors = inspect_batch(batch_path)
        row.update(status=value["status"], verified=complete, validation_errors=errors,
                   metrics=value["history"][-1], states=value["states"])
        row["success_rate"] = complete / count
        row["extra_submissions_per_item"] = max(0, row["metrics"]["submissions"] - count) / count
        write(report, status)
        if process.returncode == 2 or errors:
            status["status"] = "verification_failed"
            write(report, status)
            return 2
        if limit == 1:
            status["status"] = "between_runs_cooldown"
            write(report, status)
            time.sleep(120)
            status["status"] = "running"
    first, second = status["runs"]
    status["candidate_for_limit_2"] = (second["success_rate"] >= first["success_rate"]
        and second["extra_submissions_per_item"] <= first["extra_submissions_per_item"]
        and second["metrics"]["elapsed_seconds"] < first["metrics"]["elapsed_seconds"])
    status["status"] = "complete"
    status["finished"] = datetime.now().isoformat()
    status["default_limit_changed"] = False
    status["caveat"] = "Single sequential comparison cannot exclude server/time-of-day variation; no auto promotion."
    write(report, status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
