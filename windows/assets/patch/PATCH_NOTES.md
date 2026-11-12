# Dart 源码改动清单

基于上游 Pocket-Motion v2.1.1（commit d1dcfd4）。
本文件仅列出改动内容；来源与许可证见仓库根目录 NOTICE.md。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `lib/src/media_scanner.dart` | 实况判定改为多信号：精确容器时长（mvhd/mdhd/stts）、TPLG/LIVEPHOTO 标记（含 null 交错）、配对静态 JPG、文件名标记；按顶层原子定位 moov（支持 moov 在尾部）；解析 QuickTime mdta 设备型号与 creation_time；读取配套 JPG 的 EXIF；递归子目录；排除 LRF 代理；记录识别依据 |
| `lib/src/models.dart` | 增加 `durationSeconds` / `make` / `model` / `modelSource` / `pairedPhotoPath` / `reason` / `overrideKind` 与 `effectiveKind`、`coverSeconds` |
| `lib/src/media_metadata.dart` | `extractFromMp4` 增加 `fallbackModel` 参数；新增 `_normalizeModelName` 归一化紧凑型号写法；补齐两代 Google Motion Photo XMP 字段；新增 `stco/co64` chunk offset 重定位 |
| `lib/src/live_photo_exporter.dart` | 使用扫描器解析出的 make/model；封面帧与 `coverTimeSeconds` 改用 `asset.coverSeconds`（按时长自适应） |
| `lib/src/motion_photo_exporter.dart` | 同上；优先使用配套原始 JPG 作封面；XMP 的 `MotionPhotoPresentationTimestampUs` 改为动态值；补齐 `GCamera:MicroVideo` / `MicroVideoVersion` / `MicroVideoOffset` / `MicroVideoPresentationTimestampUs` |
| `lib/main.dart` | 列表显示时长 / 设备型号 / 识别依据；统计与筛选改用 `effectiveKind`；新增「设为实况 / 设为视频」手动纠正；设备型号支持手动指定 |

## 应用方式

1. 取得上游仓库并切到 commit `d1dcfd4`。
2. 用本目录下 `lib/` 内对应文件覆盖上游同名文件。
3. 保留上游其余文件（例如 `lib/src/tool_path.dart`、`thumbnail_image.dart`），本补丁未包含它们。

## 状态

本补丁按 Dart 3 / Flutter 3.24+ 语法编写，未在 Flutter 环境中执行
`flutter analyze` 或 `flutter build windows`。请以源码参考方式使用。
