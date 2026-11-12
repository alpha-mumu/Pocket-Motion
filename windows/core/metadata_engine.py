# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""纯 Python 媒体元数据与容器封装引擎。

Modifications:
  * 以纯标准库实现 TIFF/EXIF APP1、Apple Live Photo MakerNote、
    ISO/IEC 23008-12 .HEIC 封装，以及 MOV mebx / tref->cdsc / mdta 注入；
  * 同时写入两代 Google Motion Photo XMP 字段（GCamera:MotionPhoto* 与
    GCamera:MicroVideo*），并校验 MicroVideoOffset 与尾部视频段长度一致；
  * 新增 _relocate_chunk_offsets()，在 moov 位置变化时同步修正 stco/co64；
  * 提供 build_xmp_app1 / inject_into_jpeg 等可独立调用的字节级接口。
"""
from __future__ import annotations

import datetime as _dt
import os
import struct
import uuid as _uuid

from . import mp4

QT_EPOCH_OFFSET = 2082844800


def be16(v):
    return struct.pack('>H', v & 0xFFFF)


def be32(v):
    return struct.pack('>I', v & 0xFFFFFFFF)


def be64(v):
    return struct.pack('>Q', v & 0xFFFFFFFFFFFFFFFF)


def bes32(v):
    return struct.pack('>i', v)


def wrap_atom(typ, payload):
    t = typ.encode('latin-1')[:4].ljust(4, b' ')
    return be32(len(payload) + 8) + t + payload


def wrap_full_box(typ, version, flags, payload):
    return wrap_atom(typ, be32(((version & 0xFF) << 24) | (flags & 0xFFFFFF)) + payload)


def ascii0(s):
    return s.encode('utf-8', 'replace') + b'\x00'


def rational(num, den):
    return be32(num) + be32(den)


# ---------------------------------------------------------------------------
# EXIF APP1
# ---------------------------------------------------------------------------

def _format_exif_date(dt):
    if dt is None:
        return None
    return '%04d:%02d:%02d %02d:%02d:%02d' % (
        dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _format_tz(dt):
    off = dt.utcoffset() if dt and dt.tzinfo else None
    if off is None:
        # 本地时间按系统时区
        off = _dt.datetime.now().astimezone().utcoffset()
    total = int(off.total_seconds() // 60)
    sign = '-' if total < 0 else '+'
    total = abs(total)
    return '%s%02d:%02d' % (sign, total // 60, total % 60)


def _format_iso8601(dt):
    if dt is None:
        return None
    return '%04d-%02d-%02dT%02d:%02d:%02d%s' % (
        dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, _format_tz(dt))


_RUN_TIME_BPLIST = bytes([
    98, 112, 108, 105, 115, 116, 48, 48, 212, 1, 2, 3, 4, 5, 6, 7, 8, 85,
    101, 112, 111, 99, 104, 85, 102, 108, 97, 103, 115, 89, 116, 105, 109,
    101, 115, 99, 97, 108, 101, 85, 118, 97, 108, 117, 101, 16, 0, 16, 1,
    17, 2, 88, 17, 3, 132, 8, 17, 23, 29, 39, 45, 47, 49, 52, 0, 0, 0, 0,
    0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 55,
])


def build_apple_maker_note(live_uuid):
    num_entries = 6
    data_start = 14 + 2 + num_entries * 12 + 4  # 92
    entries = bytearray()
    blobs = bytearray()

    def add(tag, typ, count, raw):
        entries.extend(be16(tag) + be16(typ) + be32(count))
        if len(raw) <= 4:
            entries.extend(raw.ljust(4, b'\x00'))
        else:
            entries.extend(be32(data_start + len(blobs)))
            blobs.extend(raw)
            if len(blobs) % 2:
                blobs.append(0)

    accel = b''.join(bes32(v) for v in (0, 1, -1, 1, 0, 1))
    uuid_bytes = live_uuid.encode('ascii') + b'\x00'
    add(0x0001, 9, 1, bes32(14))
    add(0x0003, 7, len(_RUN_TIME_BPLIST), _RUN_TIME_BPLIST)
    add(0x0008, 10, 3, accel)
    add(0x0011, 2, len(uuid_bytes), uuid_bytes)
    add(0x0014, 9, 1, bes32(10))
    add(0x0017, 10, 1, bes32(900) + bes32(1))

    out = bytearray(b'Apple iOS\x00' + bytes([0x00, 0x01, 0x4D, 0x4D]))
    out.extend(be16(num_entries))
    out.extend(entries)
    out.extend(be32(0))
    out.extend(blobs)
    return bytes(out)


def build_exif_app1(make, model, shot_at=None, live_photo_uuid=None,
                    image_width=None, image_height=None, software='Pocket Motion 4'):
    shot = _format_exif_date(shot_at)
    has_date = shot is not None
    num_ifd0 = 10 if has_date else 9
    ifd0_offset = 8
    data0_offset = ifd0_offset + 2 + num_ifd0 * 12 + 4

    entries0 = bytearray()
    blobs0 = bytearray()

    def add_entry(entries, blobs, base, tag, typ, count, raw):
        entries.extend(be16(tag) + be16(typ) + be32(count))
        if len(raw) <= 4:
            entries.extend(raw.ljust(4, b'\x00'))
        else:
            entries.extend(be32(base + len(blobs)))
            blobs.extend(raw)
            if len(blobs) % 2:
                blobs.append(0)

    make_b, model_b, soft_b = ascii0(make), ascii0(model), ascii0(software)
    add_entry(entries0, blobs0, data0_offset, 0x010F, 2, len(make_b), make_b)
    add_entry(entries0, blobs0, data0_offset, 0x0110, 2, len(model_b), model_b)
    add_entry(entries0, blobs0, data0_offset, 0x0112, 3, 1, be16(1))
    add_entry(entries0, blobs0, data0_offset, 0x011A, 5, 1, rational(72, 1))
    add_entry(entries0, blobs0, data0_offset, 0x011B, 5, 1, rational(72, 1))
    add_entry(entries0, blobs0, data0_offset, 0x0128, 3, 1, be16(2))
    add_entry(entries0, blobs0, data0_offset, 0x0131, 2, len(soft_b), soft_b)
    if has_date:
        dt_b = ascii0(shot)
        add_entry(entries0, blobs0, data0_offset, 0x0132, 2, len(dt_b), dt_b)
    add_entry(entries0, blobs0, data0_offset, 0x0213, 3, 1, be16(1))

    placeholder = len(entries0)
    add_entry(entries0, blobs0, data0_offset, 0x8769, 4, 1, be32(0))
    exif_ifd_offset = data0_offset + len(blobs0)
    entries0[placeholder + 8:placeholder + 12] = be32(exif_ifd_offset)

    has_uuid = bool(live_photo_uuid)
    has_dims = bool(image_width and image_height and image_width > 0 and image_height > 0)
    num_exif = 3 + (5 if has_date else 0) + (1 if has_uuid else 0) + (2 if has_dims else 0)
    data_exif_offset = exif_ifd_offset + 2 + num_exif * 12 + 4

    entriesx = bytearray()
    blobsx = bytearray()
    add_entry(entriesx, blobsx, data_exif_offset, 0x9000, 7, 4, b'0232')
    if has_date:
        tz = _format_tz(shot_at)
        for tag in (0x9003, 0x9004):
            b = ascii0(shot)
            add_entry(entriesx, blobsx, data_exif_offset, tag, 2, len(b), b)
        for tag in (0x9010, 0x9011, 0x9012):
            b = ascii0(tz)
            add_entry(entriesx, blobsx, data_exif_offset, tag, 2, len(b), b)
    add_entry(entriesx, blobsx, data_exif_offset, 0x9101, 7, 4, bytes([1, 2, 3, 0]))
    if has_uuid:
        mn = build_apple_maker_note(live_photo_uuid)
        add_entry(entriesx, blobsx, data_exif_offset, 0x927C, 7, len(mn), mn)
    add_entry(entriesx, blobsx, data_exif_offset, 0xA001, 3, 1, be16(1))
    if has_dims:
        add_entry(entriesx, blobsx, data_exif_offset, 0xA002, 4, 1, be32(image_width))
        add_entry(entriesx, blobsx, data_exif_offset, 0xA003, 4, 1, be32(image_height))

    tiff = bytearray(b'MM\x00\x2a' + be32(8))
    tiff.extend(be16(num_ifd0))
    tiff.extend(entries0)
    tiff.extend(be32(0))
    tiff.extend(blobs0)
    tiff.extend(be16(num_exif))
    tiff.extend(entriesx)
    tiff.extend(be32(0))
    tiff.extend(blobsx)

    payload_len = 2 + 6 + len(tiff)
    return b'\xff\xe1' + be16(payload_len) + b'Exif\x00\x00' + bytes(tiff)


def build_xmp_app1(mp4_length, presentation_us=1500000):
    """构造 Google Motion Photo 的 XMP APP1。

    同时写入两代标记，缺一不可：
    - 早期格式 MicroVideo*：Google 相册、多数 Android 厂商相册与第三方查看器
      靠 GCamera:MicroVideo / MicroVideoOffset 定位并播放尾部视频；
    - 新版 MotionPhoto* / Container:Directory：Pixel 4 之后的新格式。
    只写新版时，很多相册会把文件当成普通静态 JPG 显示。
    MicroVideoOffset 与 Item:Length 都是「文件末尾视频段的字节数」。
    """
    xml = ('''<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Pocket Motion 4">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"
    xmlns:Container="http://ns.google.com/photos/1.0/container/"
    xmlns:Item="http://ns.google.com/photos/1.0/container/item/"
    GCamera:MotionPhoto="1"
    GCamera:MotionPhotoVersion="1"
    GCamera:MotionPhotoPresentationTimestampUs="%(ts)d"
    GCamera:MicroVideo="1"
    GCamera:MicroVideoVersion="1"
    GCamera:MicroVideoOffset="%(len)d"
    GCamera:MicroVideoPresentationTimestampUs="%(ts)d">
   <Container:Directory>
    <rdf:Seq>
     <rdf:li rdf:parseType="Resource">
      <Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" Item:Length="0" Item:Padding="0"/>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="%(len)d"/>
     </rdf:li>
    </rdf:Seq>
   </Container:Directory>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>''' % {'ts': presentation_us, 'len': mp4_length})
    content = b'http://ns.adobe.com/xap/1.0/\x00' + xml.encode('utf-8')
    return b'\xff\xe1' + be16(len(content) + 2) + content


def inject_into_jpeg(jpeg, app1_segments):
    if len(jpeg) < 4 or jpeg[0] != 0xFF or jpeg[1] != 0xD8:
        return jpeg
    pos = 2
    while pos + 4 <= len(jpeg) and jpeg[pos] == 0xFF:
        marker = jpeg[pos + 1]
        if marker in (0xDA, 0xD9):
            break
        seg_len = (jpeg[pos + 2] << 8) | jpeg[pos + 3]
        if seg_len < 2 or pos + 2 + seg_len > len(jpeg):
            break
        if marker in (0xE0, 0xE1):
            pos += 2 + seg_len
        else:
            break
    out = bytearray(b'\xff\xd8')
    for seg in app1_segments:
        out.extend(seg)
    out.extend(jpeg[pos:])
    return bytes(out)


def jpeg_dimensions(jpg):
    if len(jpg) < 4 or jpg[0] != 0xFF or jpg[1] != 0xD8:
        return None
    pos = 2
    while pos + 9 <= len(jpg):
        if jpg[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(jpg) and jpg[pos] == 0xFF:
            pos += 1
        if pos >= len(jpg):
            break
        marker = jpg[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xDA or pos + 2 > len(jpg):
            break
        seg_len = (jpg[pos] << 8) | jpg[pos + 1]
        if seg_len < 2 or pos + seg_len > len(jpg):
            break
        if marker in (0xC0, 0xC1, 0xC2) and seg_len >= 7:
            return ((jpg[pos + 5] << 8) | jpg[pos + 6], (jpg[pos + 3] << 8) | jpg[pos + 4])
        pos += seg_len
    return None


# ---------------------------------------------------------------------------
# HEIC 封装
# ---------------------------------------------------------------------------

def _find_atoms(buf, start, end):
    return list(mp4.children(buf, start, end))


def sanitize_hvcc_for_heic(hvcc):
    if len(hvcc) < 31:
        return hvcc
    payload = bytearray(hvcc[8:])
    profile_idc = payload[1] & 0x1F
    if profile_idc not in (1, 2, 3):
        payload[1] = (payload[1] & 0xE0) | 0x01
        payload[2] |= 0x60
    num_arrays = payload[22]
    p = 23
    kept = []
    for _ in range(num_arrays):
        if p + 3 > len(payload):
            break
        nal_type = payload[p] & 0x3F
        num_nalus = (payload[p + 1] << 8) | payload[p + 2]
        arr_start = p
        p += 3
        valid = True
        for _ in range(num_nalus):
            if p + 2 > len(payload):
                valid = False
                break
            nlen = (payload[p] << 8) | payload[p + 1]
            p += 2 + nlen
            if p > len(payload):
                valid = False
                break
        if not valid:
            break
        if nal_type in (32, 33, 34):
            arr = bytearray(payload[arr_start:p])
            arr[0] = 0x80 | nal_type
            kept.append(bytes(arr))
    if not kept:
        return hvcc
    out = bytearray(payload[:22])
    out.append(len(kept))
    for arr in kept:
        out.extend(arr)
    return wrap_atom('hvcC', bytes(out))


def strip_non_slice_nals(sample):
    out = bytearray()
    p = 0
    while p + 4 <= len(sample):
        nlen = mp4.u32(sample, p)
        if nlen <= 0 or p + 4 + nlen > len(sample):
            break
        nal_type = (sample[p + 4] >> 1) & 0x3F
        if nal_type <= 31:
            out.extend(sample[p:p + 4 + nlen])
        p += 4 + nlen
    return bytes(out) if out else sample


def extract_hevc_frame_from_mp4(b):
    """返回 (width, height, hvcc_box, hevc_sample)。"""
    best = None

    def walk_trak(start, end):
        t = {'w': 0, 'h': 0, 'hvcc': None, 'offset': 0, 'size': 0}

        def walk(s, e):
            for a in _find_atoms(b, s, e):
                bs, be = a.body, a.end
                if a.type in ('mdia', 'minf', 'stbl'):
                    walk(bs, be)
                elif a.type == 'stsd' and bs + 8 + 86 <= be:
                    # stsd body = FullBox(4) + entry_count(4) + VisualSampleEntry
                    ep = bs + 8
                    if ep + 36 <= be:
                        esz = mp4.u32(b, ep)
                        t['w'] = mp4.u16(b, ep + 32)
                        t['h'] = mp4.u16(b, ep + 34)
                        e_end = min(ep + esz, be) if esz else be
                        sp = ep + 86
                        while sp + 8 <= e_end:
                            ssz = mp4.u32(b, sp)
                            styp = mp4.fourcc(b, sp + 4)
                            if ssz < 8 or sp + ssz > e_end:
                                break
                            if styp == 'hvcC':
                                t['hvcc'] = bytes(b[sp:sp + ssz])
                                break
                            sp += ssz
                elif a.type == 'stsz' and be - bs >= 8:
                    # stsz body = FullBox(4) + sample_size(4) + sample_count(4) [+ entries]
                    sample_size = mp4.u32(b, bs + 4)
                    t['size'] = sample_size
                    if sample_size == 0 and be - bs >= 16:
                        t['size'] = mp4.u32(b, bs + 12)
                elif a.type == 'stco' and be - bs >= 12:
                    t['offset'] = mp4.u32(b, bs + 8)
                elif a.type == 'co64' and be - bs >= 16:
                    t['offset'] = mp4.u64(b, bs + 8)
        walk(start, end)
        if t['hvcc'] and t['size'] > 0 and t['offset'] + t['size'] <= len(b):
            return t
        return None

    def walk_root(s, e):
        nonlocal best
        for a in _find_atoms(b, s, e):
            if a.type == 'moov':
                walk_root(a.body, a.end)
            elif a.type == 'trak' and best is None:
                r = walk_trak(a.body, a.end)
                if r:
                    best = r
    walk_root(0, len(b))
    if not best or best['size'] <= 1024 or best['offset'] + best['size'] > len(b):
        raise ValueError('无法从临时 HEVC 帧文件中提取有效的 hvcC 或图像码流')
    return best['w'], best['h'], best['hvcc'], bytes(b[best['offset']:best['offset'] + best['size']])


def build_heic_from_hevc_mp4(hevc_mp4_path, out_heic_path, make, model,
                             live_uuid, shot_at=None):
    with open(hevc_mp4_path, 'rb') as f:
        data = f.read()
    width, height, raw_hvcc, raw_sample = extract_hevc_frame_from_mp4(data)
    hvcc = sanitize_hvcc_for_heic(raw_hvcc)
    sample = strip_non_slice_nals(raw_sample)

    app1 = build_exif_app1(make=make, model=model, shot_at=shot_at,
                           live_photo_uuid=live_uuid,
                           image_width=width, image_height=height)
    exif_payload = app1[4:]
    exif_item = be32(6) + exif_payload

    ftyp = wrap_atom('ftyp', b'heic' + be32(0) + b'mif1' + b'MiPr' + b'miaf' + b'MiHB' + b'heic')
    hdlr = wrap_full_box('hdlr', 0, 0,
                         be32(0) + b'pict' + be32(0) + be32(0) + be32(0) + b'\x00')
    dinf = wrap_atom('dinf', be32(0x1C) + b'dref' + be32(0) + be32(1) +
                     be32(0x0C) + b'url ' + be32(1))
    pitm = wrap_full_box('pitm', 0, 0, be16(1))
    infe1 = wrap_full_box('infe', 2, 0, be16(1) + be16(0) + b'hvc1' + b'\x00')
    infe2 = wrap_full_box('infe', 2, 1, be16(2) + be16(0) + b'Exif' + b'\x00')
    iinf = wrap_full_box('iinf', 0, 0, be16(2) + infe1 + infe2)
    cdsc = wrap_atom('cdsc', be16(2) + be16(1) + be16(1))
    iref = wrap_full_box('iref', 0, 0, cdsc)
    colr = wrap_atom('colr', b'nclx' + be16(1) + be16(1) + be16(6) + b'\x80')
    ispe = wrap_full_box('ispe', 0, 0, be32(width) + be32(height))
    irot = wrap_atom('irot', b'\x00')
    pixi = wrap_full_box('pixi', 0, 0, bytes([3, 8, 8, 8]))
    ipco = wrap_atom('ipco', colr + hvcc + ispe + irot + pixi)
    ipma = wrap_full_box('ipma', 0, 0, bytes([
        0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x05,
        0x81, 0x82, 0x03, 0x84, 0x05,
    ]))
    iprp = wrap_atom('iprp', ipco + ipma)

    iloc_size = 44
    meta_total = 12 + len(hdlr) + len(dinf) + len(pitm) + len(iinf) + len(iref) + len(iprp) + iloc_size
    mdat_header_offset = len(ftyp) + meta_total
    exif_offset = mdat_header_offset + 8
    hevc_offset = exif_offset + len(exif_item)

    iloc_payload = bytearray(32)
    iloc_payload[0] = 0x44
    iloc_payload[1] = 0x00
    struct.pack_into('>H', iloc_payload, 2, 2)
    struct.pack_into('>H', iloc_payload, 4, 1)
    struct.pack_into('>H', iloc_payload, 6, 0)
    struct.pack_into('>H', iloc_payload, 8, 1)
    struct.pack_into('>I', iloc_payload, 10, hevc_offset)
    struct.pack_into('>I', iloc_payload, 14, len(sample))
    struct.pack_into('>H', iloc_payload, 18, 2)
    struct.pack_into('>H', iloc_payload, 20, 0)
    struct.pack_into('>H', iloc_payload, 22, 1)
    struct.pack_into('>I', iloc_payload, 24, exif_offset)
    struct.pack_into('>I', iloc_payload, 28, len(exif_item))
    iloc = wrap_full_box('iloc', 0, 0, bytes(iloc_payload))
    assert len(iloc) == iloc_size, (len(iloc), iloc_size)

    meta = wrap_full_box('meta', 0, 0,
                         hdlr + dinf + pitm + iinf + iref + iprp + iloc)
    mdat = wrap_atom('mdat', exif_item + sample)
    with open(out_heic_path, 'wb') as f:
        f.write(ftyp + meta + mdat)


# ---------------------------------------------------------------------------
# MOV Live Photo 元数据
# ---------------------------------------------------------------------------

def _patch_moov_timestamps(buf, start, end, qt_sec):
    pos = start
    for a in list(mp4.children(buf, start, end)):
        bs, be = a.body, a.end
        if a.type in ('mvhd', 'tkhd', 'mdhd') and be - bs >= 12:
            ver = buf[bs]
            if ver == 1 and be - bs >= 28:
                struct.pack_into('>Q', buf, bs + 4, qt_sec)
                struct.pack_into('>Q', buf, bs + 12, qt_sec)
            elif ver == 0:
                struct.pack_into('>I', buf, bs + 4, qt_sec & 0xFFFFFFFF)
                struct.pack_into('>I', buf, bs + 8, qt_sec & 0xFFFFFFFF)
        elif a.type in ('trak', 'mdia'):
            _patch_moov_timestamps(buf, bs, be, qt_sec)


def _relocate_chunk_offsets(buf, start, end, shift, boundary):
    """把 moov 内 stco / co64 的绝对 chunk 偏移整体平移。

    write_mov_live_photo_metadata 会把 moov 从原位置摘出、追加到文件末尾。
    若原文件是 faststart（moov 在 mdat 之前，DJI 相机 MP4 常见），mdat 会整体
    前移 moov.size；此时若不同步修正 stco/co64，所有帧都指向错误位置，
    播放器只能显示封面帧甚至完全无法播放 —— 表现为「导出的是静态图片」。
    """
    if not shift:
        return
    for a in list(mp4.children(buf, start, end)):
        bs, be = a.body, a.end
        if a.type in ('trak', 'mdia', 'minf', 'stbl'):
            _relocate_chunk_offsets(buf, bs, be, shift, boundary)
        elif a.type in ('stco', 'co64') and be - bs >= 8:
            count = mp4.u32(buf, bs + 4)
            width = 4 if a.type == 'stco' else 8
            base = bs + 8
            for i in range(count):
                p = base + i * width
                if p + width > be:
                    break
                if a.type == 'stco':
                    old = mp4.u32(buf, p)
                    new = max(0, old + shift) if old >= boundary else old
                    struct.pack_into('>I', buf, p, new & 0xFFFFFFFF)
                else:
                    old = mp4.u64(buf, p)
                    new = max(0, old + shift) if old >= boundary else old
                    struct.pack_into('>Q', buf, p, new)


def _parse_trak_timing(buf, start, end):
    t = {'track_id': 0, 'is_video': False, 'timescale': 0,
         'duration': 0, 'frame_delta': 0}

    def walk(s, e):
        for a in mp4.children(buf, s, e):
            bs, be = a.body, a.end
            if a.type == 'tkhd' and be - bs >= 20:
                ver = buf[bs]
                t['track_id'] = mp4.u32(buf, bs + (20 if ver == 1 else 12))
            elif a.type == 'mdhd' and be - bs >= 20:
                ver = buf[bs]
                if ver == 1 and be - bs >= 32:
                    t['timescale'] = mp4.u32(buf, bs + 20)
                    t['duration'] = mp4.u64(buf, bs + 24)
                else:
                    t['timescale'] = mp4.u32(buf, bs + 12)
                    t['duration'] = mp4.u32(buf, bs + 16)
            elif a.type == 'hdlr' and be - bs >= 12:
                if mp4.fourcc(buf, bs + 8) == 'vide':
                    t['is_video'] = True
            elif a.type == 'stts' and be - bs >= 16:
                count = mp4.u32(buf, bs + 4)
                if count >= 1 and bs + 16 <= be:
                    t['frame_delta'] = mp4.u32(buf, bs + 12)
            elif a.type in ('mdia', 'minf', 'stbl'):
                walk(bs, be)
    walk(start, end)
    return t


def _inspect_moov(moov):
    out = {'movie_timescale': 600, 'movie_duration': 2100,
           'new_meta_track_id': 1, 'video': None}
    max_id = 0
    for a in mp4.children(moov, 0, len(moov)):
        bs, be = a.body, a.end
        if a.type == 'mvhd' and be - bs >= 24:
            ver = moov[bs]
            if ver == 1 and be - bs >= 32:
                out['movie_timescale'] = mp4.u32(moov, bs + 20)
                out['movie_duration'] = mp4.u64(moov, bs + 24)
            else:
                out['movie_timescale'] = mp4.u32(moov, bs + 12)
                out['movie_duration'] = mp4.u32(moov, bs + 16)
        elif a.type == 'trak':
            t = _parse_trak_timing(moov, bs, be)
            max_id = max(max_id, t['track_id'])
            if t['is_video'] and out['video'] is None and t['timescale'] > 0:
                out['video'] = {
                    'track_id': t['track_id'],
                    'timescale': t['timescale'],
                    'duration': t['duration'],
                    'frame_delta': t['frame_delta'] or 1,
                }
    out['movie_timescale'] = out['movie_timescale'] or 600
    out['movie_duration'] = out['movie_duration'] or 2100
    out['new_meta_track_id'] = max_id + 1
    return out


def _bump_mvhd_next_track_id(mvhd_atom, next_id):
    if len(mvhd_atom) < 16:
        return
    ver = mvhd_atom[8]
    off = 8 + (104 if ver == 1 else 92)
    if off + 4 <= len(mvhd_atom):
        struct.pack_into('>I', mvhd_atom, off, next_id)


def _build_still_image_time_trak(meta_track_id, video_track_id, movie_timescale,
                                 movie_duration, video_timescale, video_duration,
                                 frame_delta, cover_seconds, chunk_offset, qt_sec):
    one_frame = frame_delta if frame_delta > 0 else max(1, video_timescale // 30)
    cover_ticks = int(round(cover_seconds * video_timescale))
    cover_ticks = (cover_ticks // one_frame) * one_frame
    if cover_ticks <= 0 or cover_ticks + one_frame >= video_duration:
        cover_ticks = video_duration // 2
        cover_ticks = (cover_ticks // one_frame) * one_frame
    if cover_ticks < one_frame:
        cover_ticks = one_frame
    tail = video_duration - cover_ticks - one_frame if video_duration > cover_ticks + one_frame else one_frame
    total = cover_ticks + one_frame + tail

    tkhd_payload = bytearray(84)
    struct.pack_into('>I', tkhd_payload, 0, 0x0000000F)
    struct.pack_into('>I', tkhd_payload, 4, qt_sec & 0xFFFFFFFF)
    struct.pack_into('>I', tkhd_payload, 8, qt_sec & 0xFFFFFFFF)
    struct.pack_into('>I', tkhd_payload, 12, meta_track_id)
    struct.pack_into('>I', tkhd_payload, 20, movie_duration)
    struct.pack_into('>I', tkhd_payload, 40, 0x00010000)
    struct.pack_into('>I', tkhd_payload, 56, 0x00010000)
    struct.pack_into('>I', tkhd_payload, 72, 0x40000000)
    tkhd = wrap_atom('tkhd', bytes(tkhd_payload))
    tref = wrap_atom('tref', wrap_atom('cdsc', be32(video_track_id)))

    elst = bytearray(20)
    struct.pack_into('>I', elst, 4, 1)
    struct.pack_into('>I', elst, 8, movie_duration)
    struct.pack_into('>I', elst, 16, 0x00010000)
    edts = wrap_atom('edts', wrap_atom('elst', bytes(elst)))

    mdhd = bytearray(24)
    struct.pack_into('>I', mdhd, 4, qt_sec & 0xFFFFFFFF)
    struct.pack_into('>I', mdhd, 8, qt_sec & 0xFFFFFFFF)
    struct.pack_into('>I', mdhd, 12, video_timescale)
    struct.pack_into('>I', mdhd, 16, total)
    struct.pack_into('>H', mdhd, 20, 0x55C4)
    mdhd_atom = wrap_atom('mdhd', bytes(mdhd))

    mdia_hdlr = wrap_atom('hdlr', be32(0) + b'mhlr' + b'meta' + b'appl' +
                          be32(0) + be32(0) + bytes([0x19]) + b'Core Media Metadata Track')
    gmhd = wrap_atom('gmhd', bytes([
        0x00, 0x00, 0x00, 0x18, 0x67, 0x6D, 0x69, 0x6E,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x40, 0x80, 0x00,
        0x80, 0x00, 0x80, 0x00, 0x00, 0x00,
    ]))
    minf_hdlr = wrap_atom('hdlr', be32(0) + b'dhlr' + b'alis' + b'appl' +
                          be32(0) + be32(0) + bytes([0x17]) + b'Core Media Data Handler')
    dinf = wrap_atom('dinf', bytes([
        0x00, 0x00, 0x00, 0x1C, 0x64, 0x72, 0x65, 0x66,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
        0x00, 0x00, 0x00, 0x0C, 0x61, 0x6C, 0x69, 0x73,
        0x00, 0x00, 0x00, 0x01,
    ]))

    stsd = bytes([
        0x00, 0x00, 0x00, 0x70, 0x73, 0x74, 0x73, 0x64,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
        0x00, 0x00, 0x00, 0x60, 0x6D, 0x65, 0x62, 0x78,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
        0x00, 0x00, 0x00, 0x50, 0x6B, 0x65, 0x79, 0x73,
        0x00, 0x00, 0x00, 0x48, 0x00, 0x00, 0x00, 0x01,
        0x00, 0x00, 0x00, 0x30, 0x6B, 0x65, 0x79, 0x64,
        0x6D, 0x64, 0x74, 0x61,
    ]) + b'com.apple.quicktime.still-image-time' + bytes([
        0x00, 0x00, 0x00, 0x10, 0x64, 0x74, 0x79, 0x70,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x41,
    ])

    stts_payload = bytearray(32)
    struct.pack_into('>I', stts_payload, 4, 3)
    struct.pack_into('>I', stts_payload, 8, 1)
    struct.pack_into('>I', stts_payload, 12, cover_ticks)
    struct.pack_into('>I', stts_payload, 16, 1)
    struct.pack_into('>I', stts_payload, 20, one_frame)
    struct.pack_into('>I', stts_payload, 24, 1)
    struct.pack_into('>I', stts_payload, 28, tail)
    stts = wrap_atom('stts', bytes(stts_payload))

    stsc = bytearray(20)
    struct.pack_into('>I', stsc, 4, 1)
    struct.pack_into('>I', stsc, 8, 1)
    struct.pack_into('>I', stsc, 12, 3)
    struct.pack_into('>I', stsc, 16, 1)
    stsc_atom = wrap_atom('stsc', bytes(stsc))

    stsz = bytearray(24)
    struct.pack_into('>I', stsz, 8, 3)
    struct.pack_into('>I', stsz, 12, 8)
    struct.pack_into('>I', stsz, 16, 9)
    struct.pack_into('>I', stsz, 20, 8)
    stsz_atom = wrap_atom('stsz', bytes(stsz))

    stco = bytearray(12)
    struct.pack_into('>I', stco, 4, 1)
    struct.pack_into('>I', stco, 8, chunk_offset)
    stco_atom = wrap_atom('stco', bytes(stco))

    stbl = wrap_atom('stbl', stsd + stts + stsc_atom + stsz_atom + stco_atom)
    minf = wrap_atom('minf', gmhd + minf_hdlr + dinf + stbl)
    mdia = wrap_atom('mdia', mdhd_atom + mdia_hdlr + minf)
    return wrap_atom('trak', tkhd + tref + edts + mdia)


def _build_qt_mdta(live_uuid, make, model, creation_iso):
    hdlr = wrap_atom('hdlr', bytes(8) + b'mdta' + bytes(12))
    items = [
        ('com.apple.quicktime.content.identifier', 1, live_uuid.encode('utf-8')),
        ('com.apple.quicktime.make', 1, make.encode('utf-8')),
        ('com.apple.quicktime.model', 1, model.encode('utf-8')),
    ]
    if creation_iso:
        items.append(('com.apple.quicktime.creationdate', 1, creation_iso.encode('utf-8')))
    items += [
        ('com.apple.quicktime.live-photo.auto', 21, b'\x01'),
        ('com.apple.quicktime.live-photo.vitality-score', 23, bytes([0x3F, 0x80, 0x00, 0x00])),
        ('com.apple.quicktime.live-photo.vitality-scoring-version', 21, be32(4)),
    ]

    keys_payload = bytearray(be32(0) + be32(len(items)))
    ilst_payload = bytearray()
    for i, (key, dtype, val) in enumerate(items, start=1):
        kb = key.encode('utf-8')
        keys_payload.extend(be32(len(kb) + 8) + b'mdta' + kb)
        data_atom = wrap_atom('data', be32(dtype) + be32(0) + val)
        ilst_payload.extend(be32(len(data_atom) + 8) + be32(i) + data_atom)
    meta = wrap_atom('meta', hdlr + wrap_atom('keys', bytes(keys_payload)) +
                     wrap_atom('ilst', bytes(ilst_payload)))
    return meta


def write_mov_live_photo_metadata(src_path, out_path, live_uuid, make, model,
                                  shot_at=None, cover_seconds=1.5):
    with open(src_path, 'rb') as f:
        data = bytearray(f.read())

    top = list(mp4.children(data, 0, len(data)))
    moov_atom = next((a for a in top if a.type == 'moov'), None)
    # 保持 ftyp 长度与原文件完全一致，否则后续 mdat 的绝对偏移会整体错位，
    # 导致所有 stco / co64 引用失效（视频无法播放）。
    orig_ftyp = next((a for a in top if a.type == 'ftyp'), None)
    ftyp_payload_len = (orig_ftyp.size - orig_ftyp.hdr) if orig_ftyp else 16
    ftyp_payload = b'qt  ' + be32(0x200) + b'qt  '
    if len(ftyp_payload) < ftyp_payload_len:
        ftyp_payload += b'\x00' * (ftyp_payload_len - len(ftyp_payload))
    elif len(ftyp_payload) > ftyp_payload_len:
        ftyp_payload = ftyp_payload[:ftyp_payload_len]
    qt_ftyp = wrap_atom('ftyp', ftyp_payload)
    if moov_atom is None:
        with open(out_path, 'wb') as f:
            f.write(bytes(data))
        return False

    pre = bytearray()
    for a in mp4.children(data, 0, len(data)):
        if a.pos == moov_atom.pos:
            continue
        if a.type == 'ftyp':
            pre.extend(qt_ftyp)
        else:
            pre.extend(data[a.pos:a.end])

    still_chunk = bytes([
        0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00, 0x01,
        0x00, 0x00, 0x00, 0x09, 0x00, 0x00, 0x00, 0x01, 0x00,
        0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00, 0x01,
    ])
    extra_mdat = wrap_atom('mdat', still_chunk)
    chunk_file_offset = len(pre) + 8

    moov_body = bytearray(data[moov_atom.body:moov_atom.end])
    # moov 被移到文件末尾：若它原本排在 mdat 前面（faststart），mdat 会前移，
    # 必须同步修正所有 stco/co64，否则导出 MOV 的视频帧全部失效。
    moov_ahead_of_mdat = any(
        a.type == 'mdat' and a.pos >= moov_atom.end for a in top)
    if moov_ahead_of_mdat:
        _relocate_chunk_offsets(moov_body, 0, len(moov_body),
                                -moov_atom.size, moov_atom.end)
    qt_sec = 0
    if shot_at:
        utc = shot_at
        if utc.tzinfo is None:
            utc = utc.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
        qt_sec = int(utc.timestamp()) + QT_EPOCH_OFFSET
        _patch_moov_timestamps(moov_body, 0, len(moov_body), qt_sec)

    moov_info = _inspect_moov(moov_body)
    cleaned = bytearray()
    for a in mp4.children(moov_body, 0, len(moov_body)):
        if a.type == 'mvhd':
            copy = bytearray(moov_body[a.pos:a.end])
            _bump_mvhd_next_track_id(copy, moov_info['new_meta_track_id'] + 1)
            cleaned.extend(copy)
        elif a.type not in ('meta', 'udta'):
            cleaned.extend(moov_body[a.pos:a.end])

    has_still_track = False
    v = moov_info['video']
    if v:
        cleaned.extend(_build_still_image_time_trak(
            meta_track_id=moov_info['new_meta_track_id'],
            video_track_id=v['track_id'],
            movie_timescale=moov_info['movie_timescale'],
            movie_duration=moov_info['movie_duration'],
            video_timescale=v['timescale'],
            video_duration=v['duration'],
            frame_delta=v['frame_delta'],
            cover_seconds=cover_seconds,
            chunk_offset=chunk_file_offset,
            qt_sec=qt_sec,
        ))
        has_still_track = True

    cleaned.extend(_build_qt_mdta(live_uuid, make, model, _format_iso8601(shot_at)))
    new_moov = wrap_atom('moov', bytes(cleaned))
    with open(out_path, 'wb') as f:
        f.write(bytes(pre) + extra_mdat + new_moov)
    return has_still_track


def new_uuid():
    return str(_uuid.uuid4()).upper()
