# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""MP4 / QuickTime 容器解析（纯 Python 标准库，零外部依赖）。

用于：
  * 精确读取视频时长（mvhd / mdhd），不依赖 ffprobe；
  * 读取 QuickTime mdta 元数据（Make / Model / CreationDate / GPS）；
  * 读取 DJI 私有数据轨中的模板串与固件签名；
  * 读取 JPEG EXIF（设备型号 / 原始拍摄时间）。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import struct

QT_EPOCH_OFFSET = 2082844800  # 1904-01-01 -> 1970-01-01


def u16(b, o):
    return struct.unpack_from('>H', b, o)[0]


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def u64(b, o):
    return struct.unpack_from('>Q', b, o)[0]


def fourcc(b, o):
    return b[o:o + 4].decode('latin-1')


class Atom:
    __slots__ = ('pos', 'size', 'hdr', 'type', 'body', 'end')

    def __init__(self, pos, size, hdr, typ):
        self.pos = pos
        self.size = size
        self.hdr = hdr
        self.type = typ
        self.body = pos + hdr
        self.end = pos + size

    def __repr__(self):
        return '<Atom %s @%d size=%d>' % (self.type, self.pos, self.size)


def top_atoms(f, file_len, limit_mb=64):
    """遍历顶层原子（基于文件句柄 seek，不把整个文件读入内存）。"""
    pos = 0
    while pos + 8 <= file_len:
        f.seek(pos)
        hdr = f.read(16)
        if len(hdr) < 8:
            break
        size = u32(hdr, 0)
        typ = fourcc(hdr, 4)
        hlen = 8
        if size == 1 and len(hdr) >= 16:
            size = u64(hdr, 8)
            hlen = 16
        elif size == 0:
            size = file_len - pos
        if size < hlen or pos + size > file_len:
            break
        yield Atom(pos, size, hlen, typ)
        pos += size


def children(buf, start, end):
    """遍历内存 buffer 中的同级原子。"""
    pos = start
    while pos + 8 <= end:
        size = u32(buf, pos)
        typ = fourcc(buf, pos + 4)
        hlen = 8
        if size == 1 and pos + 16 <= end:
            size = u64(buf, pos + 8)
            hlen = 16
        elif size == 0:
            size = end - pos
        if size < hlen or pos + size > end:
            break
        yield Atom(pos, size, hlen, typ)
        pos += size


