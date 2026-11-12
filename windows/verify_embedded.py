# -*- coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""构建前验证：内嵌 ffmpeg 的压缩/释放/缓存复用与内置资源完整性。"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import resources  # noqa: E402


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.digest()


def main():
    orig = os.path.join(HERE, 'ffmpeg.exe')
    size = os.path.getsize(orig)
    info = resources._read_header()
    assert info, 'assets/ffmpeg.bin 不存在'
    assert info[1] == size and info[2] == sha(orig), '内嵌数据头与原文件不一致'
    print('[OK] 内嵌数据：%d -> %d 字节，SHA256 匹配' % (size, os.path.getsize(info[0])))

    # 模拟「单 exe 环境」：没有同目录外置 ffmpeg.exe，缓存指向临时目录
    tmp = tempfile.mkdtemp(prefix='pm4_embed_')
    resources._external_ffmpeg = lambda: None
    resources.cache_dir = lambda: tmp
    t0 = time.time()
    p = resources.ffmpeg_path()
    t1 = time.time()
    assert os.path.abspath(p).startswith(os.path.abspath(tmp)), '未释放到缓存目录'
    assert os.path.getsize(p) == size and sha(p) == info[2], '释放出的文件校验失败'
    r = subprocess.run([p, '-hide_banner', '-version'], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=60)
    assert r.returncode == 0 and b'ffmpeg version' in r.stdout, '释放出的 ffmpeg 无法执行'
    print('[OK] 首次释放并可运行：%.1fs -> %s' % (t1 - t0, p))

    t2 = time.time()
    p2 = resources.ffmpeg_path()
    t3 = time.time()
    assert p2 == p and (t3 - t2) < 2.0, '缓存复用异常'
    print('[OK] 二次启动复用缓存：%.3fs' % (t3 - t2))

    out = tempfile.mkdtemp(prefix='pm4_bundle_')
    root, n = resources.export_bundle(out)
    assert n == len(resources.BUNDLE_MAP), '导出的资源数量不符：%d' % n
    assert os.path.isfile(os.path.join(root, '使用说明.txt')), '说明未导出'
    assert os.path.isfile(os.path.join(root, '上游源码补丁', 'lib', 'src',
                                       'media_scanner.dart')), '补丁未导出'
    assert 'v4.2.1' in resources.read_text('docs/readme.txt'), '说明版本不符'
    print('[OK] 内置文档与源码补丁：%d 个文件 -> %s' % (n, root))

    import webui
    for token in ('showDocs()', 'doSelftest()', 'exportBundle()',
                  '/api/docs', '/api/selftest', '/api/export_bundle',
                  'v4.2.1 单文件版'):
        assert token in webui.INDEX_HTML, '界面缺少：%s' % token
    print('[OK] 界面按钮与后端接口齐全')
    print('RESULT: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
