// Pocket Motion 4 - unofficial adaptation of Pocket-Motion (MIT).
// Upstream: https://github.com/MicageEritc/Pocket-Motion @ d1dcfd4
// See LICENSE and NOTICE.md for attribution and license details.

import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';
import 'package:path/path.dart' as p;
import 'models.dart';

/// 媒体扫描器：从相机 DCIM 目录识别 照片/视频/实况。
///
/// Modifications:
///  - 实况判定改为多信号：DJI TPLG / LIVEPHOTO / MOTIONPHOTO 模板串（含 null
///    交错写法）、MP4 容器精确时长（0.30~5.00s，可配置）、与静态 JPG 成对、
///    文件名标记；
///  - 按顶层原子定位 moov，支持 moov 位于文件尾部；
///  - 读取 QuickTime mdta 设备型号与 creation_time、配套 JPG 的 EXIF；
///  - 递归子目录、排除 LRF 低码率代理、记录识别依据、支持手动指定类别。
class MediaScanner {
  static const int _headBytes = 512 * 1024;
  static const int _tailBytes = 512 * 1024;
  static const int _qtEpochOffset = 2082844800;

  static const double minLiveDuration = 0.30;
  static const double maxLiveDuration = 5.00;

  static final _tplgRegex = RegExp(r'TPLG_[\x20-\x7E]{3,80}');
  static final _liveWordRegex =
      RegExp(r'LIVE[_ -]?PHOTO|MOTION[_ -]?PHOTO', caseSensitive: false);
  static final _proxyRegex =
      RegExp(r'LRF|PROXY|_LOW|_PREVIEW|THUMB', caseSensitive: false);
  static final _liveNameRegex = RegExp(
      r'LIVE[_ -]?PHOTO|MOTION[_ -]?PHOTO|[_ -]LP\b|[_ -]LIVE\b',
      caseSensitive: false);
  static final _stemRegex = RegExp(
      r'^(?:DJI[_-])?(\d{14})[_-](\d{3,5})',
      caseSensitive: false);
  static final _fileTimeRegex =
      RegExp(r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})');
  static final _modelRegex = RegExp(
      r'OSMO[\s_-]*POCKET[\s_-]*([0-9])\s*([A-Za-z]{0,2})|'
      r'POCKET[\s_-]*([0-9])\s*([A-Za-z]{0,2})',
      caseSensitive: false);

  final bool durationRule;
  final bool recursive;
  final String defaultModel;

  MediaScanner({
    this.durationRule = true,
    this.recursive = true,
    this.defaultModel = 'DJI Osmo Pocket 4',
  });

  Future<List<MediaAsset>> scan(String dirPath) async {
    final dir = Directory(dirPath);
    if (!await dir.exists()) return [];

    final files = _collectFiles(dir);
    final photos = <String, String>{};
    for (final fp in files) {
      final ext = p.extension(fp).toLowerCase();
      if (_photoExts.contains(ext)) {
        photos.putIfAbsent(_stemKey(p.basename(fp)), () => fp);
      }
    }

    final List<MediaAsset> assets = [];
    for (final fp in files) {
      final name = p.basename(fp);
      final ext = p.extension(name).toLowerCase();
      final stat = await File(fp).stat();
      final shotAt = _parseFileNameTime(name);
      final paired = photos[_stemKey(name)];

      if (_photoExts.contains(ext)) {
        final ex = _readJpegExif(fp);
        assets.add(MediaAsset(
          path: fp,
          filename: name,
          kind: MediaKind.photo,
          shotAt: _parseExifTime(ex['datetime_original']) ?? shotAt,
          sizeBytes: stat.size,
          make: ex['make'],
          model: _normalizeModel(ex['model']) ?? ex['model'],
          modelSource: ex['model'] == null ? null : 'EXIF',
          reason: '静态照片',
        ));
        continue;
      }
      if (ext != '.mp4' && ext != '.mov' && ext != '.m4v') continue;
      if (_proxyRegex.hasMatch(name)) continue; // LRF 代理不导出

      final probe = await _probe(fp);
      final make = probe.make ?? 'DJI';
      final model = probe.model ?? defaultModel;

      final reasons = <String>[];
      MediaKind kind = MediaKind.video;
      if (probe.liveMarkerDesc != null) {
        kind = MediaKind.livePhoto;
        reasons.add(probe.liveMarkerDesc!);
      } else if (durationRule &&
          probe.duration >= minLiveDuration &&
          probe.duration <= maxLiveDuration) {
        kind = MediaKind.livePhoto;
        reasons.add('短片时长 ${probe.duration.toStringAsFixed(2)}s'
            '（实况区间 $minLiveDuration~$maxLiveDuration s）');
      } else if (paired != null &&
          probe.duration > 0 &&
          probe.duration <= maxLiveDuration * 2) {
        kind = MediaKind.livePhoto;
        reasons.add('与静态照片成对 + 短片 ${probe.duration.toStringAsFixed(2)}s');
      } else if (_liveNameRegex.hasMatch(name)) {
        kind = MediaKind.livePhoto;
        reasons.add('文件名含实况标记');
      } else {
        reasons.add('时长 ${probe.duration.toStringAsFixed(2)}s，判定为普通视频');
      }
      if (paired != null) reasons.add('有配套封面 JPG');

      assets.add(MediaAsset(
        path: fp,
        filename: name,
        kind: kind,
        shotAt: shotAt ?? probe.creationTime,
        sizeBytes: stat.size,
        template: probe.template,
        durationSeconds: probe.duration,
        make: make,
        model: model,
        modelSource: probe.modelSource,
        pairedPhotoPath: paired,
        reason: reasons.join('；'),
      ));
    }

    assets.sort((a, b) =>
        (b.shotAt ?? DateTime(0)).compareTo(a.shotAt ?? DateTime(0)));
    return assets;
  }

  static const _photoExts = {'.jpg', '.jpeg', '.dng', '.heic', '.png'};

  List<String> _collectFiles(Directory dir) {
    final out = <String>[];
    void walk(Directory d, int depth) {
      List<FileSystemEntity> entries;
      try {
        entries = d.listSync();
      } catch (_) {
        return;
      }
      for (final e in entries) {
        if (e is File) {
          out.add(e.path);
        } else if (e is Directory && recursive && depth < 2) {
          final low = p.basename(e.path).toLowerCase();
          if (low.startsWith('.') ||
              low == 'system volume information' ||
              low == r'$recycle.bin') {
            continue;
          }
          walk(e, depth + 1);
        }
      }
    }

    walk(dir, 0);
    return out;
  }

  static String _stemKey(String name) {
    final base = p.basenameWithoutExtension(name);
    final m = _stemRegex.firstMatch(base);
    if (m != null) return '${m.group(1)}_${m.group(2)}';
    return base.toLowerCase();
  }

  /// 读取头部、尾部与 moov，得到时长、标记串与设备型号。
  Future<_Probe> _probe(String path) async {
    final r = _Probe();
    final f = File(path);
    final raf = await f.open();
    try {
      final len = await raf.length();
      await raf.setPosition(0);
      final head = await raf.read(math.min(len, _headBytes));
      final tailStart = math.max(0, len - _tailBytes);
      await raf.setPosition(tailStart);
      final tail = await raf.read(len - tailStart);

      final headText = latin1.decode(head, allowInvalid: true);
      final tailText = latin1.decode(tail, allowInvalid: true);
      final allText = '$headText\n$tailText';

      // moov 可能在文件尾部（非 faststart），必须按顶层原子定位后单独读取
      int pos = 0;
      while (pos + 8 <= len) {
        await raf.setPosition(pos);
        final hdr = await raf.read(16);
        if (hdr.length < 8) break;
        final bd = ByteData.sublistView(hdr);
        int sz = bd.getUint32(0);
        final typ = latin1.decode(hdr.sublist(4, 8), allowInvalid: true);
        int hdrLen = 8;
        if (sz == 1 && hdr.length >= 16) {
          sz = bd.getUint64(8);
          hdrLen = 16;
        } else if (sz == 0) {
          sz = len - pos;
        }
        if (sz < hdrLen || pos + sz > len) break;
        if (typ == 'moov' && sz - hdrLen <= 32 * 1024 * 1024) {
          await raf.setPosition(pos + hdrLen);
          final moov = await raf.read(sz - hdrLen);
          _parseMoov(moov, r);
          r.allText = '$headText\n'
              '${latin1.decode(moov, allowInvalid: true)}\n$tailText';
          break;
        }
        pos += sz;
      }
      r.allText ??= allText;
      _analyseText(r);
    } finally {
      await raf.close();
    }
    return r;
  }

  static void _parseMoov(Uint8List moov, _Probe r) {
    for (final a in _children(moov, 0, moov.length)) {
      if (a.type == 'mvhd') {
        final bd = ByteData.sublistView(moov, a.body, a.end);
        if (a.end - a.body >= 20) {
          final ver = moov[a.body];
          int ts = 0, dur = 0, qtSec = 0;
          if (ver == 1 && a.end - a.body >= 32) {
            qtSec = bd.getUint64(4);
            ts = bd.getUint32(20);
            dur = bd.getUint64(24);
          } else {
            qtSec = bd.getUint32(4);
            ts = bd.getUint32(12);
            dur = bd.getUint32(16);
          }
          if (ts > 0 && dur > 0) r.duration = dur / ts;
          if (qtSec > _qtEpochOffset) {
            r.creationTime ??= DateTime.fromMillisecondsSinceEpoch(
              (qtSec - _qtEpochOffset) * 1000,
              isUtc: true,
            ).toLocal();
          }
        }
      } else if (a.type == 'trak') {
        _parseTrak(moov, a, r);
      } else if (a.type == 'meta' || a.type == 'udta') {
        _parseQtMeta(moov, a, r);
      }
    }
  }

  static void _parseTrak(Uint8List buf, _Atom trak, _Probe r) {
    int timescale = 0;
    int total = 0;
    bool isVideo = false;
    void walk(int s, int e) {
      for (final a in _children(buf, s, e)) {
        final bd = ByteData.sublistView(buf, a.body, a.end);
        if (a.type == 'hdlr' && a.end - a.body >= 12) {
          if (latin1.decode(buf.sublist(a.body + 8, a.body + 12)) == 'vide') {
            isVideo = true;
          }
        } else if (a.type == 'mdhd' && a.end - a.body >= 20) {
          final ver = buf[a.body];
          if (ver == 1 && a.end - a.body >= 32) {
            timescale = bd.getUint32(20);
            total = bd.getUint64(24);
          } else {
            timescale = bd.getUint32(12);
            total = bd.getUint32(16);
          }
        } else if (a.type == 'stts' && a.end - a.body >= 16) {
          final count = bd.getUint32(4);
          int sum = 0;
          int off = a.body + 8;
          for (var i = 0; i < count && off + 8 <= a.end; i++) {
            sum += bd.getUint32(off - a.body) * bd.getUint32(off - a.body + 4);
            off += 8;
          }
          if (sum > 0) total = sum;
        } else if (a.type == 'mdia' || a.type == 'minf' || a.type == 'stbl') {
          walk(a.body, a.end);
        }
      }
    }

    walk(trak.body, trak.end);
    if (isVideo && timescale > 0 && total > 0) {
      r.duration = total / timescale;
    }
  }

  static void _parseQtMeta(Uint8List buf, _Atom meta, _Probe r) {
    // meta 是 FullBox（子原子从 body+4 开始），udta 是普通 atom（body 即子原子）
    final start = meta.type == 'meta' ? meta.body + 4 : meta.body;
    if (start + 4 > meta.end) return;
    final subs = _children(buf, start, meta.end);
    final keys = <int, String>{};
    for (final a in subs) {
      if (a.type == 'keys' && a.end - a.body >= 8) {
        final bd = ByteData.sublistView(buf, a.body, a.end);
        final count = bd.getUint32(4);
        int kp = a.body + 8;
        for (var i = 1; i <= count && kp + 8 <= a.end; i++) {
          final ks = bd.getUint32(kp - a.body);
          if (ks < 8 || kp + ks > a.end) break;
          keys[i] = utf8.decode(buf.sublist(kp + 8, kp + ks),
              allowMalformed: true);
          kp += ks;
        }
      }
    }
    for (final a in subs) {
      if (a.type != 'ilst') continue;
      for (final item in _children(buf, a.body, a.end)) {
        // ilst item = size(4) + index(4) + data atom；index 位于 pos+4
        if (item.pos + 8 > item.end) continue;
        final idx =
            ByteData.sublistView(buf, item.pos + 4, item.pos + 8).getUint32(0);
        final key = keys[idx];
        if (key == null) continue;
        for (final d in _children(buf, item.body, item.end)) {
          if (d.type == 'data' && d.end - d.body >= 8) {
            final v = utf8
                .decode(buf.sublist(d.body + 8, d.end), allowMalformed: true)
                .trim();
            final lk = key.toLowerCase();
            if (lk.contains('model') && v.isNotEmpty) {
              r.model ??= _normalizeModel(v) ?? v;
              r.modelSource ??= 'MP4 QuickTime 元数据';
            } else if (lk.contains('make') && v.isNotEmpty) {
              r.make ??= v;
            }
          }
        }
      }
    }
    // 递归 udta 内嵌的 meta
    for (final a in subs) {
      if (a.type == 'udta') _parseQtMeta(buf, a, r);
    }
  }

  static void _analyseText(_Probe r) {
    final text = r.allText ?? '';
    final folded = text.replaceAll('\x00', '');
    for (final candidate in [text, folded]) {
      for (final m in _tplgRegex.allMatches(candidate)) {
        final token = m.group(0)!;
        r.template ??= token;
        if (_liveWordRegex.hasMatch(token)) {
          r.liveMarkerDesc = 'DJI 实况模板串 $token';
        }
      }
      if (_liveWordRegex.hasMatch(candidate) && r.liveMarkerDesc == null) {
        if (candidate.contains('TPLG_') ||
            candidate.contains('GCamera:MotionPhoto')) {
          r.liveMarkerDesc = 'DJI/动态照片实况标记';
        }
      }
    }
    if (r.model == null) {
      final m = _modelRegex.firstMatch(folded);
      if (m != null) {
        final num = m.group(1) ?? m.group(3) ?? '';
        final suffix = (m.group(2) ?? m.group(4) ?? '').toUpperCase();
        r.model = 'DJI Osmo Pocket $num$suffix';
        r.modelSource = 'MP4 固件签名';
      }
    }
  }

  static String? _normalizeModel(String? raw) {
    if (raw == null || raw.trim().isEmpty) return null;
    final s = raw.trim();
    if (s.contains('TPLG') || s.startsWith('DJI_20')) return null;
    final m = _modelRegex.firstMatch(s);
    if (m == null) return null;
    final num = m.group(1) ?? m.group(3) ?? '';
    final suffix = (m.group(2) ?? m.group(4) ?? '').toUpperCase();
    return 'DJI Osmo Pocket $num$suffix';
  }

  static List<_Atom> _children(Uint8List b, int start, int end) {
    final out = <_Atom>[];
    int pos = start;
    while (pos + 8 <= end) {
      int sz = ByteData.sublistView(b, pos, pos + 8).getUint32(0);
      final typ = latin1.decode(b.sublist(pos + 4, pos + 8),
          allowInvalid: true);
      int hdr = 8;
      if (sz == 1) {
        if (pos + 16 > end) break;
        sz = ByteData.sublistView(b, pos + 8, pos + 16).getUint64(0);
        hdr = 16;
      } else if (sz == 0) {
        sz = end - pos;
      }
      if (sz < hdr || pos + sz > end) break;
      out.add(_Atom(pos, sz, hdr, typ));
      pos += sz;
    }
    return out;
  }

  /// 轻量读取 JPEG 的 EXIF Make / Model / DateTimeOriginal
  static Map<String, String> _readJpegExif(String path) {
    final out = <String, String>{};
    try {
      final f = File(path);
      if (!f.existsSync()) return out;
      final raf = f.openSync();
      final len = raf.lengthSync();
      final b = raf.readSync(math.min(len, 256 * 1024));
      raf.closeSync();
      if (b.length < 4 || b[0] != 0xFF || b[1] != 0xD8) return out;
      int pos = 2;
      while (pos + 4 <= b.length) {
        if (b[pos] != 0xFF) {
          pos++;
          continue;
        }
        while (pos < b.length && b[pos] == 0xFF) {
          pos++;
        }
        if (pos >= b.length) break;
        final marker = b[pos];
        pos++;
        if (marker == 0xD8 || marker == 0xD9 ||
            (marker >= 0xD0 && marker <= 0xD7)) {
          continue;
        }
        if (marker == 0xDA || pos + 2 > b.length) break;
        final segLen = (b[pos] << 8) | b[pos + 1];
        if (segLen < 2 || pos + segLen > b.length) break;
        if (marker == 0xE1 &&
            pos + 8 <= b.length &&
            latin1.decode(b.sublist(pos + 2, pos + 8)) == 'Exif\x00\x00') {
          _parseTiff(b.sublist(pos + 8, pos + segLen), out);
        }
        pos += segLen;
      }
    } catch (_) {}
    return out;
  }

  static void _parseTiff(Uint8List t, Map<String, String> out) {
    if (t.length < 8) return;
    final big = t[0] == 0x4D && t[1] == 0x4D;
    int u16(int o) => o + 2 <= t.length
        ? ByteData.sublistView(t, o, o + 2).getUint16(0, big ? Endian.big : Endian.little)
        : 0;
    int u32(int o) => o + 4 <= t.length
        ? ByteData.sublistView(t, o, o + 4).getUint32(0, big ? Endian.big : Endian.little)
        : 0;

    const sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8};

    void ifd(int off, Map<int, String> tags) {
      if (off + 2 > t.length) return;
      final n = u16(off);
      for (var i = 0; i < math.min(n, 256); i++) {
        final e = off + 2 + i * 12;
        if (e + 12 > t.length) break;
        final tag = u16(e);
        final typ = u16(e + 2);
        final count = u32(e + 4);
        int vo = e + 8;
        final size = sizes[typ] ?? 1;
        final total = size * count;
        if (total > 4) vo = u32(e + 8);
        if (tags.containsKey(tag) && (typ == 2 || typ == 7) &&
            vo + total <= t.length) {
          out[tags[tag]!] = ascii
              .decode(t.sublist(vo, vo + total), allowInvalid: true)
              .replaceAll('\x00', '')
              .trim();
        }
      }
    }

    final ifd0 = u32(4);
    final Map<int, String> t0 = {
      0x010F: 'make', 0x0110: 'model', 0x0132: 'datetime'
    };
    ifd(ifd0, t0);
    // 找 ExifIFD 指针
    if (ifd0 + 2 <= t.length) {
      final n = u16(ifd0);
      for (var i = 0; i < math.min(n, 256); i++) {
        final e = ifd0 + 2 + i * 12;
        if (e + 12 > t.length) break;
        if (u16(e) == 0x8769) {
          ifd(u32(e + 8), {0x9003: 'datetime_original'});
          break;
        }
      }
    }
  }

  static DateTime? _parseExifTime(String? s) {
    if (s == null) return null;
    final m = RegExp(r'(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})')
        .firstMatch(s);
    if (m == null) return null;
    try {
      return DateTime(
        int.parse(m[1]!), int.parse(m[2]!), int.parse(m[3]!),
        int.parse(m[4]!), int.parse(m[5]!), int.parse(m[6]!),
      );
    } catch (_) {
      return null;
    }
  }

  /// DJI_20261022215019_... -> 2026-10-22 21:50:19（相机本地时间）
  static DateTime? _parseFileNameTime(String name) {
    final m = _fileTimeRegex.firstMatch(name);
    if (m == null) return null;
    try {
      return DateTime(
        int.parse(m[1]!), int.parse(m[2]!), int.parse(m[3]!),
        int.parse(m[4]!), int.parse(m[5]!), int.parse(m[6]!),
      );
    } catch (_) {
      return null;
    }
  }
}

class _Atom {
  final int pos;
  final int size;
  final int hdr;
  final String type;
  const _Atom(this.pos, this.size, this.hdr, this.type);
  int get body => pos + hdr;
  int get end => pos + size;
}

class _Probe {
  double duration = 0;
  String? template;
  String? make;
  String? model;
  String? modelSource;
  String? liveMarkerDesc;
  String? allText;
  DateTime? creationTime;
}
