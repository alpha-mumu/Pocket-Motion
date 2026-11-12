# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""端到端自检：生成模拟 Osmo Pocket 4 素材 -> 扫描识别 -> 导出 -> 校验产物。"""
from __future__ import annotations

import datetime as dt
import os
import re
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import resources  # noqa: E402
from core import exporters, metadata_engine as me, mp4, scanner  # noqa: E402

SAMPLES = os.path.join(HERE, '_selftest', 'DCIM', 'DJI_001')
OUTPUT = os.path.join(HERE, '_selftest', 'out')
FFMPEG = resources.ffmpeg_path()


def run(args, check=True):
    # 某些安全软件会在 exe 首次执行时扫描拦截，造成无 stderr 的瞬时失败，重试即可。
    last = None
    for attempt in range(3):
        p = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error'] + args,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        last = p
        if p.returncode == 0 or not check:
            return p
        time.sleep(0.8 * (attempt + 1))
    raise RuntimeError('ffmpeg failed (3 attempts): ' +
                       last.stderr.decode('utf-8', 'replace')[-800:])


def decode_frames(path):
    """完整解码视频流，返回 (返回码, 帧数, 时长秒, 错误摘要)。"""
    p = subprocess.run([FFMPEG, '-hide_banner', '-i', path, '-map', '0:v:0',
                        '-an', '-sn', '-dn', '-f', 'null', '-'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    e = p.stderr.decode('utf-8', 'replace')
    fr = re.findall(r'frame=\s*(\d+)', e)
    du = re.findall(r'Duration:\s*(\d+):(\d+):(\d+\.\d+)', e)
    secs = (int(du[0][0]) * 3600 + int(du[0][1]) * 60 + float(du[0][2])) if du else 0.0
    return p.returncode, int(fr[-1]) if fr else 0, secs, e[-400:]


def append_free(path, text):
    payload = text.encode('ascii')
    with open(path, 'ab') as f:
        f.write(struct.pack('>I', len(payload) + 8) + b'free' + payload)


def make_video(name, seconds, codec='hevc', extra_meta=None, faststart=True):
    path = os.path.join(SAMPLES, name)
    vcodec = ['-c:v', 'libx265', '-tag:v', 'hvc1'] if codec == 'hevc' else ['-c:v', 'libx264']
    args = ['-y', '-f', 'lavfi', '-i',
            'testsrc2=size=640x360:rate=30:duration=%.2f' % seconds,
            '-pix_fmt', 'yuv420p'] + vcodec + ['-preset', 'ultrafast', '-crf', '28']
    if extra_meta:
        args += ['-movflags', 'use_metadata_tags']
        for k, v in extra_meta.items():
            args += ['-metadata', '%s=%s' % (k, v)]
    elif faststart:
        args += ['-movflags', '+faststart']
    args += [path]
    run(args)
    return path


def make_jpg(name, shot):
    path = os.path.join(SAMPLES, name)
    run(['-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=0.2',
         '-frames:v', '1', '-q:v', '2', '-update', '1', path])
    with open(path, 'rb') as f:
        jpg = f.read()
    exif = me.build_exif_app1(make='DJI', model='OsmoPocket4', shot_at=shot,
                              image_width=640, image_height=360)
    jpg = me.inject_into_jpeg(jpg, [exif])
    with open(path, 'wb') as f:
        f.write(jpg)
    return path


def make_samples():
    os.makedirs(SAMPLES, exist_ok=True)
    shot = dt.datetime(2026, 11, 6, 10, 30, 0)
    made = {}

    # A) Pocket 4 主场景：3 秒 HEVC MP4，无任何 TPLG 标记，配一张原始 JPG
    a = make_video('DJI_20261106103000_0001_D.MP4', 3.0, 'hevc')
    make_jpg('DJI_20261106103000_0001_D.JPG', shot)
    made['pocket4'] = a

    # B) Pocket 4 变体：3 秒 HEVC，尾部写固件签名（无 TPLG）
    b = make_video('DJI_20261106103100_0002_D.MP4', 3.0, 'hevc')
    append_free(b, 'DJI OsmoPocket4 firmware v01.02.0300 dbgi')
    made['pocket4_sig'] = b

    # C) Pocket 4P 场景：带 TPLG_VIDEO_LIVEPHOTO 模板串
    c = make_video('DJI_20261106103200_0003_D.MP4', 3.0, 'hevc')
    append_free(c, 'TPLG_VIDEO_LIVEPHOTO_4K_2997 dbgi')
    made['pocket4p'] = c

    # D) 普通长视频（归入「视频」）
    d = make_video('DJI_20261106103300_0004_D.MP4', 10.0, 'hevc')
    append_free(d, 'TPLG_VIDEO_4K_2997 dbgi')
    made['longvideo'] = d

    # E) 3 秒 H.264（非 HEVC 源，验证封面编码回退）
    e = make_video('DJI_20261106103400_0005_D.MP4', 3.0, 'h264')
    made['h264'] = e

    # F) moov 在文件尾部（非 faststart）的 3 秒实况
    f = make_video('DJI_20261106103500_0006_D.MP4', 3.0, 'hevc', faststart=False)
    made['moov_tail'] = f

    # G) QuickTime mdta 设备元数据
    g = make_video('DJI_20261106103600_0007_D.MP4', 3.0, 'hevc',
                   extra_meta={'com.apple.quicktime.make': 'DJI',
                               'com.apple.quicktime.model': 'OsmoPocket4',
                               'com.apple.quicktime.creationdate': '2026-11-06T10:36:00+08:00'})
    made['qtmeta'] = g
    return made


def check_scan():
    cfg = {'min_live_duration': 0.30, 'max_live_duration': 5.00,
           'duration_rule': True, 'recursive': False,
           'default_model': 'DJI Osmo Pocket 4'}
    assets = scanner.scan(SAMPLES, cfg)
    by_key = {}
    for a in assets:
        key = a.filename.split('_')[2]
        by_key.setdefault(key, []).append(a)
    print('\n--- 扫描结果 ---')
    for a in sorted(assets, key=lambda x: x.filename):
        print('  %-34s %-4s dur=%5.2fs  model=%-22s time=%s' % (
            a.filename, a.kind_label, a.duration, a.model, a.shot_at))
        print('      依据: %s' % a.reason)

    vids = [a for a in assets if a.kind != 'photo']
    assert len(vids) == 7, '应有 7 个视频，实际 %d' % len(vids)
    live = [a for a in vids if a.kind == 'live']
    assert len(live) == 6, '应有 6 个实况（7 个视频中除长视频），实际 %d' % len(live)
    longs = [a for a in vids if a.kind == 'video']
    assert len(longs) == 1 and '0004' in longs[0].filename, '长视频应判为普通视频'
    print('\n[OK] 分类正确：6 实况 / 1 普通视频（含无 TPLG 的 Pocket 4 3 秒 MP4）')

    # 设备型号与时间
    p4 = [a for a in vids if '0001' in a.filename][0]
    assert p4.model == 'DJI Osmo Pocket 4', 'Pocket 4 型号应来自配套 JPG EXIF：%s' % p4.model
    assert p4.shot_at == dt.datetime(2026, 11, 6, 10, 30, 0), '拍摄时间错误：%s' % p4.shot_at
    print('[OK] 拍摄信息：%s %s / %s（来源 %s / %s）' % (
        p4.make, p4.model, p4.shot_at, p4.model_source, p4.time_source))
    return assets


def check_exports(assets):
    cfg = exporters.Exporter(ffmpeg_path=FFMPEG, log=lambda s: print('  ' + s))
    os.makedirs(OUTPUT, exist_ok=True)
    targets = [a for a in assets if a.kind == 'live' and
               ('0001' in a.filename or '0002' in a.filename or
                '0005' in a.filename or '0007' in a.filename)]
    print('\n--- 导出测试（%d 个样本） ---' % len(targets))
    for a in targets:
        h, m = cfg.export_apple(a, os.path.join(OUTPUT, 'live'))
        j = cfg.export_motion(a, os.path.join(OUTPUT, 'live'))
        assert os.path.getsize(h) > 4096 and os.path.getsize(m) > a.size_bytes * 0.5
        assert os.path.getsize(j) > a.size_bytes
        _verify_heic(h, a)
        _verify_mov(m, a)
        _verify_motion_jpg(j, a)
        _verify_pair(h, m)
    print('\n[OK] 全部导出产物通过结构校验')


def _verify_pair(heic_path, mov_path):
    """Apple Live Photo 的成对依据：HEIC MakerNote 与 MOV mdta 的 ContentIdentifier 必须一致。"""
    uuid_re = re.compile(rb'[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}')
    with open(heic_path, 'rb') as f:
        hb = f.read()
    with open(mov_path, 'rb') as f:
        mb = f.read()
    hu = set(uuid_re.findall(hb))
    mu = set(uuid_re.findall(mb))
    assert hu, 'HEIC MakerNote 中缺少 ContentIdentifier'
    assert hu & mu, 'HEIC 与 MOV 的 ContentIdentifier 不一致'
    cid = next(iter(hu & mu)).decode()
    # EXIF 字段解析
    idx = hb.find(b'Exif\x00\x00MM')
    assert idx >= 0, 'HEIC 缺少 Exif APP1 段'
    exif = {}
    mp4._parse_tiff(hb[idx + 6:], exif)
    assert exif.get('make', '').upper() == 'DJI', 'EXIF Make 错误: %s' % exif.get('make')
    assert 'Osmo Pocket 4' in exif.get('model', ''), 'EXIF Model 错误: %s' % exif.get('model')
    assert exif.get('datetime_original', '').startswith('2026:11:06'), \
        'EXIF DateTimeOriginal 错误: %s' % exif.get('datetime_original')
    assert 'MakerNote' in hb.decode('latin-1', 'replace') or b'Apple iOS\x00' in hb, \
        'HEIC 缺少 Apple MakerNote'
    assert b'Apple iOS\x00' in hb, 'HEIC 缺少 Apple MakerNote 头'
    print('     配对 OK: ContentIdentifier %s 一致；EXIF Make/Model/DateTimeOriginal/MakerNote 正确' % cid[:8])


def _verify_heic(path, a):
    with open(path, 'rb') as f:
        b = f.read()
    assert b[4:8] == b'ftyp' and b[8:12] == b'heic', 'HEIC ftyp 品牌错误'
    for box in (b'meta', b'iloc', b'iprp', b'hvcC', b'mdat', b'Exif'):
        assert box in b, 'HEIC 缺少 %s' % box
    assert b'DJI Osmo Pocket 4' in b or a.model.encode('utf-8') in b, 'HEIC 缺少设备型号'
    assert b'2026:11:06' in b, 'HEIC 缺少拍摄时间'
    assert b'Pocket Motion' in b, 'HEIC 缺少 EXIF Software'
    print('     HEIC OK: %.1f KB, EXIF 含设备与时间' % (len(b) / 1024))


def _verify_mov(path, a):
    with open(path, 'rb') as f:
        b = f.read()
    assert b[4:8] == b'ftyp' and b[8:12] == b'qt  ', 'MOV ftyp 品牌错误'
    for token in (b'moov', b'trak', b'com.apple.quicktime.still-image-time',
                  b'com.apple.quicktime.content.identifier',
                  b'com.apple.quicktime.live-photo.auto',
                  b'com.apple.quicktime.model'):
        assert token in b, 'MOV 缺少 %s' % token
    assert a.model.encode('utf-8') in b, 'MOV 缺少真实设备型号'
    info = mp4.read_mp4_info(path)
    assert info['error'] is None, 'MOV 容器解析失败: %s' % info['error']
    assert info['track_count'] >= 2, 'MOV 应含视频轨 + still-image-time 元数据轨'
    assert info['duration'] > 0.5, 'MOV 时长异常: %s' % info['duration']
    # 必须真的含完整可播放的多帧动态视频，而不是只有一张封面
    rc, frames, secs, tail = decode_frames(path)
    assert rc == 0 and frames > 1, 'MOV 动态视频不可播放或只有单帧: rc=%s 帧=%s %s' % (
        rc, frames, tail)
    assert abs(secs - info['duration']) < 0.6, 'MOV 实际时长与容器不符: %.2f / %.2f' % (
        secs, info['duration'])
    print('     MOV  OK: %.1f MB, %d 轨, %.2fs / %d 帧, 元数据齐全且完整可播放' % (
        len(b) / 1048576, info['track_count'], secs, frames))


def _verify_motion_jpg(path, a):
    with open(path, 'rb') as f:
        b = f.read()
    assert b[:2] == b'\xff\xd8', 'Motion Photo 不是 JPEG'
    assert b'Exif\x00\x00' in b[:4096], '缺少 EXIF APP1'
    assert b'http://ns.adobe.com/xap/1.0/\x00' in b[:8192], '缺少 XMP APP1'
    assert b'GCamera:MotionPhoto="1"' in b[:8192], '缺少 MotionPhoto 标记（新版）'
    assert b'GCamera:MotionPhotoPresentationTimestampUs=' in b[:8192], '缺少新版时间戳'
    # 早期格式 MicroVideo 字段：Google 相册 / 多数 Android 相册靠它识别动态照片，
    # 缺失时会把整个文件当成普通静态 JPG 显示。
    for token in (b'GCamera:MicroVideo="1"', b'GCamera:MicroVideoVersion="1"',
                  b'GCamera:MicroVideoOffset=', b'GCamera:MicroVideoPresentationTimestampUs='):
        assert token in b[:8192], '缺少兼容标记 %s' % token.decode()
    assert a.model.encode('utf-8') in b[:8192], '缺少设备型号'
    assert b'2026:11:06' in b[:8192], '缺少拍摄时间'
    m = re.search(rb'Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="(\d+)"', b[:8192])
    assert m, 'XMP 缺少视频长度'
    plen = int(m.group(1))
    mo = re.search(rb'GCamera:MicroVideoOffset="(\d+)"', b[:8192])
    assert mo and int(mo.group(1)) == plen, 'MicroVideoOffset 与 Item:Length / 视频段不一致'
    # JPEG EOI 之后应紧跟 MP4
    eoi = b.rfind(b'\xff\xd9', 0, len(b) - plen)
    assert eoi >= 0, '找不到 JPEG EOI'
    assert b[eoi + 2:eoi + 10][4:8] == b'ftyp', 'EOI 后不是 MP4 数据'
    assert len(b) - (eoi + 2) == plen, '视频长度与 XMP 声明不一致'
    # 按相册的方式（从文件尾按长度截取）真正解码一遍，确认动态视频存在
    seg = b[-plen:]
    assert seg[4:8] == b'ftyp', '按 MicroVideoOffset 截出的不是有效 MP4'
    tmp = path + '.vseg.tmp'
    with open(tmp, 'wb') as f:
        f.write(seg)
    try:
        rc, frames, secs, tail = decode_frames(tmp)
        assert rc == 0 and frames > 1, '内嵌动态视频不可播放或只有单帧: rc=%s 帧=%s %s' % (
            rc, frames, tail)
    finally:
        os.remove(tmp)
    print('     JPG  OK: %.1f MB, 双代 XMP 齐全, 内嵌 %.2fs / %d 帧完整可播放' % (
        len(b) / 1048576, secs, frames))


def check_faststart_injection():
    """faststart（moov 在 mdat 前）源直接注入时，chunk offset 必须重定位。"""
    src = os.path.join(SAMPLES, 'DJI_20261106103000_0001_D.MP4')
    with open(src, 'rb') as f:
        d = f.read()
    tops = list(mp4.children(d, 0, len(d)))
    moov_pos = next(a.pos for a in tops if a.type == 'moov')
    mdat_pos = next(a.pos for a in tops if a.type == 'mdat')
    assert moov_pos < mdat_pos, '样本应为 faststart（moov 在 mdat 之前）'
    out = os.path.join(OUTPUT, '_faststart_direct.mov')
    has = me.write_mov_live_photo_metadata(
        src, out, me.new_uuid(), 'DJI', 'DJI Osmo Pocket 4',
        dt.datetime(2026, 11, 6, 10, 30, 0), 1.5)
    assert has, '缺少 still-image-time 定时轨'
    rc, frames, secs, tail = decode_frames(out)
    assert rc == 0 and frames > 1, 'faststart 源直注入后视频帧失效: rc=%s 帧=%s %s' % (
        rc, frames, tail)
    print('[OK] faststart 源（moov 在 mdat 前）直接注入仍可解码 %d 帧 / %.2fs' % (
        frames, secs))
    os.remove(out)


def main():
    print('=' * 72)
    print('Pocket Motion 4 自检  ·  %s' % FFMPEG)
    print('=' * 72)
    make_samples()
    print('\n[OK] 生成 7 个模拟样本（含 Pocket 4 无标记 3s MP4 / 4P TPLG / 长视频 / H.264）')
    assets = check_scan()
    check_exports(assets)
    check_faststart_injection()
    print('\n' + '=' * 72)
    print('全部自检通过')
    print('=' * 72)


def run_packaged_selftest(base_dir):
    """供打包后的 exe 调用：在临时目录完整跑一遍，返回 (是否通过, 文本报告)。"""
    import contextlib
    import io
    import shutil
    import tempfile
    import traceback

    global SAMPLES, OUTPUT, FFMPEG
    tmp = tempfile.mkdtemp(prefix='pm4_selftest_')
    SAMPLES = os.path.join(tmp, 'DCIM', 'DJI_001')
    OUTPUT = os.path.join(tmp, 'out')
    # 单文件版：ffmpeg 压缩内嵌，首次调用时释放到本机缓存并返回其路径。
    FFMPEG = resources.ffmpeg_path()
    buf = io.StringIO()
    ok = True
    try:
        with contextlib.redirect_stdout(buf):
            print('Pocket Motion 4 打包版自检')
            print('ffmpeg: %s (exists=%s)' % (FFMPEG, os.path.isfile(FFMPEG)))
            make_samples()
            assets = check_scan()
            check_exports(assets)
            check_faststart_injection()
            print('RESULT: PASS')
    except Exception as e:  # noqa: BLE001
        ok = False
        buf.write('\nRESULT: FAIL\n%s\n%s' % (e, traceback.format_exc()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok, buf.getvalue()


if __name__ == '__main__':
    main()
