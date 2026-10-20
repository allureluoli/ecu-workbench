#!/usr/bin/env python3
"""ECU Workbench: anonymous, loopback-only, standard-library web application."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import urllib.parse

import checksum
from core import Catalog, diff_report, preflight, preview_map

ROOT=Path(__file__).resolve().parent
MAX_UPLOAD=64*1024*1024


class Workspace:
    def __init__(self,data_root,state_root):
        self.catalog=Catalog(data_root)
        self.state=Path(state_root)
        self.sessions={}
        self.lock=threading.RLock()

    @property
    def profile_path(self):
        custom=self.state/'checksum_profiles.json'
        return custom if custom.exists() else self.catalog.root/'checksum_profiles.json'

    def profiles(self):
        return checksum.load_profiles(self.profile_path)

    def session(self,sid):
        if sid not in self.sessions:
            raise ValueError('文件会话已失效，请重新加载')
        return self.sessions[sid]

    def report(self,sid):
        s=self.session(sid)
        report=checksum.inspect(s['data'],s['version'],s['baseline'],self.profiles())
        previous=s.get('checksum',{})
        if (report['checksum']=='verified' and previous.get('checksum')=='corrected'
                and previous.get('sha256')==report['sha256']
                and previous.get('profile_sha256')==report.get('profile_sha256')):
            report.update(checksum='corrected',message=previous['message'],
                          corrections=previous['corrections'],input_sha256=previous.get('input_sha256'))
        s['checksum']=report
        return report


class Handler(BaseHTTPRequestHandler):
    server_version='ECUWorkbench/0.1'

    @property
    def workspace(self):
        return self.server.workspace

    def send(self,status,body,kind='application/json; charset=utf-8',headers=None):
        if isinstance(body,(dict,list)):
            body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
        elif isinstance(body,str):
            body=body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)

    def local_request(self,mutation=False):
        host=self.headers.get('Host','')
        url=urllib.parse.urlsplit('http://'+host)
        if url.hostname not in ('127.0.0.1','localhost','::1') or url.port!=self.server.server_port:
            raise ValueError('仅接受本机地址')
        origin=self.headers.get('Origin')
        if mutation and origin and origin!='http://'+host:
            raise ValueError('拒绝跨来源写入请求')

    def query(self):
        url=urllib.parse.urlsplit(self.path)
        values=urllib.parse.parse_qs(url.query)
        return url.path,lambda k,d=None:values.get(k,[d])[0]

    def integer(self,value,default=None):
        return int(value) if value not in (None,'') else default

    def do_GET(self):
        try:
            self.local_request()
            path,g=self.query();cat=self.workspace.catalog
            static={'/':('index.html','text/html; charset=utf-8'),
                    '/app.js':('app.js','text/javascript; charset=utf-8'),
                    '/style.css':('style.css','text/css; charset=utf-8')}
            if path in static:
                name,kind=static[path]
                return self.send(200,(ROOT/'web'/name).read_bytes(),kind)
            if path=='/api/status':
                status=cat.stats();status['checksum_profiles']=len(self.workspace.profiles())
                status['authentication']='none';status['backend_dependency']=False
                return self.send(200,status)
            if path=='/api/directories':return self.send(200,{'items':cat.directories()})
            if path=='/api/versions':
                return self.send(200,cat.versions(g('q',''),self.integer(g('dir')),
                    min(1000,max(1,int(g('limit',100)))),max(0,int(g('offset',0)))))
            if path=='/api/version':return self.send(200,cat.version(int(g('id'))))
            if path=='/api/modules':return self.send(200,{'items':cat.modules()})
            if path in ('/api/faults','/api/faults.csv'):
                filters={'vid':self.integer(g('ver')),'query':g('q',''),'code':g('code','')}
                if path.endswith('.csv'):
                    return self.send(200,cat.faults_csv(**filters),'text/csv; charset=utf-8',
                        {'Content-Disposition':'attachment; filename="fault-codes.csv"'})
                return self.send(200,cat.faults(**filters,limit=min(500,max(1,int(g('limit',100)))),offset=max(0,int(g('offset',0)))))
            if path in ('/api/maps','/api/map'):
                pack=cat.maps(int(g('ver')),int(g('module')))
                if path=='/api/map':
                    idx=int(g('index',0))
                    if not 0<=idx<len(pack['items']):raise ValueError('地图不存在或尚未解析')
                    return self.send(200,preview_map(pack['items'][idx]))
                if g('download')=='1':
                    return self.send(200,pack,headers={'Content-Disposition':'attachment; filename="map-snapshot.json"'})
                summary=[{k:v for k,v in m.items() if k not in ('xDatas','yDatas','zDatas')} for m in pack['items']]
                return self.send(200,dict(pack,items=summary))
            if path=='/api/asset':
                mid=int(g('module'));asset=cat.asset_path(mid)
                return self.send(200,asset.read_bytes(),'application/octet-stream',
                    {'Content-Disposition':f'attachment; filename="module-{mid}{asset.suffix}"'})
            if path=='/api/profiles':return self.send(200,{'schema':1,'profiles':self.workspace.profiles()})
            if path in ('/api/report','/api/bin'):
                with self.workspace.lock:
                    sid=g('sid');s=self.workspace.session(sid)
                    if path=='/api/bin':
                        out,report=checksum.finalize(s['baseline'],s['data'],s['version'],self.workspace.profiles())
                        s['data']=out
                        if report['checksum']!='verified':s['checksum']=report
                        report=self.workspace.report(sid)
                        return self.send(200,out,'application/octet-stream',
                            {'Content-Disposition':'attachment; filename="checked.bin"','X-Checksum-State':report['checksum']})
                    report={'preflight':preflight(s['data']),'checksum':self.workspace.report(sid),
                            'baseline_sha256':checksum.sha(s['baseline']),'candidate_versions':s['candidates']}
                    headers={'Content-Disposition':'attachment; filename="inspection-report.json"'} if g('download')=='1' else None
                    return self.send(200,report,headers=headers)
            return self.send(404,{'error':'接口不存在'})
        except (ValueError,TypeError,KeyError,IndexError,OSError) as e:
            return self.send(400,{'error':str(e)})
        except Exception:
            return self.send(500,{'error':'数据读取失败，请检查导入的数据格式'})

    def do_POST(self):
        try:
            self.local_request(mutation=True)
            path,g=self.query();length=int(self.headers.get('Content-Length','0'))
            bound=MAX_UPLOAD if path in ('/api/upload','/api/baseline') else 1024*1024
            if length<0 or length>bound:return self.send(413,{'error':f'请求超过上限 {bound} 字节'})
            body=self.rfile.read(length)
            if len(body)!=length:raise ValueError('上传文件不完整')
            with self.workspace.lock:
                if path=='/api/upload':
                    if not body:raise ValueError('不能加载空文件')
                    if len(self.workspace.sessions)>=8:raise ValueError('最多保留 8 个文件会话，请重启清空')
                    sid=secrets.token_hex(16);candidates=self.workspace.catalog.identify(body)
                    version=candidates[0]['id'] if candidates else None
                    self.workspace.sessions[sid]={'data':body,'baseline':body,'version':version,'candidates':candidates}
                    return self.send(200,{'sid':sid,'preflight':preflight(body),'candidates':candidates,
                        'checksum':self.workspace.report(sid)})
                if path=='/api/baseline':
                    s=self.workspace.session(g('sid'));profiles=self.workspace.profiles()
                    current=checksum.inspect(s['data'],s['version'],profiles=profiles)
                    baseline=checksum.inspect(body,s['version'],profiles=profiles)
                    if (baseline['checksum']!='verified' or not current.get('supported')
                            or current.get('profile')!=baseline.get('profile')):
                        raise ValueError('原始文件必须校验通过且与当前文件匹配同一规则')
                    s['baseline']=body;s.pop('checksum',None)
                    return self.send(200,self.workspace.report(g('sid')))
                if path=='/api/correct':
                    s=self.workspace.session(g('sid'))
                    out,report=checksum.correct(s['data'],s['version'],s['baseline'],self.workspace.profiles())
                    s['data']=out;s['checksum']=report
                    return self.send(200,report)
                if path=='/api/profiles':
                    doc=json.loads(body.decode('utf-8-sig'))
                    result=checksum.install_profiles(doc,self.workspace.state/'checksum_profiles.json')
                    for s in self.workspace.sessions.values():s.pop('checksum',None)
                    return self.send(200,{'ok':True,'count':len(result['profiles'])})
                if path=='/api/diff':
                    req=json.loads(body.decode('utf-8'))
                    return self.send(200,diff_report(self.workspace.session(req['a'])['data'],
                                                    self.workspace.session(req['b'])['data']))
            return self.send(404,{'error':'接口不存在'})
        except (ValueError,TypeError,KeyError,IndexError,OSError) as e:
            return self.send(400,{'error':str(e)})
        except Exception:
            return self.send(500,{'error':'操作失败，未提交校验修正'})


def create_server(data_root,state_root,port=23010):
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.workspace=Workspace(data_root,state_root)
    return server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--state-dir',type=Path,default=ROOT/'state')
    parser.add_argument('--port',type=int,default=23010)
    args=parser.parse_args()
    data=args.data_dir or (ROOT/'data/local' if (ROOT/'data/local/ecu-mask.db').exists() else ROOT/'data/demo')
    server=create_server(data,args.state_dir,args.port)
    print(f'ECU Workbench: http://127.0.0.1:{server.server_port}/',flush=True)
    print('Dataset: '+server.workspace.catalog.stats()['dataset'],flush=True)
    print('Local only. No login, card keys, activation or vendor server required.',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
