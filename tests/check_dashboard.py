"""Opt-in visual check of our own Tk window; no browser or live downloads."""
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
from lieta_automator.gui import TickerApp


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder, \
            patch('lieta_automator.gui.settings.load_settings', return_value={'enable_multi_window': True,
                'last_selected_models': ['Gamma', 'Term', 'Smile', 'TV Code']}), \
            patch('lieta_automator.config.TEMP_DOWNLOAD_DIR_NAME', folder), \
            patch.object(TickerApp, '_setup_logging'):
        root = tk.Tk()
        try:
            root.title('UI validation')
            app = TickerApp(root)
            app.tickers = ['DEMO'] * 37
            app.destination_path = folder
            app.file_label.config(text='示範清單.txt · 37 個 ticker')
            app.dest_label.config(text='D:\\模型資料\\每日下載')
            app.input_hint.config(text='介面預覽 · 非實際下載狀態')
            states = {
                'Gamma': {'state': '完成', 'ticker': '', 'success': 37, 'pending': 0, 'deferred': 0},
                'Term': {'state': '等待結果', 'ticker': 'DEMO', 'success': 24, 'pending': 13, 'deferred': 1},
                'Smile': {'state': '等待提交', 'ticker': 'DEMO', 'success': 25, 'pending': 12, 'deferred': 0},
                'TV Code': {'state': '等待登入', 'ticker': 'DEMO', 'success': 22, 'pending': 15, 'deferred': 0},
            }
            app.runner = SimpleNamespace(done=threading.Event(), dispatch=SimpleNamespace(stop=threading.Event()),
                multi=True, snapshot=lambda: (states, {'cooldown': 20, 'active': 1, 'limit': 1}))
            app.automation_running = True
            app.toggle_ui_state(False)
            app.log_text.config(state='normal')
            app.log_text.insert('end', '介面預覽：此畫面使用模擬資料，沒有向網站發送請求。\n')
            app.log_text.config(state='disabled')
            app._poll_batch()
            app._set_summary('介面預覽：成功 147/148，未完成 1 項\nSmile / LITE：Try Again：本輪兩次一般提交均失敗。')
            root.update()
            hwnd = int(root.wm_frame(), 16)
            ImageGrab.grab(window=hwnd).save(output / 'dashboard.png')
            root.geometry('900x620')
            root.update()
            # Scroll-to-focus must expose bottom controls even on small windows.
            app.log_text.focus_force()
            root.update()
            ImageGrab.grab(window=hwnd).save(output / 'dashboard-small.png')
            print('Captured dashboard and compact layout; no live requests.')
        finally:
            root.destroy()


if __name__ == '__main__':
    main()
