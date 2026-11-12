# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""媒体扫描与「实况」识别。

Modifications:
  * 实况判定改为多信号：DJI TPLG / LIVEPHOTO / MOTIONPHOTO 模板串（含 null
    交错写法）、MP4 容器精确时长（默认 0.30~5.00s，可配置）、与静态 JPG
    成对、文件名标记；
  * 按顶层原子定位 moov，支持 moov 位于文件尾部；
  * 读取 QuickTime mdta 设备型号与 creation_time、配套 JPG 的 EXIF；
  * 递归子目录、排除 LRF 低码率代理、记录识别依据、支持手动指定类别。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field

from . import mp4

VIDEO_EXTS = {'.mp4', '.mov', '.m4v'}
PHOTO_EXTS = {'.jpg', '.jpeg', '.dng', '.heic', '.png'}

# 明确的「非实况」文件名特征
_PROXY_RE = re.compile(r'(?i)(?:LRF|PROXY|_LOW|_PREVIEW|THUMB)')
_LIVE_NAME_RE = re.compile(r'(?i)(?:LIVE[_ -]?PHOTO|MOTION[_ -]?PHOTO|[_ -]LP\b|[_ -]LIVE\b)')
_STEM_KEY_RE = re.compile(r'(?i)^(DJI[_-])?(\d{14})[_-](\d{3,5})')


@dataclass
class MediaAsset:
    path: str
    filename: str
    kind: str = 'video'                  # live | video | photo
    shot_at: _dt.datetime | None = None
    size_bytes: int = 0
    duration: float = 0.0
    model: str = ''
    make: str = 'DJI'
    time_source: str = ''
    model_source: str = ''
    reason: str = ''
    markers: list = field(default_factory=list)
    paired_jpg: str | None = None
    mp4_info: dict | None = None
    override_kind: str | None = None    # 用户手动指定：live / video
    width: int = 0
    height: int = 0

    @property
    def effective_kind(self):
        return self.override_kind or self.kind

    @property
    def kind_label(self):
        return {'live': '实况', 'video': '视频', 'photo': '照片'}.get(self.effective_kind, '?')

    @property
    def friendly_spec(self):
        bits = []
        if self.duration:
            bits.append('%.2fs' % self.duration)
        if self.width and self.height:
            bits.append('%dx%d' % (self.width, self.height))
        if self.mp4_info and self.mp4_info.get('codec'):
            bits.append(self.mp4_info['codec'])
        return ' / '.join(bits)


def _strip_nulls(s):
    """把 UTF-16LE / null 交错写法折叠成普通 ASCII 文本。"""
    return s.replace('\x00', '')


def _has_live_marker(info):
    """检查 DJI 私有模板串与其它实况标记。返回 (命中, 描述)。"""
    texts = [info.get('head_text') or '', info.get('moov_text') or '',
             info.get('tail_text') or '']
    joined = '\n'.join(texts)
    # 原始文本 + 去 null 文本，覆盖 UTF-16LE 交错写法
    candidates = [joined, _strip_nulls(joined)]
    for text in candidates:
        for m in re.finditer(r'TPLG_[\x20-\x7e]{3,80}', text, re.I):
            token = m.group(0)
            if re.search(r'LIVE[_ -]?PHOTO|MOTION[_ -]?PHOTO', token, re.I):
                return True, 'DJI 实况模板串 %s' % token[:48]
        if re.search(r'(?i)TPLG[_-](?:PHOTO|VIDEO)[_-]?LIVE', text):
            return True, 'DJI TPLG LIVE 模板串'
        if re.search(r'(?i)GCamera:MotionPhoto|MotionPhoto="1"', text):
            return True, '动态照片 XMP 标记'
    return False, ''


