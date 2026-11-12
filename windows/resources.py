# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""单文件版资源层：所有外置文件都封装在 exe 内部。

* ffmpeg.exe 以 zlib 压缩内嵌，首次运行释放到 %LOCALAPPDATA%\\PocketMotion4\\bin\\
  并做哈希校验；后续启动直接复用，不再重复解压，也避免每次从临时目录
  运行一个 100MB 的可执行文件被安全软件反复扫描。
* app.ico、使用说明、上游 Dart 补丁作为纯数据内嵌，可在界面里查看/导出。
* 运行目录（exe 所在目录）永远不需要出现第二个文件。

源码模式（未打包）下优先使用工程目录的 ffmpeg.exe / assets\\，方便开发调试。
"""
from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import threading
import zlib

MAGIC = b'PM4FF01\0'
HEADER_LEN = 8 + 8 + 32
_LOCK = threading.Lock()
_CACHE_READY = False

# 内嵌资源 -> 导出时使用的中文路径
BUNDLE_MAP = {
    'docs/readme.txt': '使用说明.txt',
    'patch/PATCH_NOTES.md': '上游源码补丁/PATCH_NOTES.md',
    'patch/lib/main.dart': '上游源码补丁/lib/main.dart',
    'patch/lib/src/models.dart': '上游源码补丁/lib/src/models.dart',
    'patch/lib/src/media_scanner.dart': '上游源码补丁/lib/src/media_scanner.dart',
    'patch/lib/src/media_metadata.dart': '上游源码补丁/lib/src/media_metadata.dart',
    'patch/lib/src/live_photo_exporter.dart': '上游源码补丁/lib/src/live_photo_exporter.dart',
    'patch/lib/src/motion_photo_exporter.dart': '上游源码补丁/lib/src/motion_photo_exporter.dart',
}


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def base_dir():
    """exe 所在目录（源码模式下为工程目录）。"""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def res_root():
    """PyInstaller onefile 运行时的解包目录里的资源根。"""
    if is_frozen():
        return os.path.join(getattr(sys, '_MEIPASS', base_dir()), 'pm4_assets')
    return os.path.join(base_dir(), 'assets')


def res_path(rel):
    return os.path.join(res_root(), *rel.split('/'))


def read_bytes(rel):
    with open(res_path(rel), 'rb') as f:
        return f.read()


def read_text(rel):
    return read_bytes(rel).decode('utf-8', 'replace')


def cache_dir():
    root = (os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
            or tempfile.gettempdir())
    d = os.path.join(root, 'PocketMotion4', 'bin')
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        d = os.path.join(tempfile.gettempdir(), 'PocketMotion4', 'bin')
        os.makedirs(d, exist_ok=True)
        return d


def _external_ffmpeg():
    """兼容旧部署 / 源码模式：同目录放了 ffmpeg.exe 就直接用。"""
    p = os.path.join(base_dir(), 'ffmpeg.exe')
    try:
        if os.path.isfile(p) and os.path.getsize(p) > 1024 * 1024:
            return p
    except OSError:
        pass
    return None


def _read_header():
    """返回 (压缩数据路径, 原始长度, sha256)；内嵌资源缺失时返回 None。"""
    p = res_path('ffmpeg.bin')
    if not os.path.isfile(p):
        return None
    with open(p, 'rb') as f:
        head = f.read(HEADER_LEN)
    if len(head) != HEADER_LEN or head[:8] != MAGIC:
        raise RuntimeError('内置 ffmpeg 数据格式损坏，请重新下载完整的 exe')
    size, digest = struct.unpack('<Q', head[8:16])[0], head[16:48]
    return p, size, digest


def _cache_valid(path, size, digest, stamp):
    """用轻量标记文件避免每次启动都对 100MB 文件做哈希。"""
    try:
        if os.path.getsize(path) != size:
            return False
        with open(stamp, 'r', encoding='ascii') as f:
            if f.read().strip() != digest.hex():
                return False
        return True
    except (OSError, ValueError):
        return False


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.digest()


def _extract_ffmpeg(blob_path, dest, size, digest, stamp):
    """流式解压到临时文件，验证长度与哈希后原子替换。"""
    tmp = dest + '.tmp%d' % os.getpid()
    try:
        with open(blob_path, 'rb') as src, open(tmp, 'wb') as out:
            src.seek(HEADER_LEN)
            dec = zlib.decompressobj()
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(dec.decompress(chunk))
            out.write(dec.flush())
        if os.path.getsize(tmp) != size or _hash_file(tmp) != digest:
            raise RuntimeError('内置 ffmpeg 校验失败，请重新下载完整的 exe')
        os.replace(tmp, dest)
        try:
            with open(stamp, 'w', encoding='ascii') as f:
                f.write(digest.hex())
        except OSError:
            pass
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


def ffmpeg_path():
    """返回可直接执行的 ffmpeg.exe 路径（内嵌时会自动释放到本机缓存）。"""
    global _CACHE_READY
    ext = _external_ffmpeg()
    if ext:
        return ext
    info = _read_header()
    if info is None:
        return 'ffmpeg'  # 源码模式且没有外置文件：退回 PATH，便于开发
    blob, size, digest = info
    d = cache_dir()
    dest = os.path.join(d, 'ffmpeg.exe')
    stamp = os.path.join(d, 'ffmpeg.ok')
    if _CACHE_READY and _cache_valid(dest, size, digest, stamp):
        return dest
    with _LOCK:
        if not _cache_valid(dest, size, digest, stamp):
            # 没有标记但文件已存在时，先做一次哈希，正确就只补标记
            try:
                if os.path.getsize(dest) == size and _hash_file(dest) == digest:
                    with open(stamp, 'w', encoding='ascii') as f:
                        f.write(digest.hex())
                else:
                    _extract_ffmpeg(blob, dest, size, digest, stamp)
            except OSError:
                _extract_ffmpeg(blob, dest, size, digest, stamp)
        _CACHE_READY = True
    return dest


def warm_up():
    """后台预热：提前释放内置 ffmpeg 并触发一次执行，降低首次导出被拦截概率。

    安全软件可能瞬时拦截新释放的 exe（stderr 为空、瞬间退出），重试 3 次。
    """
    try:
        import subprocess
        p = ffmpeg_path()
        kw = {}
        if os.name == 'nt':
            kw['creationflags'] = 0x08000000
        for attempt in range(3):
            try:
                r = subprocess.run([p, '-hide_banner', '-version'],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=30, **kw)
                if r.returncode == 0 and b'ffmpeg version' in (r.stdout or b''):
                    return True
            except (OSError, subprocess.TimeoutExpired):
                pass
            threading.Event().wait(0.8 * (attempt + 1))
        return False
    except Exception:  # noqa: BLE001
        return False


def icon_path():
    p = res_path('app.ico')
    return p if os.path.isfile(p) else None


def has_embedded_ffmpeg():
    return _read_header() is not None


def export_bundle(dest_dir):
    """把使用说明与上游源码补丁导出到指定目录（用户主动触发才会写盘）。"""
    root = os.path.join(dest_dir, 'PocketMotion4-说明与源码补丁')
    n = 0
    for rel, name in BUNDLE_MAP.items():
        src = res_path(rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(root, *name.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, 'rb') as f, open(dst, 'wb') as o:
            o.write(f.read())
        n += 1
    return root, n