def qt_time_to_datetime(sec):
    if not sec or sec <= QT_EPOCH_OFFSET:
        return None
    try:
        return _dt.datetime.fromtimestamp(sec - QT_EPOCH_OFFSET, _dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_mvhd(buf, a):
    """返回 (timescale, duration, creation_datetime_utc)。"""
    body = a.body
    if body + 12 > a.end:
        return None
    ver = buf[body]
    try:
        if ver == 1 and body + 32 <= a.end:
            creation = qt_time_to_datetime(u64(buf, body + 4))
            timescale = u32(buf, body + 20)
            duration = u64(buf, body + 24)
        else:
            creation = qt_time_to_datetime(u32(buf, body + 4))
            timescale = u32(buf, body + 12)
            duration = u32(buf, body + 16)
    except struct.error:
        return None
    return timescale, duration, creation


def _parse_mdhd(buf, a):
    body = a.body
    if body + 20 > a.end:
        return None
    ver = buf[body]
    try:
        if ver == 1 and body + 32 <= a.end:
            creation = qt_time_to_datetime(u64(buf, body + 4))
            timescale = u32(buf, body + 20)
            duration = u64(buf, body + 24)
        else:
            creation = qt_time_to_datetime(u32(buf, body + 4))
            timescale = u32(buf, body + 12)
            duration = u32(buf, body + 16)
    except struct.error:
        return None
    return timescale, duration, creation


def _parse_stts(buf, a):
    """返回 (sample_count, first_delta, total_duration)。"""
    body = a.body
    if body + 8 > a.end:
        return None
    try:
        count = u32(buf, body + 4)
    except struct.error:
        return None
    p = body + 8
    total = 0
    samples = 0
    first_delta = 0
    for _ in range(min(count, 4096)):
        if p + 8 > a.end:
            break
        scount = u32(buf, p)
        sdelta = u32(buf, p + 4)
        if first_delta == 0 and scount:
            first_delta = sdelta
        samples += scount
        total += scount * sdelta
        p += 8
    return samples, first_delta, total


def _read_data_atom(buf, a):
    """解析 ilst item 中的 data 原子。"""
    for sub in children(buf, a.body, a.end):
        if sub.type == 'data' and sub.end - sub.body >= 8:
            raw = bytes(buf[sub.body + 8:sub.end])
            return raw.decode('utf-8', 'replace').rstrip('\x00').strip()
    # 老式 ©xxx 原子：2 字节长度 + 文本
    if a.end - a.body > 4:
        try:
            n = u16(buf, a.body + 2)
            if a.body + 4 + n <= a.end:
                return bytes(buf[a.body + 4:a.body + 4 + n]).decode('utf-8', 'replace').strip()
        except struct.error:
            pass
    return None


def _parse_meta(buf, a, out):
    """解析 QuickTime meta (keys/ilst) 以及 udta 里的 ©mak/©mod/©xyz。"""
    # meta 是 FullBox，前 4 字节是 version/flags
    start = a.body + 4 if a.end - a.body >= 4 else a.body
    subs = list(children(buf, start, a.end))
    key_names = {}
    for s in subs:
        if s.type == 'keys' and s.end - s.body >= 8:
            try:
                count = u32(buf, s.body + 4)
            except struct.error:
                continue
            p = s.body + 8
            for i in range(1, count + 1):
                if p + 8 > s.end:
                    break
                ksize = u32(buf, p)
                if ksize < 8 or p + ksize > s.end:
                    break
                key_names[i] = bytes(buf[p + 8:p + ksize]).decode('utf-8', 'replace')
                p += ksize
    for s in subs:
        if s.type == 'ilst':
            for idx, item in enumerate(children(buf, s.body, s.end), start=1):
                if item.end - item.body < 4:
                    continue
                item_idx = u32(buf, item.body + 4) if item.body + 8 <= item.end else idx
                key = key_names.get(item_idx)
                if not key:
                    continue
                # data 原子在 item body 之后
                val = None
                for d in children(buf, item.body, item.end):
                    if d.type == 'data':
                        val = _read_data_atom(buf, d)
                        break
                if val:
                    out.setdefault(key.lower(), val)
        elif s.type in ('\xa9mak', '\xa9mod', '\xa9xyz', '\xa9swr', '\xa9day'):
            val = _read_data_atom(buf, s)
            if val:
                out.setdefault(s.type, val)
        elif s.type in ('udta', 'meta'):
            _parse_meta(buf, s, out)


def parse_moov(buf):
    """解析 moov 内容，返回结构化信息字典。"""
    info = {
        'movie_timescale': 0,
        'movie_duration': 0,
        'creation_utc': None,
        'video_duration': 0.0,
        'video_timescale': 0,
        'frame_delta': 0,
        'track_count': 0,
        'max_track_id': 0,
        'video_track_id': 0,
        'width': 0,
        'height': 0,
        'codec': '',
        'keys': {},
    }

    def walk_trak(a):
        t = {'track_id': 0, 'is_video': False, 'timescale': 0,
             'duration': 0, 'frame_delta': 0, 'width': 0, 'height': 0,
             'codec': '', 'creation_utc': None, 'duration_total': 0}

        def walk(s, e):
            for c in children(buf, s, e):
                if c.type == 'tkhd' and c.end - c.body >= 20:
                    ver = buf[c.body]
                    try:
                        t['track_id'] = u32(buf, c.body + (20 if ver == 1 else 12))
                    except struct.error:
                        pass
                elif c.type == 'mdhd':
                    r = _parse_mdhd(buf, c)
                    if r:
                        t['timescale'], t['duration'], t['creation_utc'] = r
                elif c.type == 'stts':
                    r = _parse_stts(buf, c)
                    if r:
                        t['frame_delta'] = r[1]
                        t['duration_total'] = r[2]
                elif c.type == 'hdlr' and c.end - c.body >= 12:
                    if fourcc(buf, c.body + 8) == 'vide':
                        t['is_video'] = True
                elif c.type == 'stsd' and c.end - c.body >= 16:
                    # stsd: FullBox(4) + entry_count(4) + sample entry
                    p = c.body + 8
                    if p + 8 <= c.end:
                        esize = u32(buf, p)
                        etype = fourcc(buf, p + 4)
                        t['codec'] = etype
                        if p + 86 <= c.end:
                            try:
                                t['width'] = u16(buf, p + 32)
                                t['height'] = u16(buf, p + 34)
                            except struct.error:
                                pass
                elif c.type in ('mdia', 'minf', 'stbl'):
                    walk(c.body, c.end)
                elif c.type == 'meta' or c.type == 'udta':
                    _parse_meta(buf, c, info['keys'])

        walk(a.body, a.end)
        return t

    for a in children(buf, 0, len(buf)):
        if a.type == 'mvhd':
            r = _parse_mvhd(buf, a)
            if r:
                info['movie_timescale'], info['movie_duration'], info['creation_utc'] = r
        elif a.type == 'trak':
            t = walk_trak(a)
            info['track_count'] += 1
            info['max_track_id'] = max(info['max_track_id'], t['track_id'])
            if t['is_video']:
                dur = (t['duration_total'] or t['duration']) / (t['timescale'] or 1)
                if dur > 0:
                    info['video_duration'] = dur
                    info['video_timescale'] = t['timescale']
                    info['frame_delta'] = t['frame_delta']
                    info['video_track_id'] = t['track_id']
                    info['width'] = t['width']
                    info['height'] = t['height']
                    info['codec'] = t['codec']
            if info['creation_utc'] is None and t['creation_utc']:
                info['creation_utc'] = t['creation_utc']
        elif a.type in ('meta', 'udta'):
            _parse_meta(buf, a, info['keys'])

    if not info['video_duration'] and info['movie_timescale']:
        info['video_duration'] = info['movie_duration'] / info['movie_timescale']
    return info


# ---------------------------------------------------------------------------
# 设备型号归一化
# ---------------------------------------------------------------------------

_MODEL_PATTERNS = [
    # 紧凑写法：OsmoPocket4P / OSMOPOCKET4 / DJIPOCKET4 / POCKET4P
    re.compile(r'(?:DJI[\s_-]*)?OSMO[\s_-]*POCKET[\s_-]*([0-9])\s*([A-Za-z]{0,2})\b', re.I),
    re.compile(r'(?:DJI[\s_-]*)?POCKET[\s_-]*([0-9])\s*([A-Za-z]{0,2})\b', re.I),
    # 带空格写法：Osmo Pocket 4 / DJI OSMO POCKET 4
    re.compile(r'OSMO\s+POCKET\s+([0-9])\s*([A-Za-z]{0,2})\b', re.I),
    # 产品代号：OP4 / OP4P / P4P
    re.compile(r'\b(?:OP|OSMOP)([0-9])([A-Za-z]{0,2})\b', re.I),
]


def normalize_model(raw):
    """把固件里各种写法归一化为 'DJI Osmo Pocket 4' 这类可读型号；无法识别返回 None。"""
    if not raw:
        return None
    s = str(raw).strip()
    if not s or len(s) > 96:
        return None
    if 'TPLG' in s or s.startswith('DJI_20') or s.lower() in ('dji', 'apple', 'unknown'):
        return None
    for pat in _MODEL_PATTERNS:
        m = pat.search(s)
        if m:
            num = m.group(1)
            suffix = (m.group(2) or '').upper()
            return 'DJI Osmo Pocket %s%s' % (num, suffix)
    # QuickTime mdta model 常见值直接是 "Osmo Pocket 4" / "Pocket 4"
    low = s.lower()
    if 'pocket' in low and re.search(r'pocket\s*[0-9]', low):
        digits = re.search(r'pocket\s*([0-9])\s*([a-z]{0,2})', low)
        if digits:
            return 'DJI Osmo Pocket %s%s' % (digits.group(1), digits.group(2).upper())
    return None


def find_model_in_text(text):
    """在二进制文本片段里寻找最可信的设备型号。"""
    if not text:
        return None
    # 优先匹配带 DJI / Osmo 前缀的完整型号
    pats = [
        re.compile(r'DJI[\s_-]{0,3}Osmo[\s_-]{0,3}Pocket[\s_-]{0,3}[0-9][A-Za-z]{0,2}', re.I),
        re.compile(r'Osmo[\s_-]{0,3}Pocket[\s_-]{0,3}[0-9][A-Za-z]{0,2}', re.I),
        re.compile(r'DJI[\s_-]{0,3}Pocket[\s_-]{0,3}[0-9][A-Za-z]{0,2}', re.I),
        re.compile(r'Pocket[\s_-]{0,3}[0-9][A-Za-z]{0,2}', re.I),
        re.compile(r'\bOP[0-9][A-Za-z]{0,2}\b', re.I),
    ]
    for pat in pats:
        m = pat.search(text)
        if m:
            norm = normalize_model(m.group(0))
            if norm:
                return norm
    return None


# ---------------------------------------------------------------------------
# JPEG EXIF 读取（轻量，只读 IFD0 / ExifIFD 的必要 tag）
# ---------------------------------------------------------------------------

def read_jpeg_exif(path):
    """返回 {'make','model','datetime_original','width','height'}。"""
    result = {}
    try:
        with open(path, 'rb') as f:
            data = f.read(256 * 1024)
    except OSError:
        return result
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return result
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        seg_len = (data[pos] << 8) | data[pos + 1]
        if seg_len < 2 or pos + seg_len > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2) and seg_len >= 7:
            result['height'] = (data[pos + 3] << 8) | data[pos + 4]
            result['width'] = (data[pos + 5] << 8) | data[pos + 6]
        if marker == 0xE1 and data[pos + 2:pos + 8] == b'Exif\x00\x00':
            tiff = data[pos + 8:pos + seg_len]
            _parse_tiff(tiff, result)
        pos += seg_len
        if marker == 0xDA:
            break
    return result


