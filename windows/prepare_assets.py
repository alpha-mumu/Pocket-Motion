# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""构建脚本：把外置 ffmpeg.exe 压缩成内嵌数据 assets/ffmpeg.bin。

格式：MAGIC(8) + 原始长度 Q(8) + 原始文件 sha256(32) + zlib 数据流。
resources.py 在运行时按此格式流式解压并校验后释放到本机缓存。
"""
import hashlib
import os
import struct
import sys
import zlib

MAGIC = b'PM4FF01\0'
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'ffmpeg.exe')
DST = os.path.join(HERE, 'assets', 'ffmpeg.bin')


def main():
    size = os.path.getsize(SRC)
    h = hashlib.sha256()
    comp = zlib.compressobj(6)
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    written = 48
    with open(SRC, 'rb') as f, open(DST, 'wb') as o:
        o.write(MAGIC + struct.pack('<Q', size) + b'\0' * 32)
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            data = comp.compress(chunk)
            o.write(data)
            written += len(data)
        tail = comp.flush()
        o.write(tail)
        written += len(tail)
        o.flush()
        o.seek(16)
        o.write(h.digest())
    print('source : %s (%d bytes)' % (SRC, size))
    print('packed : %s (%d bytes, %.1f%%)' % (DST, written, 100.0 * written / size))
    print('sha256 : %s' % h.hexdigest())
    return 0


if __name__ == '__main__':
    sys.exit(main())
