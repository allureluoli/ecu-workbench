import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

import app
import checksum
from core import Catalog, diff_report, preview_map
from import_data import import_dataset, safe_unzip
from make_demo import make_demo
from package_release import build_source


class Quiet(app.Handler):
    def log_message(self,*args):pass


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'demo';make_demo(self.data)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
        self.server.workspace=app.Workspace(self.data,self.root/'state')
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def request(self,path,body=None,headers=None):
        req=urllib.request.Request(self.base+path,data=body,headers=headers or {})
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,r.headers,r.read()
        except urllib.error.HTTPError as e:return e.code,e.headers,e.read()

    def data_json(self,path,body=None,headers=None):
        code,_,raw=self.request(path,body,headers);return code,json.loads(raw)

    def upload(self,name):
        code,d=self.data_json('/api/upload',(self.data/name).read_bytes());self.assertEqual(code,200)
        return d

    def test_no_login_catalog_and_pages(self):
        self.assertEqual(self.request('/')[0],200)
        code,d=self.data_json('/api/status')
        self.assertEqual(code,200);self.assertEqual(d['authentication'],'none')
        self.assertEqual(d['counts']['file_vers'],1)
        self.assertEqual(self.data_json('/api/versions')[1]['total'],1)
        self.assertEqual(len(self.data_json('/api/modules')[1]['items']),1)
        for path in ('/api/card/status','/api/license','/api/card/login'):
            self.assertEqual(self.request(path)[0],404)

    def test_map_preview_and_fault_export(self):
        d=self.data_json('/api/version?id=-1')[1];self.assertTrue(d['modules'][0]['decoded'])
        d=self.data_json('/api/maps?ver=-1&module=1')[1];self.assertEqual(len(d['items']),1)
        d=self.data_json('/api/map?ver=-1&module=1&index=0')[1]
        self.assertFalse(d['editable']);self.assertEqual(len(d['values']),5)
        self.assertAlmostEqual(d['values'][0][0],436.8)
        code,headers,body=self.request('/api/faults.csv?code=P0001')
        self.assertEqual(code,200);self.assertIn('P0001',body.decode('utf-8-sig'))

    def test_checksum_correct_and_download(self):
        d=self.upload('edited.bin');sid=d['sid']
        self.assertEqual(d['checksum']['checksum'],'invalid')
        self.assertFalse(d['checksum']['can_correct'])
        self.assertEqual(self.data_json('/api/correct?sid='+sid,b'')[0],400)
        self.assertTrue(self.data_json('/api/baseline?sid='+sid,(self.data/'original.bin').read_bytes())[1]['can_correct'])
        code,d=self.data_json('/api/correct?sid='+sid,b'')
        self.assertEqual((code,d['checksum']),(200,'corrected'))
        code,headers,b=self.request('/api/bin?sid='+sid)
        profiles=checksum.load_profiles(self.data/'checksum_profiles.json')
        self.assertEqual(checksum.inspect(b,-1,profiles=profiles)['checksum'],'verified')
        report=self.data_json('/api/report?sid='+sid)[1]
        self.assertEqual(report['checksum']['sha256'],checksum.sha(b))
        self.assertEqual(report['checksum']['checksum'],'corrected')

    def test_diff_counts_all_bytes(self):
        a=self.upload('original.bin')['sid'];b=self.upload('edited.bin')['sid']
        d=self.data_json('/api/diff',json.dumps({'a':a,'b':b}).encode())[1]
        self.assertEqual(d['changed_bytes'],1);self.assertEqual(d['ranges'][0]['offset'],42)
        d=diff_report(b'abc',b'abcd');self.assertEqual(d['changed_bytes'],1)
        d=diff_report(b'abc',b'abc');self.assertEqual(d['range_count'],0)

    def test_unknown_file_is_never_auto_fixed(self):
        code,d=self.data_json('/api/upload',b'Unknown firmware bytes')
        sid=d['sid'];self.assertEqual(d['checksum']['checksum'],'unsupported')
        code,_,out=self.request('/api/bin?sid='+sid)
        self.assertEqual(out,b'Unknown firmware bytes')
        self.assertEqual(self.data_json('/api/correct?sid='+sid,b'')[0],400)

    def test_profile_install_error_retains_working_catalog(self):
        self.assertEqual(self.data_json('/api/profiles',b'{"schema":0,"profiles":[]}')[0],400)
        self.assertEqual(len(self.data_json('/api/profiles')[1]['profiles']),1)
        code,d=self.data_json('/api/profiles',b'{"schema":1,"profiles":[]}')
        self.assertEqual((code,d['count']),(200,0))
        self.assertEqual(self.data_json('/api/profiles')[1]['profiles'],[])

    def test_local_only_not_an_authentication_gate(self):
        self.assertEqual(self.data_json('/api/status')[0],200)
        self.assertEqual(self.request('/api/status',headers={'Host':'evil.example:'+str(self.server.server_port)})[0],400)
        self.assertEqual(self.request('/api/upload',b'x',headers={'Origin':'https://evil.example'})[0],400)
        self.assertEqual(len(self.server.workspace.sessions),0)

    def test_invalid_map_layout_is_metadata_only(self):
        m={'name':'unknown','zDatas':'00','zBit':'bad','col':1,'row':1}
        self.assertEqual(preview_map(m)['values'],[])
        self.assertTrue(preview_map(m)['warnings'])
        m=json.loads((self.data/'map-packs/-1_1.json').read_text('utf-8'))[0]
        m['lineSkipBytes']=2
        self.assertEqual(preview_map(m)['values'],[])

    def test_database_readonly(self):
        cat=Catalog(self.data)
        with cat.db() as db:
            with self.assertRaises(sqlite3.OperationalError):db.execute('delete from fault_codes')

    def test_import_and_no_unwanted_files(self):
        (self.data/'license.dat').write_text('must not copy')
        (self.data/'secret.pem').write_text('must not copy')
        target=self.root/'local'
        summary=import_dataset(self.data,target)
        self.assertEqual(summary['counts']['file_vers'],1)
        self.assertFalse((target/'license.dat').exists());self.assertFalse((target/'original.bin').exists())
        self.assertTrue((target/'map-packs/-1_1.json').is_file())
        with self.assertRaises(ValueError):import_dataset(self.data,target)

    def test_import_rejects_zip_traversal(self):
        archive=self.root/'bad.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('../outside.txt','bad')
        with self.assertRaises(ValueError):safe_unzip(archive,self.root/'unpacked')
        self.assertFalse((self.root/'outside.txt').exists())

    def test_release_includes_data_and_excludes_state(self):
        package=self.root/'source.zip';result=build_source(package)
        self.assertGreater(result['files'],10)
        with zipfile.ZipFile(package) as z:
            names=z.namelist()
            self.assertTrue(any(n.endswith('data/demo/ecu-mask.db') for n in names))
            self.assertTrue(any(n.endswith('data/local/ecu-mask.db') for n in names))
            self.assertFalse(any('/state/' in n for n in names))
            self.assertFalse(any(n.endswith(('cards.json','license.dat','.pem','.exe','.asar')) for n in names))


if __name__=='__main__':unittest.main()
