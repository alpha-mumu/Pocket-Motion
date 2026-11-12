# 归属与许可证说明

## 上游项目

- **项目**：Pocket-Motion
- **作者**：MicageEritc
- **地址**：https://github.com/MicageEritc/Pocket-Motion
- **基线版本**：v2.1.1
- **基线 commit**：`d1dcfd46d11956c4a6ae70f29601bf987140f3de`

本项目是上述开源项目的**非官方改进版**，不是上游作者的作品，也未获得其背书。
原项目的全部权利归其作者所有。本仓库仅在本项目新增/修改的部分主张权利。

## 上游许可证状态（如实披露）

上游 `README.md` 声明「本项目基于 MIT License 开源发布」并带有 MIT 徽章。
截至 2026-11-12 核对时，上游仓库根目录**没有 LICENSE 文件**，GitHub API 的
`license` 字段为 `null`，本地基线 commit 中也不含 LICENSE 文件。

因此本仓库采取以下保守做法：

- **不整体再分发上游源码**。仓库内不包含上游 Flutter 工程的完整副本。
- `windows/assets/patch/` 下的 Dart 文件仅作为「针对指定 commit 的修改参考」提供，
  需由使用者自行取得上游源码后应用；完整改动清单见该目录 `PATCH_NOTES.md`。
- 每个源码文件头都保留了上游地址、基线 commit 与许可证指引。
- 若上游后续补充 LICENSE 文件或给出书面授权，可据此调整再分发范围。

新增代码沿用 MIT 许可证，与上游 README 的声明保持一致，全文见 `LICENSE`。

## 第三方组件

| 组件 | 用途 | 说明 |
| --- | --- | --- |
| FFmpeg | 视频解码、封面抽帧、无损重封装 | **不随本仓库分发**。二进制受其自身许可证（LGPL/GPL）约束，构建者需自行准备并遵守对应条款 |
| Flutter / Dart 及上游依赖 | 上游工程 | 由使用者自行安装，见上游 `pubspec.yaml` |
| Python 3 标准库 | 独立 Windows 实现 | PSF License |
| pywebview / pythonnet / bottle / PyInstaller | 窗口与打包 | 构建时按需安装，各自许可证见其项目 |
| Microsoft Edge WebView2 Runtime | 独立窗口渲染 | 由操作系统提供，非本仓库分发内容 |

## 商标

DJI、Osmo Pocket、DJI Mimo、Apple、Live Photo、HEIC、QuickTime、Google、
Motion Photo、Android 等名称与商标归各自权利人所有。本仓库仅作兼容性描述，
不表示任何隶属、授权或背书关系。

## 免责声明

本项目按「现状」提供，不保证对任何特定机型、固件版本或相册应用的兼容性。
使用者应自行备份原始素材并验证导出结果。
