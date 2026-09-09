"""Native Tk dashboard. Presentation only; all requests stay in BatchRunner."""
import tkinter as tk
from tkinter import ttk

from .batch import MODELS

COLORS = {
    "background": "#F3F5F8", "surface": "#FFFFFF", "text": "#172B3A",
    "muted": "#526577", "border": "#D7DFE7", "accent": "#0B6655",
    "hover": "#084C40", "soft": "#E4F2EC", "warning": "#8A4B08",
    "error": "#B42332", "disabled": "#657483", "field": "#F6F8FA",
}
FONT = "Microsoft JhengHei UI"


def apply_theme(root):
    c = COLORS
    root.configure(background=c["background"])
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FONT, 10), background=c["background"], foreground=c["text"])
    style.configure("TFrame", background=c["background"])
    style.configure("Card.TFrame", background=c["surface"])
    style.configure("TLabel", background=c["background"], font=(FONT, 10))
    style.configure("Card.TLabel", background=c["surface"])
    style.configure("Muted.TLabel", foreground=c["muted"])
    style.configure("CardMuted.TLabel", background=c["surface"], foreground=c["muted"])
    style.configure("Title.TLabel", font=(FONT, 22, "bold"))
    style.configure("Heading.TLabel", font=(FONT, 12, "bold"))
    style.configure("CardHeading.TLabel", font=(FONT, 12, "bold"), background=c["surface"])
    style.configure("Metric.TLabel", font=(FONT, 20, "bold"), background=c["surface"])
    style.configure("Badge.TLabel", background=c["soft"], foreground=c["accent"], padding=(10, 5))
    style.configure("TButton", font=(FONT, 10), padding=(12, 8), background=c["surface"], bordercolor=c["border"],
                    lightcolor=c["surface"], darkcolor=c["surface"], focuscolor=c["accent"])
    style.map("TButton", background=[("active", c["soft"])],
              foreground=[("disabled", c["disabled"])], bordercolor=[("focus", c["accent"])])
    style.configure("Primary.TButton", background=c["accent"], foreground="white",
                    bordercolor=c["accent"], focuscolor="white", font=(FONT, 10, "bold"))
    style.map("Primary.TButton", background=[("disabled", c["border"]), ("active", c["hover"])],
              foreground=[("disabled", c["muted"]), ("!disabled", "white")])
    style.configure("Stop.TButton", foreground=c["error"])
    style.configure("TCheckbutton", font=(FONT, 10), background=c["surface"], padding=(4, 8))
    style.map("TCheckbutton", background=[("active", c["soft"])])
    style.configure("Horizontal.TProgressbar", troughcolor=c["field"], background=c["accent"],
                    borderwidth=0, bordercolor=c["field"], lightcolor=c["field"], darkcolor=c["field"], thickness=6)
    style.configure("TLabelframe", background=c["surface"], bordercolor=c["border"])
    style.configure("TLabelframe.Label", background=c["surface"], font=(FONT, 10, "bold"))


def card(parent, padding=18):
    outer = tk.Frame(parent, background=COLORS["surface"], highlightthickness=1,
                     highlightbackground=COLORS["border"])
    body = ttk.Frame(outer, style="Card.TFrame", padding=padding)
    body.pack(fill="both", expand=True)
    return outer, body


def path_label(parent, text):
    label = ttk.Label(parent, text=text, style="CardMuted.TLabel", wraplength=260, justify="left")
    label.pack(fill="x", pady=(8, 12))
    # Paths wrap to actual available width, including at larger system text sizes.
    label.bind("<Configure>", lambda e: label.configure(wraplength=max(80, e.width)))
    return label


