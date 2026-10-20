#!/usr/bin/env python3
"""Version-bound checksum profiles. No guessed ECU addresses or executable plugins."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import zlib

PROFILE_FILE = Path(os.environ.get('ECU_CHECKSUM_PROFILES',
                    Path(__file__).with_name('checksum_profiles.json')))
ALGORITHMS = {'crc32': 4, 'sum16': 2, 'sum32': 4}
FLASHABLE = '未判定（校验和通过不代表可刷写）'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('地址和长度必须为整数或 0x 开头的字符串')
    return value if isinstance(value, int) else int(value, 16 if value.lower().startswith('0x') else 10)


def intersects(a, b):
    return a[0] < b[1] and b[0] < a[1]


def validate_catalog(doc):
    if not isinstance(doc, dict) or doc.get('schema') != 1 or not isinstance(doc.get('profiles'), list):
        raise ValueError('规则文件须含 schema:1 和 profiles 数组')
    profiles, ids = [], set()
    if len(doc['profiles']) > 1000:
        raise ValueError('规则数量超过 1000')
    for raw in doc['profiles']:
        if not isinstance(raw, dict):
            raise ValueError('规则必须是对象')
        p = dict(raw)
        pid = p.get('id')
        if not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', pid) or pid in ids:
            raise ValueError('规则 id 无效或重复')
        ids.add(pid)
        if not isinstance(p.get('name'), str) or not p['name'].strip():
            raise ValueError(f'{pid}: 缺少名称')
        if not isinstance(p.get('evidence'), str) or not p['evidence'].strip():
            raise ValueError(f'{pid}: 必须注明算法依据和样本验证情况')
        p['version'], p['size'] = number(p['version']), number(p['size'])
        if not 1 <= p['size'] <= 600 * 1024 * 1024:
            raise ValueError(f'{pid}: 文件长度无效')
        anchors = p.get('anchors')
        if not isinstance(anchors, list) or not 1 <= len(anchors) <= 64:
            raise ValueError(f'{pid}: 需要 1～64 个固定识别特征')
        p['anchors'] = []
        for a in anchors:
            offset, data = number(a['offset']), bytes.fromhex(a['hex'])
            if not data or offset < 0 or offset + len(data) > p['size']:
                raise ValueError(f'{pid}: 识别特征越界或为空')
            p['anchors'].append({'offset': offset, 'hex': data.hex()})
        # Require actual identity data rather than empty/constant padding anchors.
        identity = b''.join(bytes.fromhex(a['hex']) for a in p['anchors'])
        if len(identity) < 8 or len(set(identity)) < 3:
            raise ValueError(f'{pid}: 固定识别特征至少 8 字节，不能仅匹配填充')
        if not isinstance(p.get('blocks'), list) or not 1 <= len(p['blocks']) <= 128:
            raise ValueError(f'{pid}: 需要 1～128 个校验块')
        blocks, stores, block_ids = [], [], set()
        for raw_block in p['blocks']:
            b = dict(raw_block)
            if not isinstance(b.get('id'), str) or not b['id'] or b['id'] in block_ids:
                raise ValueError(f'{pid}: 校验块 id 无效或重复')
            block_ids.add(b['id'])
            width = ALGORITHMS.get(b.get('algorithm'))
            if width is None:
                raise ValueError(f'{pid}: 不支持的算法 {b.get("algorithm")}')
            b['store_offset'] = number(b['store_offset'])
            if not 0 <= b['store_offset'] <= p['size'] - width:
                raise ValueError(f'{pid}: 校验字段越界')
            for key in ('byteorder', 'word_byteorder'):
                b[key] = b.get(key, 'little')
                if b[key] not in ('little', 'big'):
                    raise ValueError(f'{pid}: 字节序无效')
            b['output'] = b.get('output', 'direct')
            if b['output'] not in ('direct', 'negate', 'ones_complement'):
                raise ValueError(f'{pid}: 输出变换无效')
            regions = b.get('regions')
            if not isinstance(regions, list) or not 1 <= len(regions) <= 128:
                raise ValueError(f'{pid}: 校验区域无效')
            b['regions'] = []
            for pair in regions:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError(f'{pid}: 区域须为 [start,end)，end 不包含')
                start, end = map(number, pair)
                if not 0 <= start < end <= p['size']:
                    raise ValueError(f'{pid}: 校验区域越界或为空')
                if b['algorithm'] != 'crc32' and (end - start) % width:
                    raise ValueError(f'{pid}: 累加区长度必须是字宽的整数倍')
                if any(intersects((start, end), other) for other in b['regions']):
                    raise ValueError(f'{pid}: 同一块的校验区域不能重叠')
                b['regions'].append([start, end])
            store = (b['store_offset'], b['store_offset'] + width)
            if any(intersects(store, s) for s in stores):
                raise ValueError(f'{pid}: 校验字段重叠')
            stores.append(store)
            blocks.append(b)
        # Nested checksums and self-including residue schemes need dedicated plugins.
        for s in stores:
            if any(intersects(s, reg) for b in blocks for reg in b['regions']):
                raise ValueError(f'{pid}: 当前不支持校验字段被任何校验区域包含')
            if any(intersects(s, (a['offset'], a['offset'] + len(bytes.fromhex(a['hex']))))
                   for a in p['anchors']):
                raise ValueError(f'{pid}: 校验字段不能覆盖识别特征')
        p['blocks'] = blocks
        profiles.append(p)
    return {'schema': 1, 'profiles': profiles}


def load_profiles(path=None):
    path = Path(path or PROFILE_FILE)
    if not path.exists():
        return []
    return validate_catalog(json.loads(path.read_text(encoding='utf-8-sig')))['profiles']


def install_profiles(doc, path=None):
    normalized = validate_catalog(doc)
    path = Path(path or PROFILE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return normalized


def matches(p, data, version):
    return ((version is None or p['version'] == int(version)) and len(data) == p['size']
            and all(data[a['offset']:a['offset'] + len(bytes.fromhex(a['hex']))]
                    == bytes.fromhex(a['hex']) for a in p['anchors']))


def compute(data, block):
    width = ALGORITHMS[block['algorithm']]
    value = 0
    for start, end in block['regions']:
        part = memoryview(data)[start:end]
        if block['algorithm'] == 'crc32':
            value = zlib.crc32(part, value)
        else:
            fmt = ('<' if block['word_byteorder'] == 'little' else '>') + ('H' if width == 2 else 'I')
            value += sum(v[0] for v in struct.iter_unpack(fmt, part))
    mask = (1 << (width * 8)) - 1
    if block['output'] == 'negate':
        value = -value
    elif block['output'] == 'ones_complement':
        value = ~value
    return value & mask


def check_blocks(data, profile):
    rows = []
    for b in profile['blocks']:
        start, width = b['store_offset'], ALGORITHMS[b['algorithm']]
        stored = int.from_bytes(data[start:start + width], b['byteorder'])
        expected = compute(data, b)
        rows.append({'id': b['id'], 'algorithm': b['algorithm'], 'regions': b['regions'],
                     'store_offset': start, 'width': width, 'byteorder': b['byteorder'],
                     'stored': f'{stored:0{width * 2}X}', 'expected': f'{expected:0{width * 2}X}',
                     'valid': stored == expected})
    return rows


def inspect(data, version=None, baseline=None, profiles=None):
    report = {'checksum': 'unsupported', 'supported': False, 'can_correct': False,
              'flashable': FLASHABLE, 'size': len(data), 'sha256': sha(data),
              'version': version, 'blocks': [], 'corrections': []}
    try:
        profiles = load_profiles() if profiles is None else profiles
    except (ValueError, OSError, KeyError, TypeError) as e:
        report.update(checksum='profile_error', message='校验规则加载失败：' + str(e))
        return report
    matched = [p for p in profiles if matches(p, data, version)]
    if len(matched) != 1:
        report.update(checksum='ambiguous' if matched else 'unsupported',
                      message='多个校验规则同时匹配，未执行修正' if matched else '未支持：没有匹配版本、长度和固定特征的校验规则',
                      candidates=[p['id'] for p in matched])
        return report
    p = matched[0]
    rows = check_blocks(data, p)
    valid = all(b['valid'] for b in rows)
    baseline_matches = [q for q in profiles if matches(q, baseline, version)] if baseline is not None else []
    baseline_valid = (len(baseline_matches) == 1 and baseline_matches[0]['id'] == p['id'] and
                      all(b['valid'] for b in check_blocks(baseline, p)))
    report.update(checksum='verified' if valid else 'invalid', supported=True,
                  version=p['version'],
                  profile=p['id'], profile_name=p['name'], evidence=p['evidence'], blocks=rows,
                  profile_sha256=sha(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()),
                  baseline_valid=baseline_valid,
                  baseline_sha256=sha(baseline) if baseline is not None else None,
                  can_correct=bool(baseline_valid and not valid),
                  message='规则定义的全部校验块通过' if valid else
                          ('校验不匹配，可基于已通过的原始文件修正' if baseline_valid else
                           '校验不匹配；原始文件缺失、特征不匹配或校验未通过，不能自动修正'))
    return report


def correct(data, version, baseline, profiles=None):
    profiles = load_profiles() if profiles is None else profiles
    before = inspect(data, version, baseline, profiles)
    if not before.get('baseline_valid'):
        raise ValueError(before['message'] + '；修正需要校验通过的同版本原始文件')
    if before['checksum'] == 'verified':
        return bytes(data), before
    if not before['can_correct']:
        raise ValueError(before['message'])
    p = next(p for p in profiles if p['id'] == before['profile'])
    out, changes = bytearray(data), []
    for b in p['blocks']:
        offset, width = b['store_offset'], ALGORITHMS[b['algorithm']]
        old = bytes(out[offset:offset + width])
        new = compute(data, b).to_bytes(width, b['byteorder'])
        if old != new:
            out[offset:offset + width] = new
            changes.append({'id': b['id'], 'offset': offset, 'length': width,
                            'old': old.hex(), 'new': new.hex()})
    after = inspect(out, version, baseline, profiles)
    if after['checksum'] != 'verified':
        raise ValueError('修正后复验失败，未提交任何改动')
    after.update(checksum='corrected', input_sha256=sha(data), corrections=changes,
                 message='校验字段已修正，全部校验块复验通过')
    return bytes(out), after


def finalize(baseline, data, version, profiles=None):
    """Unknown versions remain byte-for-byte unchanged; no checksum guessing."""
    try:
        profiles = load_profiles() if profiles is None else profiles
        report = inspect(data, version, baseline, profiles)
        if report['can_correct']:
            return correct(data, version, baseline, profiles)
        return bytes(data), report
    except (ValueError, OSError, KeyError, TypeError) as e:
        return bytes(data), {'checksum': 'profile_error', 'supported': False, 'can_correct': False,
                             'message': str(e), 'flashable': FLASHABLE, 'sha256': sha(data),
                             'blocks': [], 'corrections': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'fix'])
    parser.add_argument('file', type=Path)
    parser.add_argument('--version', type=int, required=True)
    parser.add_argument('--profiles', type=Path, default=PROFILE_FILE)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'fix' and (not args.baseline or not args.out):
            parser.error('fix 需要 --baseline 和 --out')
        protected = {args.file.resolve()}
        if args.baseline:
            protected.add(args.baseline.resolve())
        if args.out and (args.out.resolve() in protected or args.out.exists()):
            parser.error('输出不能覆盖输入、原始文件或已有文件')
        if args.report and (args.report.resolve() in protected or args.report.exists() or
                            (args.out and args.report.resolve() == args.out.resolve())):
            parser.error('报告路径不能覆盖输入、BIN 输出或已有文件')
        data = args.file.read_bytes()
        baseline = args.baseline.read_bytes() if args.baseline else None
        profiles = load_profiles(args.profiles)
        if args.action == 'fix':
            out, report = correct(data, args.version, baseline, profiles)
            with args.out.open('xb') as f:
                f.write(out)
        else:
            report = inspect(data, args.version, baseline, profiles)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            with args.report.open('x', encoding='utf-8') as f:
                f.write(rendered)
        print(rendered)
        return 0 if report['checksum'] in ('verified', 'corrected') else 2
    except (ValueError, OSError, KeyError, TypeError) as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
