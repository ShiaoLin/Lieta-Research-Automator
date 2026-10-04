"""Identify Table results without requiring a chart title or altering exports."""
import html
from html.parser import HTMLParser
import json
import re


def clean_text(value):
    return html.unescape(re.sub(r'<[^>]+>', ' ', str(value)))


def has_table_headers(text):
    words = set(re.findall(r'[a-z]+', clean_text(text).lower()))
    return {'expiration', 'gex', 'dex'} <= words


def is_table_text(text):
    # A header alone or a hidden/empty table is not a completed result.
    return has_table_headers(text) and bool(re.search(r'\bTotal\b|\b\d{4}-\d{2}-\d{2}\b', text, re.I)) and bool(re.search(r'\d', text))


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.rows, self.row, self.cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.rows = []
        elif self.rows is not None:
            if tag == 'tr':
                self.row = []
            elif tag in ('td', 'th') and self.row is not None:
                self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(''.join(self.cell))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == 'table' and self.rows is not None:
            self.tables.append(self.rows)
            self.rows = None


def valid_table_export(content):
    """Check real rows in a Plotly table export or a native HTML table."""
    decoder = json.JSONDecoder()
    pattern = r'''Plotly\.newPlot\s*\(\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')\s*,\s*'''
    for match in re.finditer(pattern, content):
        try:
            traces, _ = decoder.raw_decode(content, match.end())
        except (ValueError, TypeError):
            continue
        if not isinstance(traces, list):
            continue
        for trace in traces:
            if not isinstance(trace, dict) or trace.get('type') != 'table':
                continue
            header, cells = trace.get('header'), trace.get('cells')
            if not isinstance(header, dict) or not isinstance(cells, dict):
                continue
            headers = header.get('values', [])
            columns = cells.get('values', [])
            if not isinstance(headers, list) or not has_table_headers(' '.join(map(str, headers))):
                continue
            if (isinstance(columns, list) and len(columns) == len(headers) and
                    all(isinstance(column, list) and column for column in columns) and
                    len({len(column) for column in columns}) == 1 and
                    is_table_text(' '.join(map(str, headers + [v for c in columns for v in c])))):
                return True
    parser = _Tables()
    parser.feed(content)
    for rows in parser.tables:
        if len(rows) > 1 and has_table_headers(' '.join(rows[0])):
            if any(len(row) == len(rows[0]) and is_table_text(' '.join(rows[0] + row)) for row in rows[1:]):
                return True
    return False