def build_dashboard(app):
    root = app.root
    apply_theme(root)
    root.geometry("1120x840")
    root.minsize(900, 620)

    # Vertical scrolling keeps every action reachable on short/high-DPI displays.
    shell = ttk.Frame(root)
    shell.pack(fill="both", expand=True)
    canvas = tk.Canvas(shell, background=COLORS["background"], highlightthickness=0)
    scroll = ttk.Scrollbar(shell, command=canvas.yview)
    scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    canvas.configure(yscrollcommand=scroll.set)
    content = ttk.Frame(canvas, padding=24)
    window = canvas.create_window(0, 0, anchor="nw", window=content)
    content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
    def wheel(event):
        if event.widget.winfo_toplevel() == root and event.widget not in (app.log_text, app.summary_text):
            canvas.yview_scroll(-int(event.delta / 120), "units")
    root.bind("<MouseWheel>", wheel, add=True)
    def reveal_focus(event):
        widget = event.widget
        if widget.winfo_toplevel() != root or not isinstance(widget, (ttk.Button, ttk.Checkbutton, tk.Text)):
            return
        top = widget.winfo_rooty() - content.winfo_rooty()
        height = max(1, content.winfo_height())
        visible = canvas.canvasy(0)
        if top < visible:
            canvas.yview_moveto(max(0, top - 12) / height)
        elif top + widget.winfo_height() > visible + canvas.winfo_height():
            canvas.yview_moveto((top + widget.winfo_height() + 12 - canvas.winfo_height()) / height)
    root.bind("<FocusIn>", reveal_focus, add=True)

    header = ttk.Frame(content)
    header.pack(fill="x", pady=(0, 20))
    app.settings_button = ttk.Button(header, text="偏好設定", command=app._open_settings_window)
    app.settings_button.pack(side="right")
    ttk.Label(header, text="LIETA  /  AUTOMATOR", style="Muted.TLabel").pack(anchor="w")
    ttk.Label(header, text="模型下載工作台", style="Title.TLabel").pack(anchor="w", pady=(3, 5))
    ttk.Label(header, text="各模型獨立推進，共用請求間隔與冷卻。", style="Muted.TLabel").pack(anchor="w")

    columns = ttk.Frame(content)
    columns.pack(fill="x")
    columns.columnconfigure(0, weight=1, uniform="main")
    columns.columnconfigure(1, weight=2, uniform="main")
    left = ttk.Frame(columns)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
    right = ttk.Frame(columns)
    right.grid(row=0, column=1, sticky="nsew")

    panel, body = card(left)
    panel.pack(fill="both", expand=True)
    ttk.Label(body, text="下載設定", style="CardHeading.TLabel").pack(anchor="w", pady=(0, 16))
    ttk.Label(body, text="01   Ticker 清單", style="Card.TLabel").pack(anchor="w")
    app.file_label = path_label(body, "尚未選擇 .txt 清單")
    app.load_button = ttk.Button(body, text="選擇清單…", command=app.load_ticker_list)
    app.load_button.pack(fill="x")
    ttk.Label(body, text="02   選擇模型", style="Card.TLabel").pack(anchor="w", pady=(20, 4))
    choices = ttk.Frame(body, style="Card.TFrame")
    choices.pack(fill="x")
    app.models = list(MODELS)
    app.selected_models, app.model_checks = {}, []
    for i, model in enumerate(MODELS):
        var = tk.BooleanVar(value=model in app.user_settings.get("last_selected_models", []))
        cb = ttk.Checkbutton(choices, text=model, variable=var, command=app.validate_inputs)
        cb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 12))
        app.selected_models[model] = var
        app.model_checks.append(cb)
    ttk.Label(body, text="03   儲存位置", style="Card.TLabel").pack(anchor="w", pady=(18, 0))
    app.dest_label = path_label(body, "尚未選擇資料夾")
    app.dest_button = ttk.Button(body, text="選擇資料夾…", command=app.select_destination_path)
    app.dest_button.pack(fill="x")
    app.open_dest_button = ttk.Button(body, text="開啟儲存資料夾", command=app.open_destination_folder)
    app.open_dest_button.pack(fill="x", pady=(8, 0))
    app.input_hint = ttk.Label(body, text="", style="CardMuted.TLabel", wraplength=240)
    app.input_hint.pack(fill="x", pady=(18, 10))
    app.start_button = ttk.Button(body, text="開始下載", style="Primary.TButton", command=app.start_automation_thread)
    app.start_button.pack(fill="x")
    app.retry_button = ttk.Button(body, text="重試失敗項目", command=app._retry_failed)
    app.retry_button.pack(fill="x", pady=(8, 0))
    app.resume_button = ttk.Button(body, text="續跑既有批次…", command=app._resume_batch)
    app.resume_button.pack(fill="x", pady=(8, 0))
    app.stop_button = ttk.Button(body, text="停止並保存進度", style="Stop.TButton", command=app.stop_batch, state="disabled")
    app.stop_button.pack(fill="x", pady=(8, 0))

    summary, body = card(right)
    summary.pack(fill="x", pady=(0, 14))
    ttk.Label(body, text="批次總覽", style="CardHeading.TLabel").pack(anchor="w")
    app.overall_label = ttk.Label(body, text="準備開始", style="Metric.TLabel")
    app.overall_label.pack(fill="x", pady=(8, 2))
    app.overall_label.bind("<Configure>", lambda e: e.widget.configure(wraplength=max(80, e.width)))
    app.mode_label = ttk.Label(body, text="等待上限 1 · 提交間隔至少 5 秒", style="CardMuted.TLabel")
    app.mode_label.pack(anchor="w")
    app.cooldown_label = ttk.Label(body, text="共用冷卻：0 秒", style="Badge.TLabel")
    app.cooldown_label.pack(fill="x", pady=(14, 0))
    cards = ttk.Frame(right)
    cards.pack(fill="both", expand=True)
    cards.columnconfigure((0, 1), weight=1, uniform="cards")
    app.model_status, app.model_progress, app.model_counts = {}, {}, {}
    for i, model in enumerate(MODELS):
        panel, body = card(cards, 14)
        panel.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 == 0 else 7, 7 if i % 2 == 0 else 0), pady=(0, 14))
        ttk.Label(body, text=model, style="CardHeading.TLabel").pack(anchor="w")
        label = ttk.Label(body, text="未開始", style="CardMuted.TLabel", wraplength=200)
        label.pack(fill="x", pady=(8, 10))
        label.bind("<Configure>", lambda e: e.widget.configure(wraplength=max(80, e.width)))
        progress = ttk.Progressbar(body, maximum=1, value=0)
        progress.pack(fill="x", pady=(0, 8))
        count = ttk.Label(body, text="完成 0 / 0 · 待補抓 0", style="CardMuted.TLabel")
        count.pack(anchor="w")
        button = ttk.Button(body, text="登入後繼續", state="disabled",
                            command=lambda m=model: app.runner.continue_model(m) if app.runner else None)
        button.pack(fill="x", pady=(12, 0))
        app.model_status[model] = (label, button)
        app.model_progress[model] = progress
        app.model_counts[model] = count

    panel, body = card(content, 16)
    panel.pack(fill="x", pady=(20, 0))
    ttk.Label(body, text="最後總結", style="CardHeading.TLabel").pack(anchor="w")
    app.summary_text = tk.Text(body, height=7, wrap="word", state="disabled", font=(FONT, 10),
                               background=COLORS['surface'], foreground=COLORS['text'], relief='flat')
    summary_scroll = ttk.Scrollbar(body, command=app.summary_text.yview)
    summary_scroll.pack(side='right', fill='y')
    app.summary_text.configure(yscrollcommand=summary_scroll.set)
    app.summary_text.pack(fill='x', pady=(8, 0))

    panel, body = card(content, 16)
    panel.pack(fill="both", expand=True, pady=(20, 0))
    ttk.Label(body, text="執行紀錄", style="CardHeading.TLabel").pack(anchor="w", pady=(0, 10))
    log_frame = ttk.Frame(body, style="Card.TFrame")
    log_frame.pack(fill="both", expand=True)
    scrollbar = ttk.Scrollbar(log_frame)
    scrollbar.pack(side="right", fill="y")
    app.log_text = tk.Text(log_frame, height=7, state="disabled", wrap="word", borderwidth=0,
                           background=COLORS["field"], foreground=COLORS["text"],
                           font=(FONT, 10), padx=12, pady=10, highlightthickness=1,
                           highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"],
                           yscrollcommand=scrollbar.set)
    app.log_text.pack(fill="both", expand=True)
    scrollbar.configure(command=app.log_text.yview)
    ttk.Label(content, text="進度會自動保存，可從「續跑既有批次」接續未完成項目。", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
    root.after(250, app._poll_batch)
