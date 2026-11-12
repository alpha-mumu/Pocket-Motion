// Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
// Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
// See LICENSE and NOTICE.md for attribution and license details.

/// 媒体资产类型
enum MediaKind { livePhoto, video, photo }

/// 一个媒体文件（实况是 MP4，导出时再拆成 HEIC/JPG + MOV）
class MediaAsset {
  final String path;
  final String filename;
  final MediaKind kind;
  final DateTime? shotAt; // 拍摄时间（相机本地时间）
  final int sizeBytes;
  final String? template; // 相机写入的 TPLG 模板串（调试用）

  // --- v4 新增：Osmo Pocket 4 兼容与拍摄信息 ---
  final double durationSeconds; // MP4 容器精确时长（0 表示未知）
  final String? make; // 真实设备品牌，如 DJI
  final String? model; // 真实设备型号，如 DJI Osmo Pocket 4
  final String? modelSource; // 型号来源（JPG EXIF / QuickTime / 固件签名 / 默认）
  final String? pairedPhotoPath; // 与实况短片成对的静态 JPG
  final String? reason; // 识别依据，便于诊断
  MediaKind? overrideKind; // 用户在界面上手动纠正的分类

  // 非 const：overrideKind 允许界面在扫描后手动纠正分类
  MediaAsset({
    required this.path,
    required this.filename,
    required this.kind,
    required this.shotAt,
    required this.sizeBytes,
    this.template,
    this.durationSeconds = 0,
    this.make,
    this.model,
    this.modelSource,
    this.pairedPhotoPath,
    this.reason,
    this.overrideKind,
  });

  MediaKind get effectiveKind => overrideKind ?? kind;

  String get kindLabel => switch (effectiveKind) {
        MediaKind.livePhoto => '实况',
        MediaKind.video => '视频',
        MediaKind.photo => '照片',
      };

  /// 友好参数：从模板串解析分辨率/帧率，如 "4K / 29.97 fps"
  String get friendlySpec {
    final t = template;
    if (t == null) return '';
    String res = '';
    if (t.contains('4K')) res = '4K';
    if (t.contains('3K')) res = '3K';
    String fps = '';
    if (t.contains('2997')) fps = '29.97 fps';
    if (t.contains('5994')) fps = '59.94 fps';
    if (res.isEmpty && fps.isEmpty) return '';
    return [res, fps].where((s) => s.isNotEmpty).join(' / ');
  }

  /// 封面帧时间点：3 秒实况取 1.5s，其它时长取中点（上限 1.5s）
  double get coverSeconds {
    final d = durationSeconds;
    if (d <= 0) return 1.5;
    final v = d / 2.0 > 1.5 ? 1.5 : d / 2.0;
    return v < 0.05 ? 0.05 : v;
  }
}
