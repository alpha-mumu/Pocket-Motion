# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""打包后端到端验证（单文件版 v4.2.1）。

用法：python verify_packaged.py "path/to/Pocket Motion.exe"

覆盖：干净环境启动、内置资源释放、界面 API、真实扫描/导出/动态校验、
界面一键自检、说明与补丁导出、独立窗口、运行目录清洁、中文空格路径。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FAILS = []
NOTES = []


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def clean_env():
    env = dict(os.environ)
    for k in list(env):
        if k.upper().startswith('PYTHON'):
            env.pop(k, None)
    env['PYTHONUTF8'] = '1'
    return env


def http(port, path, body=None, timeout=10):
    url = 'http://127.0.0.1:%d%s' % (port, path)
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
    with OPENER.open(req, timeout=timeout) as r:
        raw = r.read().decode('utf-8')
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def wait_service(port, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            d = http(port, '/api/status', timeout=3)
            if isinstance(d, dict) and 'busy' in d:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def wait_idle(port, phase=None, timeout=300):
    """等待后台任务结束，并累积返回的日志行。"""
    seen = []
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = http(port, '/api/status', timeout=5)
            for line in s.get('logs', []):
                if line not in seen:
                    seen.append(line)
            if not s.get('busy') and (phase is None or s.get('phase') == 'idle'):
                return seen
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
    return seen


def wait_token(port, tokens, timeout=180):
    """等到日志里出现任一关键词，返回累积到的日志（避免线程异步造成误判）。"""
    if isinstance(tokens, str):
        tokens = [tokens]
    seen = []
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = http(port, '/api/status', timeout=5)
            for line in s.get('logs', []):
                if line not in seen:
                    seen.append(line)
            if any(t in line for t in tokens for line in seen):
                return seen
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return seen


def wait_new_token(port, token, c0, timeout=300):
    """只按本次任务新增的日志行判断结束（历史日志不参与匹配）。"""
    end = time.time() + timeout
    busy_seen = False
    while time.time() < end:
        try:
            s = http(port, '/api/status', timeout=5)
            n = max(0, s.get('log_count', 0) - c0)
            new = s.get('logs', [])[-n:] if n else []
            if s.get('busy'):
                busy_seen = True
            if any(token in x for x in new):
                return new, s
            if busy_seen and not s.get('busy'):
                return new, s
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return [], {}


def snapshot(port):
    try:
        return set(http(port, '/api/status').get('logs', []))
    except Exception:  # noqa: BLE001
        return set()


def check(cond, label, detail=''):
    if cond:
        print('  [OK] ' + label + ((' · ' + detail) if detail else ''))
    else:
        FAILS.append(label + ((' · ' + detail) if detail else ''))
        print('  [FAIL] ' + label + ((' · ' + detail) if detail else ''))


def window_titles():
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append(buf.value)
        return True

    ctypes.windll.user32.EnumWindows(CB(cb), 0)
    return out


def make_samples(ffmpeg, root):
    d = os.path.join(root, 'DCIM', 'DJI_001')
    os.makedirs(d, exist_ok=True)
    mp4 = os.path.join(d, 'DJI_20261106103000_0001_D.MP4')
    jpg = os.path.join(d, 'DJI_20261106103000_0001_D.JPG')
    base = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
            '-i', 'testsrc2=size=640x360:rate=30:duration=3']
    subprocess.run(base + ['-pix_fmt', 'yuv420p', '-c:v', 'libx265', '-tag:v', 'hvc1',
                           '-preset', 'ultrafast', '-crf', '28', '-movflags', '+faststart', mp4],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                    '-i', 'testsrc2=size=640x360:rate=30:duration=0.2',
                    '-frames:v', '1', '-q:v', '2', '-update', '1', jpg],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    return d, mp4


