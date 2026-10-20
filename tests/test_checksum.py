"""Known vectors and synthetic firmware only; no claimed ECU validation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import zlib

import checksum as cs


def fixture(algorithm='crc32', byteorder='little', output='direct'):
    data = bytearray(range(128))
    data[:12] = b'ECU-CK-DEMO!'
    block = {'id': 'calibration', 'algorithm': algorithm, 'regions': [[16, 96]],
             'store_offset': 120, 'byteorder': byteorder, 'word_byteorder': 'little', 'output': output}
    p = {'id': 'synthetic-demo', 'name': '合成测试文件（非 ECU）', 'version': -1,
         'size': 128, 'anchors': [{'offset': 0, 'hex': bytes(data[:12]).hex()}],
         'evidence': '合成夹具；仅验证引擎行为，不代表任何 ECU 支持', 'blocks': [block]}
    profiles = cs.validate_catalog({'schema': 1, 'profiles': [p]})['profiles']
    width = cs.ALGORITHMS[algorithm]
    # Independent reference calculation for baseline fixture.
    if algorithm == 'crc32':
        reference = zlib.crc32(bytes(data[16:96]))
    else:
        reference = sum(int.from_bytes(data[i:i+width], 'little') for i in range(16, 96, width))
    if output == 'negate':
        reference = -reference
    elif output == 'ones_complement':
        reference = ~reference
    data[120:120+width] = (reference & ((1 << (8*width))-1)).to_bytes(width, byteorder)
    return bytes(data), profiles


class ChecksumTests(unittest.TestCase):
    def test_crc32_known_vector(self):
        b = {'algorithm': 'crc32', 'regions': [[0, 9]], 'output': 'direct'}
        self.assertEqual(cs.compute(b'123456789', b), 0xCBF43926)

    def test_word_sums_known_vectors(self):
        b = {'algorithm': 'sum16', 'regions': [[0, 4]], 'output': 'direct', 'word_byteorder': 'little'}
        self.assertEqual(cs.compute(bytes.fromhex('01000200'), b), 3)
        b.update(word_byteorder='big')
        self.assertEqual(cs.compute(bytes.fromhex('01000200'), b), 768)
        b.update(algorithm='sum32', regions=[[0, 8]], word_byteorder='little')
        self.assertEqual(cs.compute(bytes.fromhex('ffffffff02000000'), b), 1)

    def test_crc_disjoint_regions_follow_declared_order(self):
        b = {'algorithm': 'crc32', 'regions': [[4, 8], [0, 2]], 'output': 'direct'}
        self.assertEqual(cs.compute(b'abcdefgh', b), zlib.crc32(b'efghab'))

    def test_verify_auto_identity_and_explicit_wrong_version(self):
        data, p = fixture()
        self.assertEqual(cs.inspect(data, profiles=p)['checksum'], 'verified')
        self.assertEqual(cs.inspect(data, 5, profiles=p)['checksum'], 'unsupported')

    def test_repair_all_algorithms_endians_and_outputs(self):
        for algorithm in cs.ALGORITHMS:
            for byteorder in ('little', 'big'):
                for output in ('direct', 'negate', 'ones_complement'):
                    with self.subTest(algorithm=algorithm, byteorder=byteorder, output=output):
                        baseline, p = fixture(algorithm, byteorder, output)
                        edited = bytearray(baseline); edited[42] ^= 0x23
                        original_input = bytes(edited)
                        self.assertEqual(cs.inspect(edited, -1, baseline, p)['checksum'], 'invalid')
                        fixed, report = cs.correct(edited, -1, baseline, p)
                        self.assertEqual(report['checksum'], 'corrected')
                        self.assertEqual(cs.inspect(fixed, -1, profiles=p)['checksum'], 'verified')
                        self.assertEqual(bytes(edited), original_input)
                        self.assertEqual(fixed[:120], original_input[:120])
                        self.assertEqual(fixed[124:], original_input[124:])
                        again, r = cs.correct(fixed, -1, baseline, p)
                        self.assertEqual(again, fixed)
                        self.assertEqual(r['checksum'], 'verified')

    def test_missing_or_invalid_baseline_never_repairs(self):
        baseline, p = fixture()
        bad = bytearray(baseline); bad[50] ^= 1
        for original in (None, bytes(bad), b'truncated'):
            with self.subTest(original=original):
                with self.assertRaises(ValueError):
                    cs.correct(bad, -1, original, p)
                out, report = cs.finalize(original, bad, -1, p)
                self.assertEqual(out, bytes(bad))
                self.assertFalse(report['can_correct'])

    def test_unknown_identity_and_size(self):
        data, p = fixture()
        altered = bytearray(data); altered[0] ^= 1
        for b in (altered, data[:-1], data+b'0'):
            with self.subTest(size=len(b)):
                out, report = cs.finalize(data, b, -1, p)
                self.assertEqual(out, bytes(b))
                self.assertEqual(report['checksum'], 'unsupported')

    def test_ambiguous_rules_no_repair(self):
        data, p = fixture(); q = copy.deepcopy(p[0]); q['id'] = 'other'
        report = cs.inspect(data, -1, data, p+[q])
        self.assertEqual(report['checksum'], 'ambiguous')
        with self.assertRaises(ValueError):
            cs.correct(data, -1, data, p+[q])

    def test_multiple_independent_blocks(self):
        data, p = fixture(); p = copy.deepcopy(p)
        p[0]['blocks'].append({'id':'second', 'algorithm':'sum16', 'regions':[[96,112]],
            'store_offset':126, 'byteorder':'big', 'word_byteorder':'little', 'output':'direct'})
        p = cs.validate_catalog({'schema':1,'profiles':p})['profiles']
        data = bytearray(data)
        data[126:128] = cs.compute(data, p[0]['blocks'][1]).to_bytes(2,'big')
        base = bytes(data); data[42] ^= 1; data[100] ^= 1
        out, report = cs.correct(data, -1, base, p)
        self.assertEqual(len(report['corrections']), 2)
        self.assertTrue(all(b['valid'] for b in report['blocks']))

    def test_ambiguous_baseline_cannot_authorize_repair(self):
        data,p=fixture();q=copy.deepcopy(p[0]);q['id']='second-identity'
        q['anchors'].append({'offset':16,'hex':data[16:24].hex()})
        edited=bytearray(data);edited[16]^=1
        report=cs.inspect(edited,-1,data,p+[q])
        self.assertEqual(report['checksum'],'invalid')
        self.assertFalse(report['can_correct'])
        with self.assertRaises(ValueError):
            cs.correct(edited,-1,data,p+[q])

    def test_bad_profile_definitions(self):
        _, p = fixture()
        mutations = [lambda p:p.update(size=0), lambda p:p.update(anchors=[]),
            lambda p:p['anchors'][0].update(offset=-1),
            lambda p:p['anchors'][0].update(hex='00'*12),
            lambda p:p['blocks'][0].update(algorithm='guess'),
            lambda p:p['blocks'][0].update(store_offset=127),
            lambda p:p['blocks'][0].update(regions=[[0,200]]),
            lambda p:p['blocks'][0].update(regions=[[16,96],[20,24]]),
            lambda p:p['blocks'][0].update(store_offset=20),
            lambda p:p['blocks'][0].update(byteorder='native'),
            lambda p:p['blocks'][0].update(algorithm='sum16', regions=[[16,95]]),
            lambda p:p['blocks'][0].update(store_offset=0),
            lambda p:p['blocks'].append(copy.deepcopy(p['blocks'][0]))]
        for change in mutations:
            item = copy.deepcopy(p[0]); change(item)
            with self.subTest(item=item), self.assertRaises((ValueError, KeyError, TypeError)):
                cs.validate_catalog({'schema':1,'profiles':[item]})

    def test_install_is_atomic_and_invalid_config_retained(self):
        _, p = fixture()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'profiles.json'
            cs.install_profiles({'schema':1,'profiles':p},path)
            previous = path.read_bytes()
            with self.assertRaises(ValueError):
                cs.install_profiles({'schema':0,'profiles':[]},path)
            self.assertEqual(path.read_bytes(),previous)
            self.assertEqual(cs.load_profiles(path),p)
            self.assertEqual(list(Path(d).glob('*.tmp')),[])

    def test_empty_catalog_does_not_claim_real_ecu_support(self):
        data = bytes(8*1024*1024)
        out, report = cs.finalize(data,data,5,[])
        self.assertEqual(report['checksum'],'unsupported')
        self.assertFalse(report['supported'])
        self.assertEqual(out,data)

if __name__ == '__main__':
    unittest.main()