def _duration_fallback(info):
    """moov 解析失败时，从已有字节文本里搜索 mvhd 头直接算时长。"""
    for key in ('moov_text', 'head_text', 'tail_text'):
        text = info.get(key) or ''
        idx = text.find('mvhd')
        if idx < 0 or idx + 24 > len(text):
            continue
        raw = text[idx + 4:idx + 40].encode('latin-1', 'replace')
        if len(raw) < 20:
            continue
        ver = raw[0]
        try:
            if ver == 1 and len(raw) >= 32:
                ts = mp4.u64(raw, 20)
                dur = mp4.u64(raw, 24)
            else:
                ts = mp4.u32(raw, 12)
                dur = mp4.u32(raw, 16)
            if ts and dur:
                return dur / ts
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def _stem_key(name):
    """把 DJI_20261022215019_0170_D.MP4 规约为配对键。"""
    base = os.path.splitext(name)[0]
    m = _STEM_KEY_RE.match(base)
    if m:
        return m.group(2) + '_' + m.group(3)
    return base.lower()


def _collect_files(dir_path, recursive=True, max_depth=2):
    out = []
    dir_path = os.path.abspath(dir_path)
    if not os.path.isdir(dir_path):
        return out

    def walk(cur, depth):
        try:
            entries = list(os.scandir(cur))
        except OSError:
            return
        for e in entries:
            if e.is_file():
                out.append(e.path)
            elif e.is_dir() and recursive and depth < max_depth:
                low = e.name.lower()
                if low.startswith('.') or low in ('system volume information', '$recycle.bin'):
                    continue
                walk(e.path, depth + 1)
    walk(dir_path, 0)
    return out


def resolve_device(info, paired_jpg, default_model='DJI Osmo Pocket 4'):
    """解析真实拍摄设备型号，返回 (make, model, model_source)。"""
    make = 'DJI'
    # 1) 配套 JPG 的 EXIF（相机直出照片信息最完整、最可信）
    if paired_jpg and os.path.isfile(paired_jpg):
        ex = mp4.read_jpeg_exif(paired_jpg)
        norm = mp4.normalize_model(ex.get('model'))
        if norm:
            return (ex.get('make') or 'DJI'), norm, '配套 JPG EXIF'
        if ex.get('model'):
            return (ex.get('make') or 'DJI'), ex['model'], '配套 JPG EXIF'
    # 2) MP4 QuickTime mdta / udta 元数据
    keys = info.get('keys') or {}
    for k in ('com.apple.quicktime.model', 'model', '\xa9mod'):
        if keys.get(k):
            norm = mp4.normalize_model(keys[k]) or keys[k]
            mk = keys.get('com.apple.quicktime.make') or keys.get('make') or keys.get('\xa9mak') or 'DJI'
            return mk, norm, 'MP4 QuickTime 元数据'
    # 3) 固件签名 / dbgi 文本
    if info.get('model_hint'):
        mk = keys.get('com.apple.quicktime.make') or keys.get('make') or 'DJI'
        return mk, info['model_hint'], 'MP4 固件签名'
    return make, default_model, '未识别（使用默认型号，可手动指定）'


def resolve_shot_at(name, info, paired_jpg, path):
    """解析真实拍摄时间（本地时间），返回 (datetime, 来源)。"""
    # 1) 文件名中的相机本地时间
    t = mp4.parse_filename_time(name)
    if t:
        return t, '文件名时间戳'
    # 2) 配套 JPG 的 EXIF DateTimeOriginal
    if paired_jpg and os.path.isfile(paired_jpg):
        ex = mp4.read_jpeg_exif(paired_jpg)
        t = mp4.parse_exif_datetime(ex.get('datetime_original') or ex.get('datetime'))
        if t:
            return t, '配套 JPG EXIF'
    # 3) MP4 creation_time（QuickTime 1904 epoch，UTC -> 本地）
    c = info.get('creation_utc')
    if c:
        try:
            return c.astimezone().replace(tzinfo=None), 'MP4 creation_time'
        except Exception:  # noqa: BLE001
            pass
    keys = info.get('keys') or {}
    iso = keys.get('com.apple.quicktime.creationdate')
    if iso:
        try:
            return _dt.datetime.fromisoformat(iso).replace(tzinfo=None), 'MP4 creationdate'
        except Exception:  # noqa: BLE001
            pass
    # 4) 文件系统修改时间
    try:
        return _dt.datetime.fromtimestamp(os.path.getmtime(path)), '文件修改时间'
    except OSError:
        return None, '未知'


