# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""本地界面后端（纯标准库 http.server）。

程序启动后只监听 127.0.0.1，界面由自带独立窗口或浏览器显示；
所有文件操作都留在本机，不联网、不上传。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import string
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import resources
from core import exporters, mp4, scanner

APP_VERSION = '4.2.1'
BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'PocketMotion4')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

DEFAULT_CFG = {
    'src': '',
    'out': os.path.join(os.path.expanduser('~'), 'Downloads', 'PocketMotion_Out'),
    'min_dur': 0.30,
    'max_dur': 5.00,
    'duration_rule': True,
    'recursive': True,
    'device_override': '',
    'h264_compat': False,
}


def load_config():
    cfg = dict(DEFAULT_CFG)
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
    except Exception:  # noqa: BLE001
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


class State:
    def __init__(self):
        self.cfg = load_config()
        self.assets = []
        self.logs = []
        self.busy = False
        self.phase = 'idle'
        self.done = 0
        self.total = 0
        self.assets_version = 0
        self.lock = threading.Lock()
        self.server = None
        self.result = None
        self.result_id = 0
        # 独立窗口模式下由 GUI 层注册：点「退出程序」时一并关闭窗口。
        self.shutdown_callback = None

    def log(self, s):
        with self.lock:
            self.logs.append(s)
            if len(self.logs) > 800:
                del self.logs[:300]

    def asset_dict(self, a):
        return {
            'path': a.path,
            'filename': a.filename,
            'kind': a.effective_kind,
            'duration': round(a.duration or 0, 3),
            'shot_at': a.shot_at.strftime('%Y-%m-%d %H:%M:%S') if a.shot_at else '',
            'model': a.model or '',
            'make': a.make or '',
            'model_source': a.model_source or '',
            'time_source': a.time_source or '',
            'size_bytes': a.size_bytes,
            'reason': ('[手动指定] ' if a.override_kind else '') + (a.reason or ''),
            'paired_jpg': bool(a.paired_jpg),
            'width': a.width,
            'height': a.height,
        }

    def scan_cfg(self):
        return {
            'min_live_duration': float(self.cfg.get('min_dur', 0.30) or 0.30),
            'max_live_duration': float(self.cfg.get('max_dur', 5.00) or 5.00),
            'duration_rule': bool(self.cfg.get('duration_rule', True)),
            'recursive': bool(self.cfg.get('recursive', True)),
            'default_model': self.cfg.get('device_override') or 'DJI Osmo Pocket 4',
        }

    def do_scan(self):
        src = (self.cfg.get('src') or '').strip()
        if not src or not os.path.isdir(src):
            self.log('扫描失败：素材源目录无效')
            return
        self.busy, self.phase, self.done, self.total = True, 'scan', 0, 0
        self.log('开始扫描：' + src)
        try:
            def prog(d, t):
                self.done, self.total = d, t
            assets = scanner.scan(src, self.scan_cfg(), progress=prog)
            with self.lock:
                self.assets = assets
                self.assets_version += 1
            counts = {k: sum(1 for a in assets if a.effective_kind == k)
                      for k in ('live', 'video', 'photo')}
            self.log('扫描完成：共 %d 个文件，实况 %d / 视频 %d / 照片 %d'
                     % (len(assets), counts['live'], counts['video'], counts['photo']))
            for a in assets:
                if a.kind == 'video' and a.duration and a.duration <= 8:
                    self.log('  [普通视频] %s  时长 %.2fs  %s' % (a.filename, a.duration, a.reason))
        except Exception:  # noqa: BLE001
            self.log('扫描失败：\n' + traceback.format_exc())
        finally:
            self.busy, self.phase = False, 'idle'

    def do_export(self, paths, fmt, h264_compat=False):
        if self.busy:
            return
        by_path = {a.path: a for a in self.assets}
        picked = [by_path[p] for p in paths if p in by_path]
        if not picked:
            self.log('导出失败：未选中任何素材')
            return
        out_root = (self.cfg.get('out') or '').strip()
        if not out_root:
            self.log('导出失败：未设置导出位置')
            return
        override = (self.cfg.get('device_override') or '').strip()
        if override:
            for a in picked:
                a.model = override
                a.model_source = '手动指定'
        self.busy, self.phase, self.done, self.total = True, 'export', 0, len(picked)
        self.log('开始导出：%d 项，格式 %s' % (len(picked), fmt))
        exp = exporters.Exporter(log=lambda s: self.log(s), base_dir=BASE_DIR)
        ok = fail = 0
        try:
            for i, a in enumerate(picked, start=1):
                try:
                    if a.effective_kind == 'photo':
                        exp.copy_media(a, os.path.join(out_root, 'photo'), 'photo')
                    elif a.effective_kind == 'video':
                        exp.copy_media(a, os.path.join(out_root, 'movie'), 'movie')
                    elif fmt == 'google':
                        exp.export_motion(a, os.path.join(out_root, 'live'),
                                          h264_compat=bool(h264_compat))
                    else:
                        exp.export_apple(a, os.path.join(out_root, 'live'))
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    self.log('失败 %s：%s' % (a.filename, e))
                self.done = i
            self.log('导出结束：成功 %d，失败 %d' % (ok, fail))
        finally:
            self.busy, self.phase = False, 'idle'
            self.result = (ok, fail)
            self.result_id += 1

    def diagnose(self, path):
        a = next((x for x in self.assets if x.path == path), None)
        if a is None:
            return '未找到该素材。'
        info = a.mp4_info or {}
        return '\n'.join([
            '文件：%s' % a.path,
            '判定：%s' % a.kind_label,
            '依据：%s' % a.reason,
            '容器时长：%.3f 秒' % (a.duration or 0),
            '编码 / 尺寸：%s  %dx%d' % (info.get('codec', '-'), a.width, a.height),
            '拍摄时间：%s（来源：%s）' % (a.shot_at, a.time_source),
            '设备型号：%s %s（来源：%s）' % (a.make, a.model, a.model_source),
            '配套 JPG：%s' % ('有' if a.paired_jpg else '无'),
            'QuickTime 元数据：%s' % (info.get('keys') or '{}'),
            '模板串 / 标记：%s' % (', '.join(info.get('markers', [])[:20]) or '无'),
            '解析错误：%s' % (info.get('error') or '无'),
        ])


    def do_verify(self):
        """逐个解码校验已导出文件里的动态视频，给出可核对的结论。"""
        live_dir = os.path.join((self.cfg.get('out') or '').strip(), 'live')
        if not os.path.isdir(live_dir):
            self.log('验证失败：找不到导出目录 %s' % live_dir)
            return
        files = sorted(f for f in os.listdir(live_dir)
                       if os.path.splitext(f)[1].lower() in
                       ('.mov', '.jpg', '.jpeg', '.heic'))
        if not files:
            self.log('验证：导出目录里还没有可验证的文件')
            return
        self.busy, self.phase = True, 'verify'
        exp = exporters.Exporter(log=lambda s: self.log(s), base_dir=BASE_DIR)
        try:
            self.log('开始验证导出结果：%s（共 %d 个文件）' % (live_dir, len(files)))
            good_n = bad_n = 0
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext == '.heic':
                    mov = os.path.splitext(f)[0] + '.MOV'
                    if os.path.isfile(os.path.join(live_dir, mov)):
                        self.log('  [配对] %s → %s 存在（须两个文件一起导入 iPhone 相册）'
                                 % (f, mov))
                    else:
                        bad_n += 1
                        self.log('  [异常] %s → 缺少同名 .MOV，导入后会显示为静态图' % f)
                    continue
                good, desc = exp.probe_dynamic(os.path.join(live_dir, f))
                self.log('  %s %s → %s' % ('[OK]' if good else '[异常]', f, desc))
                if good:
                    good_n += 1
                else:
                    bad_n += 1
            self.log('验证结束：动态视频正常 %d 项，异常 %d 项。' % (good_n, bad_n))
            self.log('注意：Windows 看图软件只显示静态封面；动态效果要在 iPhone 相册'
                     '（HEIC + MOV 成对导入）或 Google 相册（单个 JPG）里查看。')
        except Exception:  # noqa: BLE001
            self.log('验证出错：\n' + traceback.format_exc())
        finally:
            self.busy, self.phase = False, 'idle'

    def do_selftest(self):
        """界面版「一键自检」。"""
        self.busy, self.phase = True, 'selftest'
        try:
            self.log('开始一键自检：生成模拟素材 → 识别 → 导出 → 实际解码校验（约 10~30 秒）')
            from selftest import run_packaged_selftest
            ok, report = run_packaged_selftest(BASE_DIR)
            for line in report.splitlines():
                self.log(line)
            self.log('一键自检结束：%s' % ('全部通过（RESULT: PASS）' if ok
                                          else '未通过（RESULT: FAIL），请把上方日志发回'))
        except Exception:  # noqa: BLE001
            self.log('一键自检失败：\n' + traceback.format_exc())
        finally:
            self.busy, self.phase = False, 'idle'