def launch(exe, args, env):
    return subprocess.Popen([exe] + args, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    exe = os.path.abspath(sys.argv[1])
    assert os.path.isfile(exe), '找不到 exe: ' + exe
    exe_dir = os.path.dirname(exe)
    before = sorted(os.listdir(exe_dir))
    print('单文件验证：%s (%.1f MB)' % (exe, os.path.getsize(exe) / 1048576))
    check(before == ['Pocket Motion.exe'], 'exe 所在目录只有主程序',
          str(before))

    env = clean_env()
    work = tempfile.mkdtemp(prefix='pm4_verify_')
    out_dir = os.path.join(work, 'out')

    # ---------- 无窗口模式：API / 扫描 / 导出 / 自检 ----------
    port = free_port()
    print('\n[1] 干净环境启动本地服务 (端口 %d)' % port)
    p = launch(exe, ['--no-browser', '--port=%d' % port], env)
    try:
        check(wait_service(port), '服务启动成功')
        if not wait_service(port):
            return 1
        docs = http(port, '/api/docs')
        check(docs.get('version') == '4.2.1', '内置说明版本', str(docs.get('version')))
        check(docs.get('embedded') is True, 'ffmpeg 为内嵌资源')
        check('单文件版' in docs.get('text', ''), '说明文本可读自 exe')
        ff = docs.get('ffmpeg', '')
        check(os.path.isfile(ff) and os.path.getsize(ff) > 1024 * 1024,
              '内置 ffmpeg 已释放到本机缓存', ff)

        logs = wait_token(port, ['内置 ffmpeg 已就绪', '预热未完成'], timeout=120)
        check(any('单文件版 v4.2.1' in x for x in logs), '启动日志标识单文件版')
        warm = next((x for x in logs if '内置 ffmpeg' in x), '')
        check('内置 ffmpeg 已就绪' in warm, '后台预热完成', warm)

        html = http(port, '/')
        for token in ('showDocs()', 'doSelftest()', 'exportBundle()',
                      'v4.2.1 单文件版'):
            check(token in html, '界面控件/标识：' + token)

        print('\n[2] 真实扫描 → Apple / Google 导出 → 动态视频校验')
        sample_root, _ = make_samples(ff, work)
        http(port, '/api/config', {'src': sample_root, 'out': out_dir,
                                   'min_dur': 0.3, 'max_dur': 5.0,
                                   'duration_rule': True, 'recursive': True,
                                   'device_override': ''})
        http(port, '/api/scan', {})
        wait_idle(port, timeout=120)
        assets = http(port, '/api/assets')
        lives = [a for a in assets if a['kind'] == 'live']
        check(len(lives) == 1, '3 秒 MP4 被识别为实况', '%d 项' % len(lives))
        if not lives:
            return 1
        path = lives[0]['path']
        for fmt in ('apple', 'google'):
            # 按日志行号取增量：Apple 与 Google 的校验文本完全相同，不能按文本去重。
            c0 = http(port, '/api/status').get('log_count', 0)
            http(port, '/api/export', {'paths': [path], 'fmt': fmt,
                                       'h264_compat': False})
            new, _s = wait_new_token(port, '导出结束：', c0, timeout=300)
            ok = any('动态视频校验通过' in x for x in new)
            bad = [x for x in new if ('动态视频校验异常' in x or '校验异常' in x
                                      or '导出结束：成功 0' in x)]
            check(ok and not bad, '%s 导出含可播放动态视频' % fmt,
                  (next((x.strip() for x in new if '动态视频校验' in x), '')
                   + ((' | 异常：' + str(bad[:1])) if bad else '')))

        http(port, '/api/verify', {})
        lg = wait_token(port, '验证结束：动态视频正常', timeout=300)
        wait_idle(port, timeout=60)
        check(any('验证结束：动态视频正常' in x for x in lg), '验证导出结果接口',
              next((x for x in lg if '验证结束' in x), ''))

        print('\n[3] 界面一键自检 / 说明与补丁导出')
        wait_idle(port, timeout=120)
        time.sleep(1.0)
        rr = http(port, '/api/selftest', {})
        check(rr.get('ok') is not False, '一键自检接口接受请求', str(rr))
        lg = wait_token(port, '一键自检结束', timeout=420)
        diag = [x for x in lg if any(k in x for k in (
            'FAIL', '失败', 'Traceback', 'Error', 'RESULT', '自检', 'ffmpeg', '异常'))]
        for line in diag[-45:]:
            print('    | ' + line)
        wait_idle(port, timeout=60)
        check(any('RESULT: PASS' in x for x in lg), '一键自检 RESULT: PASS')
        check(any('全部通过' in x for x in lg), '自检结果写入界面日志')
        b = http(port, '/api/export_bundle', {'dir': out_dir})
        check(b.get('ok') and b.get('count') == 8, '导出 8 个内置资源文件',
              str(b.get('count')))
        check(os.path.isfile(os.path.join(b.get('path', ''), '使用说明.txt')),
              '使用说明.txt 可按需导出')
        check(os.path.isfile(os.path.join(b.get('path', ''), '上游源码补丁',
                                          'lib', 'src', 'media_scanner.dart')),
              'Dart 补丁可按需导出')

        http(port, '/api/shutdown', {})
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
        check(p.returncode is not None, '退出程序后进程结束')
    finally:
        if p.poll() is None:
            p.kill()

    # ---------- 独立窗口 ----------
    print('\n[4] 独立窗口（不调用浏览器）')
    port2 = free_port()
    p2 = launch(exe, ['--port=%d' % port2], env)
    try:
        ok = wait_service(port2, timeout=120)
        check(ok, '窗口模式服务启动')
        if ok:
            logs2 = wait_token(port2, ['独立窗口已就绪', '窗口界面仍在加载'], timeout=120)
            check(any('独立窗口已就绪' in x for x in logs2),
                  'DOM 加载完成', next((x for x in logs2 if '独立窗口已就绪' in x), ''))
            time.sleep(1.5)
            titles = [t for t in window_titles() if 'Pocket Motion' in t]
            check(bool(titles), '原生窗口标题出现', str(titles[:2]))
            http(port2, '/api/shutdown', {})
            try:
                p2.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p2.kill()
            check(p2.returncode is not None, '窗口关闭后进程结束')
    finally:
        if p2.poll() is None:
            p2.kill()

    after = sorted(os.listdir(exe_dir))
    check(after == before, '运行后 exe 所在目录仍未产生第二个文件', str(after))

    # ---------- 中文空格路径 ----------
    print('\n[5] 中文空格路径解压运行')
    cn_dir = os.path.join(work, '中文 空格 目录')
    os.makedirs(cn_dir, exist_ok=True)
    cn_exe = os.path.join(cn_dir, 'Pocket Motion.exe')
    shutil.copy2(exe, cn_exe)
    port3 = free_port()
    p3 = launch(cn_exe, ['--no-browser', '--port=%d' % port3], env)
    try:
        ok = wait_service(port3, timeout=120)
        check(ok, '中文空格路径下服务启动')
        if ok:
            d = http(port3, '/api/docs')
            check(d.get('embedded') is True, '中文路径下内嵌资源可读')
            http(port3, '/api/shutdown', {})
            try:
                p3.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p3.kill()
    finally:
        if p3.poll() is None:
            p3.kill()

    # ---------- --selftest 隐藏参数 ----------
    print('\n[6] 命令行 --selftest（干净环境）')
    report = os.path.join(tempfile.gettempdir(), 'PocketMotion4_selftest_result.txt')
    if os.path.isfile(report):
        os.remove(report)
    r = subprocess.run([exe, '--selftest'], env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=600)
    txt = open(report, 'r', encoding='utf-8').read() if os.path.isfile(report) else ''
    check(r.returncode == 0 and 'RESULT: PASS' in txt, '--selftest 完整链路 PASS',
          ('%.1f KB' % (len(txt) / 1024)) if txt else '无报告')
    check('动态视频校验通过' in txt, '报告含动态视频解码校验')

    print('\n' + '=' * 64)
    if FAILS:
        print('RESULT: FAIL  (%d 项)' % len(FAILS))
        for f in FAILS:
            print(' - ' + f)
        return 1
    print('RESULT: PASS  — 单文件版全部验证通过')
    print('=' * 64)
    return 0


if __name__ == '__main__':
    sys.exit(main())