def scan(dir_path, cfg=None, progress=None):
    """扫描目录，返回 MediaAsset 列表。"""
    cfg = cfg or {}
    min_dur = float(cfg.get('min_live_duration', 0.30))
    max_dur = float(cfg.get('max_live_duration', 5.00))
    duration_rule = bool(cfg.get('duration_rule', True))
    default_model = cfg.get('default_model', 'DJI Osmo Pocket 4')
    recursive = bool(cfg.get('recursive', True))

    files = _collect_files(dir_path, recursive=recursive)
    photos = {}
    for fp in files:
        ext = os.path.splitext(fp)[1].lower()
        if ext in PHOTO_EXTS:
            photos.setdefault(_stem_key(os.path.basename(fp)), fp)

    assets = []
    total = len(files)
    for i, fp in enumerate(files):
        if progress and i % 20 == 0:
            progress(i, total)
        name = os.path.basename(fp)
        ext = os.path.splitext(name)[1].lower()
        try:
            size = os.path.getsize(fp)
        except OSError:
            continue
        key = _stem_key(name)
        paired = photos.get(key)

        if ext in PHOTO_EXTS:
            ex = mp4.read_jpeg_exif(fp) if ext in ('.jpg', '.jpeg') else {}
            t, tsrc = resolve_shot_at(name, {}, None, fp)
            if ex.get('datetime_original'):
                egt = mp4.parse_exif_datetime(ex['datetime_original'])
                if egt:
                    t, tsrc = egt, 'EXIF DateTimeOriginal'
            assets.append(MediaAsset(
                path=fp, filename=name, kind='photo', shot_at=t,
                time_source=tsrc, size_bytes=size,
                model=ex.get('model') or '', make=ex.get('make') or 'DJI',
                model_source='EXIF' if ex.get('model') else '',
                reason='静态照片', width=ex.get('width', 0), height=ex.get('height', 0),
            ))
            continue

        if ext not in VIDEO_EXTS:
            continue
        if _PROXY_RE.search(name):
            # LRF 代理文件不参与导出
            continue

        info = mp4.read_mp4_info(fp)
        duration = info.get('duration') or 0.0
        if not duration:
            duration = _duration_fallback(info)
        if duration:
            info['duration'] = duration

        make, model, model_src = resolve_device(info, paired, default_model)
        shot_at, time_src = resolve_shot_at(name, info, paired, fp)

        marker_hit, marker_desc = _has_live_marker(info)
        reasons = []
        kind = 'video'

        # 信号 1：DJI 私有实况标记（Pocket 3 / 4P 路径）
        if marker_hit:
            kind = 'live'
            reasons.append(marker_desc)
        # 信号 2：容器时长（Pocket 4 的 3 秒 MP4 走这条）
        elif duration_rule and min_dur <= duration <= max_dur:
            kind = 'live'
            reasons.append('短片时长 %.2fs（实况区间 %.1f~%.1fs）' % (duration, min_dur, max_dur))
        # 信号 3：配对静态照片 + 短片
        elif paired and duration and duration <= max_dur * 2:
            kind = 'live'
            reasons.append('与静态照片成对 + 短片 %.2fs' % duration)
        # 信号 4：文件名标记
        elif _LIVE_NAME_RE.search(name):
            kind = 'live'
            reasons.append('文件名含实况标记')

        if paired:
            reasons.append('有配套封面 JPG')
        if not reasons:
            reasons.append('时长 %.2fs，判定为普通视频' % duration)

        assets.append(MediaAsset(
            path=fp, filename=name, kind=kind, shot_at=shot_at,
            size_bytes=size, duration=duration, model=model, make=make,
            time_source=time_src, model_source=model_src,
            reason='；'.join(reasons), markers=info.get('markers', []),
            paired_jpg=paired, mp4_info=info,
            width=info.get('width', 0), height=info.get('height', 0),
        ))

    if progress:
        progress(total, total)
    assets.sort(key=lambda a: a.shot_at or _dt.datetime.min, reverse=True)
    return assets
