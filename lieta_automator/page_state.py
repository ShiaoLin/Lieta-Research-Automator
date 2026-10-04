"""Read a single DOM snapshot; never retain elements across render cycles."""

import hashlib
import re

from .request_flow import PageState
from .notice_monitor import NOTICE_MONITOR_SCRIPT
from .table_result import is_table_text


SNAPSHOT_SCRIPT = "const noticeMonitor = " + NOTICE_MONITOR_SCRIPT + r""";
noticeMonitor.scan(document.body);
const visible = el => el && el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
const submit = document.querySelector('button[type="submit"]');
const model = document.querySelector('button[role="combobox"]');
const input = document.querySelector('input[placeholder="Ticker"]');
const buttons = Array.from(document.querySelectorAll('button'));
const download = buttons.find(el => visible(el) && /下載|download/i.test(el.innerText));
const loginPage = !visible(input) && (
    /^\/auth(?:\/|$)/i.test(location.pathname) ||
    Array.from(document.querySelectorAll('a[href="/auth"], input[type="password"]')).some(visible));
const charts = Array.from(document.querySelectorAll('svg.main-svg')).filter(visible);
const tables = Array.from(document.querySelectorAll('table')).filter(visible);
const paragraphs = Array.from(document.querySelectorAll('p')).filter(visible);
const capture = el => ({node: el, html: el.outerHTML, text: el.tagName.toLowerCase() === 'svg'
    ? Array.from(el.querySelectorAll('text')).map(text => text.textContent || '').join('\n')
    : el.tagName.toLowerCase() === 'table'
    ? Array.from(el.querySelectorAll('th,td')).map(cell => cell.textContent || '').join('\n')
    : (el.textContent || '')});
return {
    authenticated: !loginPage,
    sessionExpired: noticeMonitor.sessionExpired,
    selectedModel: model ? model.innerText.trim() : '',
    inputTicker: input ? input.value.trim().toUpperCase() : '',
    busy: Boolean(submit && (submit.disabled || submit.getAttribute('aria-busy') === 'true' ||
        /loading|載入中|處理中|計算中/i.test(submit.innerText))),
    downloadable: Boolean(download && !download.disabled),
    charts: charts.map(capture),
    tables: tables.map(capture),
    paragraphs: paragraphs.map(capture),
    errors: noticeMonitor.events.map(event => 'toast:' + event.id + ':' + event.text)
};
"""


def contains_ticker(text, ticker):
    return bool(re.search(r"(?<![A-Z0-9_.^\-])" + re.escape(ticker.upper())
                          + r"(?![A-Z0-9_.^\-])", text.upper()))


def result_key(parts):
    # Selenium's element id is already cached locally; reading it cannot go stale.
    value = "\n".join(part["node"].id + "\n" + part["html"] for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if parts else ""


def read_page_state(driver, ticker, model):
    snapshot = driver.execute_script(SNAPSHOT_SCRIPT)
    if model == "TV Code":
        pattern = re.compile(r"^\s*" + re.escape(ticker) + r"\s*:", re.I | re.M)
        parts = [part for part in snapshot["paragraphs"] if pattern.search(part["text"])]
        text = "\n".join(part["text"].strip() for part in parts)
        matches = bool(parts)
        ready = bool(text)
    elif model == "Table":
        parts = [part for part in snapshot['charts'] + snapshot.get('tables', [])
                 if is_table_text(part['text'])]
        text = '\n'.join(part['text'] for part in parts)
        # Freshness is enforced by the fetch loop. Every Table ticker starts
        # from a clean document because the table itself has no ticker title.
        matches = bool(parts) and snapshot.get('inputTicker') == ticker.upper()
        ready = bool(parts) and snapshot['downloadable']
    else:
        parts = snapshot["charts"]
        text = "\n".join(part["text"] for part in parts)
        matches = contains_ticker(text, ticker) and bool(re.search(
            r"\b" + re.escape(model) + r"\b", text, re.I))
        ready = bool(parts) and snapshot["downloadable"]
    return PageState(
        result_key=result_key(parts), text=text,
        matches=matches and snapshot["selectedModel"] == model,
        ready=ready, busy=snapshot["busy"],
        errors=frozenset(snapshot["errors"]),
        authenticated=snapshot["authenticated"],
        session_expired=snapshot["sessionExpired"],
    )
