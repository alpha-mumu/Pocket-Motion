# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""自带独立窗口：把本地界面装进一个原生应用窗口，不启动浏览器。

界面内容仍由 webui.py 的本地 http.server 提供（只监听 127.0.0.1），
因此前端 HTML/CSS/JS、全部 API 与导出引擎都保持原样。
窗口由 Windows 自带的 Edge WebView2 渲染，没有地址栏、标签页或浏览器进程。
"""
from __future__ import annotations

import os
import threading
import time
import webbrowser

APP_TITLE = 'Pocket Motion 4 · Osmo Pocket 4 实况照片导出'


def _message(text, title=APP_TITLE, flags=0x40):
    """纯标准库弹一个系统消息框（windowed 模式没有控制台）。"""
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:  # noqa: BLE001
        return 0


def has_webview2():
    """Win10/11 通常随 Edge 自带 WebView2 Runtime；缺失时回退浏览器。"""
    try:
        import winreg
        k64 = r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        k32 = r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        for hive, sub in (
            (winreg.HKEY_LOCAL_MACHINE, k64),
            (winreg.HKEY_LOCAL_MACHINE, k32),
            (winreg.HKEY_CURRENT_USER, k32),
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    ver, _ = winreg.QueryValueEx(k, 'pv')
                    if ver and ver != '0.0.0.0':
                        return True
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    for base in (r'C:\Program Files (x86)\Microsoft\EdgeWebView\Application',
                 r'C:\Program Files\Microsoft\EdgeWebView\Application'):
        try:
            for d in os.listdir(base):
                if d[:1].isdigit() and os.path.isfile(os.path.join(base, d, 'msedgewebview2.exe')):
                    return True
        except OSError:
            pass
    return False


def _fallback_browser(url, reason=''):
    msg = ('未能启动独立窗口，程序已改用默认浏览器显示界面。\n\n'
           '地址：%s\n\n'
           '建议安装 / 修复「Microsoft Edge WebView2 Runtime」后重新启动。'
           % url)
    if reason:
        msg += '\n\n详细信息：%s' % reason
    _message(msg, APP_TITLE, 0x30)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    # 服务已在后台线程，保持进程存活，用户用完点页面「退出程序」即可。
    threading.Event().wait()


def run_gui(state, base_dir, port=8787):
    """启动本地服务 + 独立窗口；窗口关闭后停止服务并退出。"""
    from webui import start_server_in_thread

    url = start_server_in_thread(port)
    if not has_webview2():
        _fallback_browser(url, '未检测到 WebView2 Runtime')
        return 0

    try:
        import webview
        win = webview.create_window(
            APP_TITLE,
            url,
            width=1200, height=820,
            min_size=(980, 640),
            background_color='#F5F7FA',
            text_select=True,
        )
        if win is None:
            raise RuntimeError('create_window 返回空窗口')
    except Exception as exc:  # noqa: BLE001
        _fallback_browser(url, str(exc))
        return 0

    # 页面里点「退出程序」时，除了停服务还要把窗口一起关掉。
    state.shutdown_callback = win.destroy

    def on_closing():
        # 返回 False = 取消关闭（pywebview Event 语义）
        if getattr(state, 'busy', False):
            r = _message('正在扫描或导出，确定要退出吗？\n未完成的任务会被中断。',
                         APP_TITLE, 0x24)  # MB_YESNO | MB_ICONQUESTION
            return r == 6  # IDYES -> 允许关闭
        return True

    win.events.closing += on_closing

    def after_start():
        # 窗口创建后在独立线程确认页面 DOM 已加载，并在界面日志区给一条反馈。
        for _ in range(40):
            time.sleep(0.3)
            try:
                title = win.evaluate_js('document.title')
                buttons = win.evaluate_js('document.querySelectorAll("button").length')
                if title:
                    state.log('独立窗口已就绪：%s · %s 个界面控件' % (title, buttons))
                    return
            except Exception:  # noqa: BLE001
                pass
        state.log('提示：窗口界面仍在加载')

    # 单文件版：图标也内嵌在 exe 里，不再依赖同目录的 app.ico。
    try:
        from resources import icon_path
        icon = icon_path()
    except Exception:  # noqa: BLE001
        icon = None
    try:
        webview.start(after_start, gui='edgechromium', private_mode=False,
                      icon=icon)
    except Exception as exc:  # noqa: BLE001
        _fallback_browser(url, str(exc))
    finally:
        try:
            if state.server:
                state.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        state.shutdown_callback = None
    return 0