STATE = State()


def browse(path):
    """列出目录，返回可供前端导航的结构。"""
    result = {'current': '', 'parent': '', 'dirs': [], 'drives': [], 'files': 0}
    if not path:
        drives = []
        for letter in string.ascii_uppercase:
            d = '%s:\\' % letter
            if os.path.isdir(d):
                drives.append(d)
        result['drives'] = drives
        home = os.path.expanduser('~')
        result['dirs'] = [{'name': n, 'path': p} for n, p in (
            ('用户目录', home),
            ('桌面', os.path.join(home, 'Desktop')),
            ('下载', os.path.join(home, 'Downloads')),
            ('图片', os.path.join(home, 'Pictures')),
        ) if os.path.isdir(p)]
        return result
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return result
    result['current'] = path
    parent = os.path.dirname(path)
    result['parent'] = parent if parent != path else ''
    dirs = []
    try:
        for e in os.scandir(path):
            if e.is_dir() and not e.name.startswith('.'):
                dirs.append({'name': e.name, 'path': e.path})
            elif e.is_file():
                result['files'] += 1
    except OSError:
        pass
    dirs.sort(key=lambda x: x['name'].lower())
    result['dirs'] = dirs[:300]
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200, ctype='application/json; charset=utf-8'):
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:  # noqa: BLE001
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ('/', '/index.html'):
            return self._send(INDEX_HTML.encode('utf-8'), ctype='text/html; charset=utf-8')
        if u.path == '/api/config':
            return self._send(STATE.cfg)
        if u.path == '/api/status':
            with STATE.lock:
                logs = list(STATE.logs)
            return self._send({
                'busy': STATE.busy, 'phase': STATE.phase,
                'done': STATE.done, 'total': STATE.total,
                'log_count': len(logs), 'logs': logs[-120:],
                'assets_version': STATE.assets_version,
                'result': STATE.result,
                'result_id': STATE.result_id,
            })
        if u.path == '/api/assets':
            return self._send([STATE.asset_dict(a) for a in STATE.assets])
        if u.path == '/api/browse':
            return self._send(browse((q.get('path') or [''])[0]))
        if u.path == '/api/docs':
            try:
                text = resources.read_text('docs/readme.txt')
            except Exception:  # noqa: BLE001
                text = '（内置说明不可用，请重新下载完整的 exe）'
            return self._send({'text': text, 'version': APP_VERSION,
                               'embedded': resources.has_embedded_ffmpeg(),
                               'ffmpeg': exporters.find_ffmpeg(BASE_DIR)})
        return self._send({'error': 'not found'}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        b = self._body()
        try:
            if u.path == '/api/config':
                for k in DEFAULT_CFG:
                    if k in b:
                        STATE.cfg[k] = b[k]
                for k in ('min_dur', 'max_dur'):
                    try:
                        STATE.cfg[k] = float(STATE.cfg[k])
                    except (TypeError, ValueError):
                        STATE.cfg[k] = DEFAULT_CFG[k]
                save_config(STATE.cfg)
                return self._send({'ok': True})
            if u.path == '/api/scan':
                if STATE.busy:
                    return self._send({'ok': False, 'msg': '正在处理，请稍候'})
                threading.Thread(target=STATE.do_scan, daemon=True).start()
                return self._send({'ok': True})
            if u.path == '/api/export':
                if STATE.busy:
                    return self._send({'ok': False, 'msg': '正在处理，请稍候'})
                STATE.result = None
                threading.Thread(target=STATE.do_export,
                                 args=(b.get('paths') or [], b.get('fmt') or 'apple',
                                       bool(b.get('h264_compat'))),
                                 daemon=True).start()
                return self._send({'ok': True})
            if u.path == '/api/verify':
                if STATE.busy:
                    return self._send({'ok': False, 'msg': '正在处理，请稍候'})
                threading.Thread(target=STATE.do_verify, daemon=True).start()
                return self._send({'ok': True})
            if u.path == '/api/selftest':
                if STATE.busy:
                    return self._send({'ok': False, 'msg': '正在处理，请稍候'})
                threading.Thread(target=STATE.do_selftest, daemon=True).start()
                return self._send({'ok': True})
            if u.path == '/api/export_bundle':
                dest = (b.get('dir') or STATE.cfg.get('out') or '').strip()
                if not dest:
                    return self._send({'ok': False, 'msg': '请先在上方设置「导出位置」'})
                root, n = resources.export_bundle(dest)
                STATE.log('已导出 %d 个内置资源文件到：%s' % (n, root))
                return self._send({'ok': True, 'path': root, 'count': n})
            if u.path == '/api/override':
                kind = b.get('kind')
                paths = set(b.get('paths') or [])
                n = 0
                for a in STATE.assets:
                    if a.path in paths:
                        a.override_kind = kind
                        n += 1
                STATE.assets_version += 1
                STATE.log('已手动把 %d 个素材设为「%s」' % (n, '实况' if kind == 'live' else '视频'))
                return self._send({'ok': True, 'count': n})
            if u.path == '/api/diagnose':
                return self._send({'text': STATE.diagnose(b.get('path') or '')})
            if u.path == '/api/open_out':
                d = (STATE.cfg.get('out') or '').strip()
                if d:
                    os.makedirs(d, exist_ok=True)
                    try:
                        os.startfile(d)
                    except OSError:
                        webbrowser.open('file:///' + d.replace('\\', '/'))
                return self._send({'ok': True})
            if u.path == '/api/shutdown':
                threading.Thread(target=lambda: STATE.server.shutdown() if STATE.server else None,
                                 daemon=True).start()
                cb = getattr(STATE, 'shutdown_callback', None)
                if cb:
                    threading.Timer(0.35, cb).start()
                return self._send({'ok': True})
        except Exception as e:  # noqa: BLE001
            STATE.log('请求处理失败：' + traceback.format_exc(limit=2))
            return self._send({'ok': False, 'msg': str(e)}, 500)
        return self._send({'error': 'not found'}, 404)


def create_server(port=8787):
    # 优先固定端口，便于手动访问；被占用才回退随机端口。
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    STATE.server = httpd
    return httpd, 'http://127.0.0.1:%d/' % httpd.server_address[1]


def _log_startup(url):
    STATE.log('已启动：' + url)
    try:
        if resources.has_embedded_ffmpeg():
            STATE.log('单文件版 v%s：视频引擎 / 窗口图标 / 使用说明 / 上游源码补丁均已内嵌'
                      % APP_VERSION)

            def _warm():
                # 首次启动把压缩内置的 ffmpeg 释放到本机缓存并预热一次执行，
                # 之后启动直接复用；不阻塞窗口显示。
                ok = resources.warm_up()
                if ok:
                    STATE.log('内置 ffmpeg 已就绪（本机缓存：%s）'
                              % resources.ffmpeg_path())
                else:
                    STATE.log('提示：内置 ffmpeg 预热未完成，首次导出会自动重试')

            threading.Thread(target=_warm, daemon=True).start()
        else:
            p = exporters.find_ffmpeg(BASE_DIR)
            STATE.log(('内置 ffmpeg 已就绪：%s' % p) if os.path.isfile(p)
                      else '警告：未找到可用 ffmpeg，导出将失败')
    except Exception:  # noqa: BLE001
        STATE.log('资源初始化提示：' + traceback.format_exc(limit=2))


def start_server_in_thread(port=8787):
    """独立窗口模式：后台线程跑本地服务，返回界面地址（不打开浏览器）。"""
    httpd, url = create_server(port)
    _log_startup(url)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return url


def serve(open_browser=True, port=8787):
    """浏览器/排障模式：当前线程阻塞运行服务。"""
    httpd, url = create_server(port)
    _log_startup(url)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


INDEX_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Pocket Motion 4 · Osmo Pocket 4 实况导出</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px/1.5 "Microsoft YaHei UI","Segoe UI",sans-serif;background:#f4f6f8;color:#1f2937}
header{background:#fff;padding:16px 24px;border-bottom:1px solid #e5e7eb}
h1{margin:0;font-size:20px;font-weight:700;color:#0f766e}
.sub{color:#6b7280;font-size:12px;margin-top:3px}
.wrap{display:flex;gap:16px;padding:16px 20px;align-items:flex-start}
.side{width:330px;flex:none}.main{flex:1;min-width:0}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px;margin-bottom:14px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.card h2{font-size:13px;margin:0 0 10px;color:#374151;font-weight:700}
label{display:block;font-size:12px;color:#6b7280;margin:8px 0 4px}
input[type=text],select{width:100%;padding:7px 9px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;outline:none}
input[type=text]:focus,select:focus{border-color:#0d9488}
.row{display:flex;gap:6px;align-items:center;margin-top:6px}
button{font:inherit;font-size:12px;padding:7px 12px;border:1px solid #d1d5db;background:#fff;color:#374151;border-radius:6px;cursor:pointer}
button:hover:not(:disabled){background:#f3f4f6}
button.primary{background:#0d9488;border-color:#0d9488;color:#fff;font-weight:600}
button.primary:hover:not(:disabled){background:#0f766e}
button:disabled{opacity:.5;cursor:not-allowed}
button.ghost{border:none;color:#0d9488;padding:4px 6px}
.inline{display:flex;align-items:center;gap:6px;font-size:12px;color:#4b5563}
.hint{font-size:11px;color:#9ca3af;line-height:1.6;margin-top:8px}
.bar{height:6px;background:#e5e7eb;border-radius:4px;overflow:hidden;margin-top:10px}
.bar i{display:block;height:100%;background:#0d9488;width:0;transition:width .2s}
.stats{display:flex;align-items:center;gap:16px;font-size:12px;color:#4b5563;margin-bottom:10px;flex-wrap:wrap}
.stats b{color:#0f766e;font-size:14px}
.tabs{display:flex;gap:6px}
.tabs button.on{background:#0d9488;color:#fff;border-color:#0d9488}
.tools{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;font-size:12px}
th{background:#f9fafb;text-align:left;padding:9px 10px;color:#6b7280;font-weight:600;border-bottom:1px solid #e5e7eb;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #f3f4f6;vertical-align:top}
tr:hover td{background:#f0fdfa}
td.reason{color:#6b7280;max-width:340px;word-break:break-all}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;background:#ccfbf1;color:#0f766e;font-size:11px}
.log{background:#111827;color:#a7f3d0;border-radius:8px;padding:10px;height:170px;overflow:auto;font:11px/1.6 Consolas,monospace;white-space:pre-wrap;margin-top:12px}
.modal{position:fixed;inset:0;background:rgba(17,24,39,.45);display:none;align-items:center;justify-content:center;z-index:20}
.modal.show{display:flex}
.box{background:#fff;border-radius:12px;width:640px;max-width:92vw;max-height:80vh;display:flex;flex-direction:column;padding:16px}
.box h3{margin:0 0 8px;font-size:15px}
.list{flex:1;overflow:auto;border:1px solid #e5e7eb;border-radius:8px;padding:6px;min-height:260px}
.item{padding:7px 9px;cursor:pointer;border-radius:5px;font-size:13px}
.item:hover{background:#f0fdfa;color:#0f766e}
.path{font:11px Consolas,monospace;color:#6b7280;word-break:break-all;padding:6px 0}
.foot{display:flex;justify-content:space-between;align-items:center;margin-top:10px;gap:8px}
.notice{background:#fffbeb;border:1px solid #fde68a;color:#92400e;padding:9px 12px;border-radius:8px;font-size:12px;margin-bottom:12px}
.ok{background:#ecfdf5;border-color:#a7f3d0;color:#065f46}
</style></head><body>
<header><h1>Pocket Motion 4</h1><div class="sub">专为 Osmo Pocket 4 的 3 秒实况短片优化 · Apple 实况照片 (HEIC+MOV) / Google 动态照片 (JPG) · 保留真实设备型号与拍摄时间</div></header>
<div class="wrap">
 <div class="side">
  <div class="card"><h2>目录</h2>
   <label>素材源目录（SD 卡 DCIM 或其上级目录）</label><input id="src" type="text" placeholder="例如 D:\DCIM\DJI_001">
   <div class="row"><button onclick="pickDir('src')">浏览…</button><button class="primary" onclick="doScan()">扫描 / 刷新</button></div>
   <label>导出位置</label><input id="out" type="text">
   <div class="row"><button onclick="pickDir('out')">浏览…</button><button onclick="api('/api/open_out',{})">打开导出目录</button></div>
   <label class="inline" style="margin-top:10px"><input id="rec" type="checkbox" checked> 递归扫描子目录（DCIM / DJI_001 等）</label>
  </div>
  <div class="card"><h2>实况识别规则</h2>
   <label class="inline"><input id="rule" type="checkbox" checked> 启用「短片时长」判据（Pocket 4 关键）</label>
   <div class="row" style="margin-top:8px"><span style="font-size:12px;color:#6b7280">时长区间</span>
    <input id="min" type="text" style="width:58px" value="0.30"><span>~</span>
    <input id="max" type="text" style="width:58px" value="5.00"><span style="font-size:12px;color:#6b7280">秒</span></div>
   <div class="hint">任一条件命中即为实况：DJI TPLG 实况模板串（兼容 4P/3）、3 秒短片时长、与静态 JPG 成对、文件名实况标记。长视频仍归入「视频」。</div>
  </div>
  <div class="card"><h2>拍摄设备信息</h2>
   <label>留空＝自动从文件解析；也可强制指定写入 EXIF / QuickTime</label>
   <input id="dev" type="text" list="devlist" placeholder="留空自动识别">
   <datalist id="devlist"><option value="DJI Osmo Pocket 4"><option value="DJI Osmo Pocket 4P"><option value="DJI Osmo Pocket 3"><option value="DJI Pocket 2"></datalist>
   <div class="hint">型号会写入 HEIC / MOV / JPG 的 EXIF Make、Model 与 QuickTime mdta，也可在此手动指定覆盖。</div>
  </div>
  <div class="card"><h2>导出</h2>
   <label class="inline"><input type="radio" name="fmt" value="apple" checked> Apple 实况照片 (.HEIC + .MOV)</label>
   <label class="inline"><input type="radio" name="fmt" value="google"> Google 动态照片 (.jpg)</label>
   <label class="inline" style="margin-top:6px"><input type="checkbox" id="h264"> 动态照片视频转 H.264（兼容优先，会重编码）</label>
   <button class="primary" style="width:100%;margin-top:12px;padding:9px" id="btnExport" onclick="doExport()">导出选中素材</button>
   <button style="width:100%;margin-top:8px;padding:7px" onclick="doVerify()">验证导出结果 · 检查是否含动态视频</button>
   <div class="bar"><i id="barfill"></i></div>
   <div class="hint" id="progress">就绪</div>
   <div class="hint" style="margin-top:10px;background:#fffbeb;border:1px solid #fde68a;color:#92400e;padding:8px;border-radius:6px;line-height:1.6">
    <b>为什么看起来像静态图？</b><br>Windows 看图软件只会显示静态封面，属正常现象。Apple 实况需把<b>同名 .HEIC + .MOV 一起</b>导入 iPhone 相册；Google 动态照片需导入 <b>Google 相册</b>（单个 .jpg）。导出日志会写明「动态视频校验通过：3.00s / 90 帧」，也可点上方按钮复核。
   </div>
  </div>
  <div class="card"><h2>帮助与内置资源</h2>
   <button style="width:100%" onclick="showDocs()">使用说明</button>
   <button style="width:100%;margin-top:8px" onclick="doSelftest()">运行一键自检</button>
   <button style="width:100%;margin-top:8px" onclick="exportBundle()">导出说明与源码补丁</button>
   <div class="hint">本版只有一个 exe：视频引擎、窗口图标、使用说明与 Dart
    源码补丁都已封装在程序内部。需要这些文件时点「导出说明与源码补丁」，
    会写出到上方设置的导出位置。</div>
  </div>
 </div>
 <div class="main">
  <div id="notice" class="notice">请先选择素材源目录并点击「扫描 / 刷新」。若识别有误，可选中文件后手动「设为实况 / 设为视频」，或用「诊断」查看文件特征。</div>
  <div class="card" style="padding:12px">
   <div class="stats"><span>共 <b id="sAll">0</b> 项</span><span>实况 <b id="sLive">0</b></span><span>视频 <b id="sVideo">0</b></span><span>照片 <b id="sPhoto">0</b></span>
    <span style="flex:1"></span>
    <div class="tabs"><button class="on" data-k="live" onclick="tab('live')">实况</button><button data-k="video" onclick="tab('video')">视频</button><button data-k="photo" onclick="tab('photo')">照片</button></div></div>
   <div class="tools"><button onclick="selAll()">全选当前</button><button onclick="selNone()">取消选择</button>
    <button onclick="override('live')">设为实况</button><button onclick="override('video')">设为视频</button>
    <button onclick="diagnose()">诊断选中文件</button><span style="font-size:11px;color:#9ca3af">已选 <b id="selCount">0</b> 项（按住 Ctrl / Shift 可多选）</span></div>
   <div style="overflow:auto;max-height:420px;border:1px solid #e5e7eb;border-radius:8px">
   <table><thead><tr><th style="width:28px"></th><th>文件名</th><th>时长</th><th>拍摄时间</th><th>设备型号</th><th>大小</th><th>识别依据 / 信息来源</th></tr></thead><tbody id="rows"></tbody></table></div>
   <div class="log" id="log"></div>
  </div>
  <div class="foot"><span style="font-size:11px;color:#9ca3af">v4.2.1 单文件版 · 所有处理均在本机完成，服务仅监听 127.0.0.1</span><button class="ghost" onclick="shutdown()">退出程序</button></div>
 </div>
</div>
<div class="modal" id="modal"><div class="box"><h3>选择文件夹</h3><div class="path" id="curPath"></div>
 <div class="list" id="dirList"></div><div class="foot"><button onclick="closeModal()">取消</button><div>
 <button onclick="navUp()">上一级</button> <button class="primary" onclick="chooseDir()">选择当前目录</button></div></div></div></div>
<div class="modal" id="docsModal"><div class="box" style="width:760px"><h3>使用说明 · Pocket Motion 4 v4.2.1 单文件版</h3>
 <div class="list" id="docsText" style="white-space:pre-wrap;font:12px/1.65 Consolas,'Microsoft YaHei UI',monospace;padding:12px"></div>
 <div class="foot"><span style="font-size:11px;color:#9ca3af">内容直接读自 exe 内置资源，无需任何外部文件</span><button class="primary" onclick="closeDocs()">关闭</button></div></div></div>
<script>
let assets=[],kind='live',sel=new Set(),ver=-1,logN=0,pickTarget='src',curDir='',busy=false,lastResultId=0;
const $=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});return r.json()}
async function get(path){const r=await fetch(path);return r.json()}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function mb(n){return (n/1048576).toFixed(1)+' MB'}
async function init(){const c=await get('/api/config');$('src').value=c.src||'';$('out').value=c.out||'';$('min').value=c.min_dur;$('max').value=c.max_dur;$('rule').checked=!!c.duration_rule;$('rec').checked=!!c.recursive;$('dev').value=c.device_override||'';$('h264').checked=!!c.h264_compat;saveCfg();poll()}
function saveCfg(){return api('/api/config',{src:$('src').value.trim(),out:$('out').value.trim(),min_dur:parseFloat($('min').value)||0.3,max_dur:parseFloat($('max').value)||5,duration_rule:$('rule').checked,recursive:$('rec').checked,device_override:$('dev').value.trim(),h264_compat:$('h264').checked})}
async function doScan(){await saveCfg();await api('/api/scan',{});logN=0;$('notice').className='notice';$('notice').textContent='正在扫描…'}
function tab(k){kind=k;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.k===k));render()}
function visible(){return assets.filter(a=>a.kind===kind)}
function selAll(){visible().forEach(a=>sel.add(a.path));render()}
function selNone(){sel.clear();render()}
async function override(k){if(!sel.size){alert('请先选中素材');return}await api('/api/override',{paths:[...sel],kind:k});await refresh()}
async function doExport(){if(!sel.size){alert('请先选中要导出的素材');return}await saveCfg();const fmt=document.querySelector('input[name=fmt]:checked').value;await api('/api/export',{paths:[...sel],fmt,h264_compat:$('h264').checked});logN=0}
async function doVerify(){await saveCfg();const r=await api('/api/verify',{});if(r.ok===false){alert(r.msg||'正在处理，请稍候');return}logN=0;$('notice').className='notice';$('notice').textContent='正在逐个解码校验导出文件里的动态视频，请查看下方日志…'}
async function showDocs(){const r=await get('/api/docs');$('docsText').textContent=r.text||'（内置说明不可用）';$('docsModal').classList.add('show')}
function closeDocs(){$('docsModal').classList.remove('show')}
async function doSelftest(){if(busy){alert('正在处理，请稍候');return}const r=await api('/api/selftest',{});if(r.ok===false){alert(r.msg||'正在处理，请稍候');return}logN=0;$('notice').className='notice';$('notice').textContent='正在运行一键自检（生成模拟素材并实际解码校验），结果会显示在下方日志区…'}
async function exportBundle(){const r=await api('/api/export_bundle',{dir:$('out').value.trim()});if(r.ok){alert('已导出 '+r.count+' 个文件到：\n'+r.path)}else{alert('导出失败：'+(r.msg||'未知原因'))}}
function render(){const v=visible();$('selCount').textContent=v.filter(a=>sel.has(a.path)).length;
 $('rows').innerHTML=v.map(a=>'<tr><td><input type="checkbox" class="pick" data-p="'+esc(a.path)+'" '+(sel.has(a.path)?'checked':'')+'></td>'
  +'<td><span class="tag">'+a.kind+'</span> '+esc(a.filename)+'</td><td>'+(a.duration?a.duration.toFixed(2)+'s':'-')+'</td><td>'+esc(a.shot_at||'-')+'</td><td>'+esc(a.model||'-')+'</td><td>'+mb(a.size_bytes)+'</td><td class="reason">'+esc(a.reason)+'</td></tr>').join('');
 document.querySelectorAll('#rows .pick').forEach(el=>el.onchange=()=>{if(el.checked)sel.add(el.dataset.p);else sel.delete(el.dataset.p);render()});
 $('sAll').textContent=assets.length;['live','video','photo'].forEach(k=>$('s'+k[0].toUpperCase()+k.slice(1)).textContent=assets.filter(a=>a.kind===k).length)}
async function refresh(){const d=await get('/api/assets');assets=d;render()}
async function poll(){try{const s=await get('/api/status');
 if(s.logs&&s.logs.length){$('log').textContent=s.logs.join('\n');$('log').scrollTop=$('log').scrollHeight}
 if(s.assets_version!==ver){ver=s.assets_version;await refresh()}
 busy=s.busy;$('btnExport').disabled=busy;
 if(s.total>0){$('barfill').style.width=(100*s.done/s.total)+'%';$('progress').textContent=s.done+' / '+s.total}else $('progress').textContent=s.busy?({scan:'扫描中…',export:'导出中…',verify:'验证中…',selftest:'自检中…'}[s.phase]||'处理中…'):'就绪';
 if(s.result&&s.result_id>lastResultId){lastResultId=s.result_id;$('notice').className='notice ok';$('notice').textContent='导出完成：成功 '+s.result[0]+' 项，失败 '+s.result[1]+' 项。输出目录：'+$('out').value}
 }catch(e){}setTimeout(poll,600)}
async function pickDir(t){pickTarget=t;curDir='';await nav('');$('modal').classList.add('show')}
function closeModal(){$('modal').classList.remove('show')}
async function nav(p){const d=await get('/api/browse?path='+encodeURIComponent(p));curDir=d.current||'';
 $('curPath').textContent=curDir||'选择一个磁盘或常用位置';
 const items=(d.drives||[]).map(x=>({name:x,path:x})).concat(d.dirs||[]);
 $('dirList').innerHTML=items.map(x=>'<div class="item">📁 '+esc(x.name)+(x.path!==x.name?' <span style="color:#9ca3af;font-size:11px">'+esc(x.path)+'</span>':'')+'</div>').join('')||'<div class="item">（无子目录）</div>';
 $('dirList').querySelectorAll('.item').forEach((el,i)=>el.onclick=()=>nav(items[i].path));
 if(!curDir&&d.drives&&d.drives.length)$('curPath').textContent='选择一个磁盘或常用位置'}
function navUp(){const p=curDir?curDir.split(/[\\/]/).slice(0,-1).join('\\'):'';nav(p)}
async function chooseDir(){if(!curDir)return;$(pickTarget).value=curDir;closeModal();await saveCfg();if(pickTarget==='src')doScan()}
async function diagnose(){if(!sel.size){alert('请先选中一个素材');return}const r=await api('/api/diagnose',{path:[...sel][0]});alert(r.text||'无信息')}
async function shutdown(){if(confirm('确定退出 Pocket Motion 4？')){await api('/api/shutdown',{});document.body.innerHTML='<div style="padding:60px;text-align:center;color:#6b7280">程序已退出，可关闭此页面。</div>'}}
init();
</script></body></html>'''