def _parse_tiff(tiff, out):
    if len(tiff) < 8:
        return
    bo = '>' if tiff[:2] == b'MM' else '<'

    def _u16(o):
        return struct.unpack_from(bo + 'H', tiff, o)[0] if o + 2 <= len(tiff) else 0

    def _u32(o):
        return struct.unpack_from(bo + 'I', tiff, o)[0] if o + 4 <= len(tiff) else 0

    def read_ifd(off, tag_map):
        if off + 2 > len(tiff):
            return
        n = _u16(off)
        exif_off = 0
        for i in range(min(n, 256)):
            e = off + 2 + i * 12
            if e + 12 > len(tiff):
                break
            tag = _u16(e)
            typ = _u16(e + 2)
            count = _u32(e + 4)
            valoff = e + 8
            size_map = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
            total = size_map.get(typ, 1) * count
            if total > 4:
                valoff = _u32(e + 8)
            if valoff + total > len(tiff):
                continue
            raw = tiff[valoff:valoff + total]
            if tag == 0x8769 and typ == 4:
                exif_off = _u32(valoff)
            elif tag in tag_map and typ in (2, 7):
                out[tag_map[tag]] = raw.decode('ascii', 'replace').rstrip('\x00').strip()
        return exif_off

    try:
        ifd0 = _u32(4)
        exif_off = read_ifd(ifd0, {0x010F: 'make', 0x0110: 'model', 0x0132: 'datetime'})
        if exif_off:
            read_ifd(exif_off, {0x9003: 'datetime_original', 0x9004: 'datetime_digitized'})
    except (struct.error, IndexError):
        pass


