# Pocket Motion 4 · Osmo Pocket 4 实况导出适配

> **非官方改进版**。本项目基于开源项目
> [Pocket-Motion](https://github.com/MicageEritc/Pocket-Motion)（作者 **MicageEritc**，MIT 声明）做兼容性改进，
> 不是上游作者的作品，与其无隶属关系。完整归属与许可证状态见
> [NOTICE.md](NOTICE.md)。

把 DJI Osmo Pocket 4 的 3 秒实况短片导出为 **Apple 实况照片
（`.HEIC` + `.MOV`）** 或 **Google 动态照片（单 `.jpg`）**，并按
「实况 / 视频 / 照片」三类自动归档到不同文件夹。Windows 版为单个 exe，
自带窗口、内嵌视频引擎与全部资源，所有处理都在本机完成。

*English: An unofficial adaptation of MicageEritc/Pocket-Motion adding Osmo
Pocket 4 compatibility, multi-signal live-photo detection, and a standalone
Windows build. See NOTICE.md for attribution.*

## 本版改进

- 实况识别改为多信号判定：容器精确时长（默认 0.30~5.00 秒）、DJI 模板串、
  与静态 JPG 成对、文件名标记；阈值可在界面调整，判定依据逐条可见。
- 拍摄设备型号与拍摄时间改为自动解析（配套 JPG EXIF → QuickTime mdta →
  固件签名 → 文件名时间戳），并支持手动指定覆盖。
- 封面帧按实际时长取中点，与 MOV 的 `still-image-time` 轨、Google XMP 的
  `PresentationTimestampUs` 对齐。
- Apple 实况：HEVC 源零重编码提取关键帧做 HEIC 封面；写入完整 Apple
  MakerNote、`ContentIdentifier`、定时元数据轨与 `tref→cdsc`。
- Google 动态照片：同时写入两代 XMP 字段，内嵌视频无损重封装
  （`+faststart`，剔除私有数据轨），可选 H.264 兼容转码。
- 新增 `moov` 位于文件尾部的支持、`stco/co64` chunk offset 重定位、
  moov 前移时的偏移修正。
- 导出后逐个**实际解码数帧**校验动态视频是否真实存在，界面提供复核按钮
  与一键自检。
- 提供独立的 Windows 单文件实现（Python + 本地 Web UI + WebView2 窗口），
  并把对上游 Flutter 工程的改进以源码补丁形式一并交付。

## 目录结构

```
.github/…（可选）  LICENSE           MIT 许可证全文
NOTICE.md          上游归属、许可证状态、第三方组件与商标声明
CHANGELOG.md       版本改动记录
windows/           独立 Windows 实现（可直接运行 / 打包）
  app.py            启动器
  webui.py          本地界面与导出调度
  gui.py            独立窗口
  core/             扫描、元数据引擎、导出器、MP4 解析
  resources.py      单文件版内嵌资源
  selftest.py       端到端自检
  Pocket Motion.spec  PyInstaller 打包配置
  assets/patch/     针对上游 Flutter 工程的 Dart 源码改动与清单
```

**上游源码不包含在本仓库中**，请按
[`windows/assets/patch/PATCH_NOTES.md`](windows/assets/patch/PATCH_NOTES.md)
的说明自行取得上游代码后应用补丁。

## 分类导出

在界面里设置「导出位置」，扫描后于「实况 / 视频 / 照片」三个标签页分别
「全选当前」（选择跨标签页保留），一次导出即可：

```
导出位置/
├── live/    <文件名>I.HEIC + <文件名>I.MOV，或 <文件名>A.jpg
├── movie/   长视频原片（无损复制）
└── photo/   静态照片原片（无损复制）
```

## 使用与构建

**直接使用打包版**：从 Releases 下载 `Pocket Motion.exe`，解压后双击即可
（Windows 10/11，需系统自带 Edge WebView2 Runtime）。

**源码运行**：

```bash
cd windows
python app.py --browser
```

**自行打包**：

```bash
cd windows
# 需自备 ffmpeg.exe（受其自身许可证约束，本仓库不分发）
python prepare_assets.py
python -m PyInstaller "Pocket Motion.spec" --distpath dist-single --workpath build-single
```

`prepare_assets.py` 会把 `ffmpeg.exe` 压缩为 `assets/ffmpeg.bin`；
该文件与 `ffmpeg.exe`、`dist*`、`build*` 均已在 `.gitignore` 中排除。

## 状态与限制

- 独立 Windows 版的端到端自检（识别 → 导出 → 实际解码校验）通过，
  自检使用按 DJI 文件规范构造的模拟素材，**尚未用 Osmo Pocket 4 真机原片验证**。
- `windows/assets/patch/` 下的 Dart 改动**未在 Flutter 环境编译验证**，
  以源码参考方式提供。
- Windows 看图软件只显示静态封面属正常现象；Apple 实况需把同名
  `.HEIC` 与 `.MOV` 一起导入 iPhone 相册，Google 动态照片需导入 Google 相册。
- 上游许可证文件状态见 [NOTICE.md](NOTICE.md)。

## 许可证

新增代码采用 [MIT](LICENSE)。上游部分的归属与许可证状态见
[NOTICE.md](NOTICE.md)。
