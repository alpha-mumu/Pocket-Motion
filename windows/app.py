# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""Pocket Motion 4 —— Osmo Pocket 4 实况照片导出工具（Windows 单文件版）启动器。

全部依赖（视频引擎、图标、说明、上游源码补丁）都封装在 exe 内部，
运行目录不需要、也不会产生第二个文件。
双击 exe 后弹出程序自带窗口（不启动浏览器），界面服务只监听 127.0.0.1。
隐藏参数：
  --selftest   用打包后的运行时 + 内置 ffmpeg 跑完整自检，结果写入临时目录
               的 PocketMotion4_selftest_result.txt
  --no-browser 只启动本地服务，不开窗口也不开浏览器（排障用）
  --browser    强制改用默认浏览器打开界面（兼容旧用法）
  --port=8899  指定本地服务端口（默认 8787，被占用时自动回退随机端口）
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
SELFTEST_REPORT = os.path.join(tempfile.gettempdir(), 'PocketMotion4_selftest_result.txt')


def main():
    if '--selftest' in sys.argv:
        try:
            from selftest import run_packaged_selftest
            ok, report = run_packaged_selftest(BASE_DIR)
        except Exception as exc:  # noqa: BLE001
            report = 'RESULT: FAIL\n%s\n%s' % (exc, traceback.format_exc())
        try:
            with open(SELFTEST_REPORT, 'w', encoding='utf-8') as f:
                f.write(report)
        except OSError:
            pass
        return 0

    port = 8787
    for arg in sys.argv[1:]:
        if arg.startswith('--port='):
            try:
                port = int(arg.split('=', 1)[1])
            except ValueError:
                pass
    from webui import serve
    if '--no-browser' in sys.argv:
        serve(open_browser=False, port=port)
    elif '--browser' in sys.argv:
        serve(open_browser=True, port=port)
    else:
        from gui import run_gui
        from webui import STATE
        return run_gui(STATE, BASE_DIR, port)
    return 0


if __name__ == '__main__':
    sys.exit(main())