def parse_exif_datetime(s):
    if not s:
        return None
    m = re.match(r'(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})', s)
    if not m:
        return None
    try:
        return _dt.datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None


def parse_filename_time(name):
    """DJI_20261022215019_0170_D.MP4 -> datetime(2026,10,22,21,50,19)"""
    m = re.search(r'(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)', name)
    if not m:
        return None
    try:
        return _dt.datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None


def read_mp4_info(path, head_bytes=512 * 1024, tail_bytes=512 * 1024):
    """读取 MP4 的时长 / 元数据 / 标记文本。只读取头部、尾部与 moov 原子。"""
    info = {
        'duration': 0.0, 'moov': None, 'keys': {}, 'creation_utc': None,
        'width': 0, 'height': 0, 'codec': '', 'head_text': '', 'tail_text': '',
        'moov_text': '', 'model_hint': None, 'markers': [], 'error': None,
        'track_count': 0, 'frame_delta': 0, 'video_track_id': 0,
        'video_timescale': 0, 'movie_timescale': 600, 'movie_duration': 0,
        'max_track_id': 0,
    }
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            atoms = list(top_atoms(f, size))
            hb = min(size, head_bytes)
            f.seek(0)
            head = f.read(hb)
            info['head_text'] = head.decode('latin-1', 'replace')
            tb = min(max(0, size - hb), tail_bytes)
            if tb > 0:
                f.seek(size - tb)
                tail = f.read(tb)
                info['tail_text'] = tail.decode('latin-1', 'replace')
            for a in atoms:
                if a.type == 'moov':
                    f.seek(a.body)
                    moov = f.read(min(a.size - a.hdr, 32 * 1024 * 1024))
                    info['moov'] = moov
                    info['moov_text'] = moov.decode('latin-1', 'replace')
                    parsed = parse_moov(moov)
                    info['duration'] = parsed['video_duration']
                    info['keys'] = parsed['keys']
                    info['creation_utc'] = parsed['creation_utc']
                    info['width'] = parsed['width']
                    info['height'] = parsed['height']
                    info['codec'] = parsed['codec']
                    info['track_count'] = parsed['track_count']
                    info['frame_delta'] = parsed['frame_delta']
                    info['video_track_id'] = parsed['video_track_id']
                    info['video_timescale'] = parsed['video_timescale']
                    info['movie_timescale'] = parsed['movie_timescale'] or 600
                    info['movie_duration'] = parsed['movie_duration']
                    info['max_track_id'] = parsed['max_track_id']
                    break
            # 若 moov 在顶层原子遍历中没找到（有些相机把 moov 藏在 mdat 之后且 size 异常），
            # 尝试从尾部文本里定位 'moov' 原子头
            if info['moov'] is None and info['tail_text']:
                idx = info['tail_text'].rfind('moov')
                if idx >= 4:
                    start = size - len(info['tail_text'].encode('latin-1', 'replace')) + idx - 4
                    if 0 <= start < size:
                        f.seek(start)
                        raw = f.read(min(size - start, 32 * 1024 * 1024))
                        if len(raw) >= 8:
                            sz = u32(raw, 0)
                            if 8 < sz <= len(raw):
                                moov = raw[8:sz]
                                info['moov'] = moov
                                parsed = parse_moov(moov)
                                info['duration'] = parsed['video_duration']
                                info['keys'] = parsed['keys']
                                info['creation_utc'] = parsed['creation_utc']
                                info['width'] = parsed['width']
                                info['height'] = parsed['height']
                                info['codec'] = parsed['codec']
                                info['track_count'] = parsed['track_count']
                                info['frame_delta'] = parsed['frame_delta']
                                info['video_track_id'] = parsed['video_track_id']
                                info['video_timescale'] = parsed['video_timescale']
                                info['movie_timescale'] = parsed['movie_timescale'] or 600
                                info['movie_duration'] = parsed['movie_duration']
                                info['max_track_id'] = parsed['max_track_id']
    except Exception as exc:  # noqa: BLE001
        info['error'] = str(exc)

    all_text = info['head_text'] + '\n' + info['moov_text'] + '\n' + info['tail_text']
    keys = info['keys']
    for k in ('com.apple.quicktime.model', 'model', '\xa9mod'):
        if keys.get(k):
            norm = normalize_model(keys[k])
            if norm:
                info['model_hint'] = norm
                break
    if not info['model_hint']:
        info['model_hint'] = find_model_in_text(all_text)

    # 收集 TPLG / 实况相关标记
    found = set()
    for pat in (r'TPLG_[\x20-\x7e]{3,80}', r'(?i)LIVE[_ -]?PHOTO', r'(?i)MOTION[_ -]?PHOTO',
                r'(?i)dbgi', r'(?i)DJI[_ -]?LIVE'):
        for m in re.finditer(pat, all_text):
            found.add(m.group(0)[:80])
    info['markers'] = sorted(found)[:40]
    return info
