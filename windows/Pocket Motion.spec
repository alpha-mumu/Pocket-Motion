# -*- mode: python ; coding: utf-8 -*-
# Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
# Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
# See LICENSE and NOTICE.md for attribution and license details.
"""Pocket Motion 4 · 单文件版打包配置（v4.2.1）。

所有外置资源都通过 datas 打进同一个 onefile exe：
  pm4_assets/ffmpeg.bin       压缩后的内置视频引擎（运行时释放到本机缓存）
  pm4_assets/app.ico          窗口图标
  pm4_assets/docs/readme.txt  使用说明
  pm4_assets/patch/**         上游 Flutter 项目 Dart 源码补丁
"""
import os
from PyInstaller.utils.hooks import collect_all

ROOT = globals().get('SPECPATH', os.getcwd())
ASSETS = os.path.join(ROOT, 'assets')

datas = []
binaries = []
hiddenimports = ['webui', 'gui', 'selftest', 'core', 'webview', 'resources']
for _pkg in ('webview', 'pythonnet', 'clr_loader', 'bottle', 'proxy_tools'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h


def embed(rel, dest):
    src = os.path.join(ASSETS, *rel.split('/'))
    if not os.path.isfile(src):
        raise SystemExit('缺少内嵌资源：%s（请先运行 prepare_assets.py）' % src)
    datas.append((src, dest))


embed('ffmpeg.bin', 'pm4_assets')
embed('app.ico', 'pm4_assets')
embed('docs/readme.txt', 'pm4_assets/docs')
embed('patch/PATCH_NOTES.md', 'pm4_assets/patch')
embed('patch/lib/main.dart', 'pm4_assets/patch/lib')
for _f in ('models.dart', 'media_scanner.dart', 'media_metadata.dart',
           'live_photo_exporter.dart', 'motion_photo_exporter.dart'):
    embed('patch/lib/src/' + _f, 'pm4_assets/patch/lib/src')


a = Analysis(
    ['app.py'],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Pocket Motion',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ASSETS, 'app.ico')],
)
