# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""导出器：Apple Live Photo / Google Motion Photo / 原片复制。"""
from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import struct
import subprocess
import time
import sys

from . import metadata_engine as me


def find_ffmpeg(base_dir=None):
    """取内置视频引擎路径（单文件版：压缩内嵌 + 本机缓存释放）。

    兼容顺序：resources 资源层 -> 显式传入的目录 / exe 同目录外置文件
    -> PATH 上的 ffmpeg。
    """
    try:
        import resources
        p = resources.ffmpeg_path()
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    except Exception:  # noqa: BLE001
        pass
    candidates = []
    if base_dir:
        candidates.append(os.path.join(base_dir, 'ffmpeg.exe'))
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), 'ffmpeg.exe'))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ffmpeg.exe'))
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return 'ffmpeg'


def _no_window_kwargs():
    kw = {}
    if os.name == 'nt':
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    return kw


class Exporter:
    def __init__(self, ffmpeg_path=None, log=None, base_dir=None):
        self.ffmpeg = ffmpeg_path or find_ffmpeg(base_dir)
        self.log = log or (lambda s: None)

    def _run(self, args, must=True):
        # 安全软件对首次执行的 ffmpeg.exe 可能瞬时拦截（stderr 为空），重试 3 次。
        last = None
        for attempt in range(3):
            p = subprocess.run([self.ffmpeg] + args, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **_no_window_kwargs())
            last = p
            if p.returncode == 0 or not must:
                return p
            time.sleep(0.8 * (attempt + 1))
        err = last.stderr.decode('utf-8', 'replace')[-1200:]
        raise RuntimeError('ffmpeg 执行失败 (exit %d, 已重试 3 次): %s' % (last.returncode, err))

    @staticmethod
    def _tmp(out_dir, base, suffix):
        return os.path.join(out_dir, '.%s.%s' % (base, suffix))

    @staticmethod
    def _cleanup(*paths):
        for p in paths:
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass

    @staticmethod
    def _stamp(path, dt):
        if not dt:
            return
        try:
            ts = dt.timestamp()
            os.utime(path, (ts, ts))
        except (OSError, OverflowError, ValueError):
            pass

    @staticmethod
    def _decode_stats(path, ffmpeg, timeout=90):
        """实际解码视频流，返回 (成功, 时长秒, 帧数, 编码, 错误摘要)。"""
        try:
            p = subprocess.run(
                [ffmpeg, '-hide_banner', '-i', path, '-map', '0:v:0',
                 '-an', '-sn', '-dn', '-f', 'null', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, **_no_window_kwargs())
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, 0.0, 0, '', str(e)
        err = p.stderr.decode('utf-8', 'replace')
        dur = 0.0
        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.\d+)', err)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        fm = re.findall(r'frame=\s*(\d+)', err)
        frames = int(fm[-1]) if fm else 0
        cm = re.search(r'Stream #0:\d+.*?Video:\s*([\w\d]+)', err)
        codec = cm.group(1) if cm else ''
        return p.returncode == 0 and frames > 1, dur, frames, codec, err[-300:]

    def probe_dynamic(self, path):
        """校验导出产物里是否真的有可播放的多帧动态视频。

        Apple 实况查 .MOV；Google 动态照片按 XMP 声明的长度从文件尾截出
        内嵌 MP4 再解码。返回 (是否通过, 人类可读描述)。
        """
        ext = os.path.splitext(path)[1].lower()
        tmp = None
        try:
            if ext in ('.jpg', '.jpeg'):
                with open(path, 'rb') as f:
                    head = f.read(8192)
                m = (re.search(rb'GCamera:MicroVideoOffset="(\d+)"', head) or
                     re.search(rb'Item:Mime="video/mp4"[^>]*Item:Length="(\d+)"', head))
                if not m:
                    return False, 'XMP 未声明视频段长度，相册会当成静态图'
                n = int(m.group(1))
                size = os.path.getsize(path)
                if n <= 0 or n >= size:
                    return False, '视频段长度异常 (%d / %d)' % (n, size)
                with open(path, 'rb') as f:
                    f.seek(-n, os.SEEK_END)
                    seg = f.read(n)
                if seg[4:8] != b'ftyp':
                    return False, '尾部不是有效 MP4（缺少 ftyp）'
                tmp = path + '.vseg.tmp'
                with open(tmp, 'wb') as f:
                    f.write(seg)
                target = tmp
            elif ext in ('.mov', '.mp4', '.m4v'):
                target = path
            else:
                return False, '不是可校验的动态视频文件'
            ok, dur, frames, codec, tail = self._decode_stats(target, self.ffmpeg)
            desc = '%.2fs / %d 帧 / %s' % (dur, frames, codec or '未知编码')
            if ok:
                return True, desc
            return False, '解码失败或不足 2 帧：%s  %s' % (desc, tail[-120:])
        except Exception as e:  # noqa: BLE001
            return False, '校验异常：%s' % e
        finally:
            if tmp:
                self._cleanup(tmp)

    @staticmethod
    def _cover_seconds(asset):
        """封面帧时间点：3 秒实况取 1.5s，其它时长取中点（上限 1.5s）。"""
        d = asset.duration or 3.0
        if d <= 0:
            return 1.5
        return max(0.05, min(1.5, d / 2.0))

    def _encode_hevc_frame(self, source, hevc_mp4, is_image=False, seek=None,
                           stream_copy=False):
        args = ['-y']
        if not is_image and seek is not None:
            args += ['-ss', '%.3f' % seek]
        args += ['-i', source, '-map', '0:v:0', '-an', '-sn', '-dn', '-frames:v', '1']
        if stream_copy and not is_image:
            # 源本身是 HEVC：直接复制关键帧，零重编码，封面与原片完全一致
            args += ['-c:v', 'copy', '-tag:v', 'hvc1', hevc_mp4]
            return self._run(args, must=False)
        args += ['-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p',
                 '-c:v', 'libx265', '-profile:v', 'main', '-preset', 'fast',
                 '-crf', '16', '-x265-params', 'log-level=error:bframes=0:ref=1',
                 '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1', hevc_mp4]
        return self._run(args)

    @staticmethod
    def _valid_hevc_mp4(path):
        if not os.path.isfile(path) or os.path.getsize(path) < 4096:
            return False
        try:
            with open(path, 'rb') as f:
                return b'hvcC' in f.read()
        except OSError:
            return False

    def _prepare_hevc_frame(self, asset, tmp_hevc, cover):
        """生成用于 HEIC 封装的单帧 HEVC MP4，返回来源描述。"""
        codec = ((asset.mp4_info or {}).get('codec') or '').lower()
        if codec in ('hvc1', 'hev1', 'hevc', 'h265'):
            self._encode_hevc_frame(asset.path, tmp_hevc, seek=cover, stream_copy=True)
            if self._valid_hevc_mp4(tmp_hevc):
                return '从原 HEVC 视频零重编码提取关键帧'
            self._cleanup(tmp_hevc)
        if asset.paired_jpg and os.path.isfile(asset.paired_jpg):
            try:
                self._encode_hevc_frame(asset.paired_jpg, tmp_hevc, is_image=True)
                if self._valid_hevc_mp4(tmp_hevc):
                    return '相机配套原始 JPG 封面'
            except RuntimeError:
                pass
            self._cleanup(tmp_hevc)
        try:
            self._encode_hevc_frame(asset.path, tmp_hevc, seek=cover)
        except RuntimeError:
            self._cleanup(tmp_hevc)
            self._encode_hevc_frame(asset.path, tmp_hevc, seek=0)
        if not self._valid_hevc_mp4(tmp_hevc):
            self._cleanup(tmp_hevc)
            self._encode_hevc_frame(asset.path, tmp_hevc, seek=0)
        return '视频 %.2fs 处抽帧' % cover

    def export_apple(self, asset, out_dir):
        """返回 (heic_path, mov_path)。"""
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(asset.filename)[0] + 'I'
        out_heic = os.path.join(out_dir, stem + '.HEIC')
        out_mov = os.path.join(out_dir, stem + '.MOV')
        tmp_hevc = self._tmp(out_dir, stem, 'hevc_frame.mp4')
        tmp_remux = self._tmp(out_dir, stem, 'remux.mov')
        cover = self._cover_seconds(asset)
        uid = me.new_uuid()
        make = asset.make or 'DJI'
        model = asset.model or 'DJI Osmo Pocket 4'
        shot = asset.shot_at

        device_label = model if model.upper().startswith(make.upper()) else '%s %s' % (make, model)
        self.log('[%s] Apple 实况：设备 %s / 拍摄 %s / 封面 %.2fs' % (
            asset.filename, device_label,
            shot.strftime('%Y-%m-%d %H:%M:%S') if shot else '未知', cover))

        try:
            # 1) 封面帧：HEVC 源零重编码 > 配套原始 JPG > 抽帧重编码
            cover_source = self._prepare_hevc_frame(asset, tmp_hevc, cover)
            self.log('  封面来源：%s' % cover_source)
            try:
                me.build_heic_from_hevc_mp4(tmp_hevc, out_heic, make, model, uid, shot)
            except (ValueError, IndexError, struct.error) as e:
                # 10-bit / 非标准参数集等情况下零重编码帧可能无法直接封装，
                # 回退到统一 8-bit Main Profile 重编码，保证导出成功。
                self.log('  零重编码封面无法封装（%s），回退到 libx265 编码' % e)
                self._cleanup(tmp_hevc, out_heic)
                self._encode_hevc_frame(asset.path, tmp_hevc, seek=cover)
                me.build_heic_from_hevc_mp4(tmp_hevc, out_heic, make, model, uid, shot)

            # 2) 无损重封装为纯净 QuickTime MOV（剔除 DJI 私有 dbgi 数据轨）
            remux_ok = True
            try:
                self._run(['-y', '-i', asset.path,
                           '-map', '0:v:0', '-map', '0:a?',
                           '-c', 'copy', '-f', 'mov', tmp_remux])
            except RuntimeError as e:
                self.log('  重封装失败，改用原 MP4 直接注入：%s' % e)
                remux_ok = False
            mov_src = tmp_remux if (remux_ok and os.path.isfile(tmp_remux)) else asset.path

            # 3) 注入 still-image-time 定时轨与 QuickTime 实况元数据
            has_still = me.write_mov_live_photo_metadata(
                mov_src, out_mov, uid, make, model, shot, cover_seconds=cover)
            if not has_still:
                self.log('  警告：未找到视频轨，MOV 未写入封面帧定时轨')
        finally:
            self._cleanup(tmp_hevc, tmp_remux)

        self._stamp(out_heic, shot)
        self._stamp(out_mov, shot)
        self.log('  完成 %s.HEIC + .MOV (UUID %s)' % (stem, uid[:8]))
        ok_v, desc = self.probe_dynamic(out_mov)
        self.log('  动态视频%s：%s' % ('校验通过' if ok_v else '校验异常', desc))
        return out_heic, out_mov

    def export_motion(self, asset, out_dir, h264_compat=False):
        """Google Motion Photo：单个 .jpg = JPEG(EXIF+XMP) + 干净 MP4 视频段。

        h264_compat=True 时把内嵌视频转成 H.264（兼容性优先，会重编码）；
        默认 False 走 stream copy 重封装，保持完全无损。
        """
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(asset.filename)[0] + 'A'
        out_path = os.path.join(out_dir, stem + '.jpg')
        tmp_jpg = self._tmp(out_dir, stem, 'still.jpg')
        tmp_mp4 = self._tmp(out_dir, stem, 'motion.mp4')
        cover = self._cover_seconds(asset)
        make = asset.make or 'DJI'
        model = asset.model or 'DJI Osmo Pocket 4'
        shot = asset.shot_at

        device_label = model if model.upper().startswith(make.upper()) else '%s %s' % (make, model)
        self.log('[%s] Google 动态照片：设备 %s / 拍摄 %s' % (
            asset.filename, device_label,
            shot.strftime('%Y-%m-%d %H:%M:%S') if shot else '未知'))
        try:
            jpg = None
            if asset.paired_jpg and os.path.isfile(asset.paired_jpg):
                with open(asset.paired_jpg, 'rb') as f:
                    head = f.read(2)
                    f.seek(0)
                    if head == b'\xff\xd8':
                        jpg = f.read()
                        self.log('  使用配套原始 JPG（无损）')
            if jpg is None:
                self._run(['-y', '-ss', '%.3f' % cover, '-i', asset.path,
                           '-frames:v', '1', '-q:v', '2', '-update', '1', tmp_jpg])
                if not os.path.isfile(tmp_jpg):
                    self._run(['-y', '-ss', '0', '-i', asset.path,
                               '-frames:v', '1', '-q:v', '2', '-update', '1', tmp_jpg])
                with open(tmp_jpg, 'rb') as f:
                    jpg = f.read()

            # 3) 内嵌视频：重封装成干净 MP4，剔除 DJI 私有 dbgi 数据轨，
            #    并让 moov 靠前（+faststart），避免相册解析视频段失败只显示静态图。
            motion_video = asset.path
            try:
                if h264_compat:
                    vargs = ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                             '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k']
                else:
                    vargs = ['-c', 'copy']
                self._run(['-y', '-i', asset.path, '-map', '0:v:0', '-map', '0:a?']
                          + vargs + ['-movflags', '+faststart', '-f', 'mp4', tmp_mp4])
                if os.path.isfile(tmp_mp4) and os.path.getsize(tmp_mp4) > 4096:
                    motion_video = tmp_mp4
                    self.log('  内嵌视频：%s' % (
                        'H.264 兼容转码' if h264_compat else '无损重封装（剔除私有轨）'))
            except RuntimeError as e:
                self.log('  内嵌视频重封装失败，回退使用原 MP4：%s' % e)
            with open(motion_video, 'rb') as f:
                mp4_bytes = f.read()
            dims = me.jpeg_dimensions(jpg)
            exif_app1 = me.build_exif_app1(
                make=make, model=model, shot_at=shot,
                image_width=dims[0] if dims else None,
                image_height=dims[1] if dims else None)
            xmp_app1 = me.build_xmp_app1(len(mp4_bytes), int(cover * 1_000_000))
            jpg = me.inject_into_jpeg(jpg, [exif_app1, xmp_app1])
            with open(out_path, 'wb') as f:
                f.write(jpg)
                f.write(mp4_bytes)
        finally:
            self._cleanup(tmp_jpg, tmp_mp4)

        self._stamp(out_path, shot)
        self.log('  完成 %s.jpg (%.1f MB)' % (stem, os.path.getsize(out_path) / 1048576))
        ok_v, desc = self.probe_dynamic(out_path)
        self.log('  动态视频%s：%s' % ('校验通过' if ok_v else '校验异常', desc))
        return out_path

    def copy_media(self, asset, out_dir, label):
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, asset.filename)
        shutil.copy2(asset.path, dest)
        shot = asset.shot_at
        if shot is None:
            try:
                shot = _dt.datetime.fromtimestamp(os.path.getmtime(asset.path))
            except OSError:
                pass
        self._stamp(dest, shot)
        self.log('  完成 %s/%s' % (label, asset.filename))
        return dest
