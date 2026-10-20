"""Offline catalog, explicit map snapshots, BIN preflight and bounded diff reports."""
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import struct


class Catalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'ecu-mask.db'
        if not self.path.is_file():
            raise ValueError('数据目录中没有 ecu-mask.db')
        with self.db() as db:
            names = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        required = {'file_vers', 'modules', 'module_configs', 'fault_codes', 'file_ver_dirs'}
        if not required <= names:
            raise ValueError('数据库缺少表：' + ', '.join(sorted(required - names)))

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('pragma query_only=on')
        try:
            yield c
        finally:
            c.close()

    def stats(self):
        with self.db() as db:
            counts = {t: db.execute(f'select count(*) from {t} where deleted_at=0').fetchone()[0]
                      for t in ('file_vers','modules','module_configs','fault_codes','file_ver_dirs')}
        counts['decoded_packs'] = len(list((self.root/'map-packs').glob('*.json')))
        counts['assets'] = sum(p.is_file() for p in (self.root/'file-ver-mappacks').glob('*'))
        manifest = self.root/'manifest.json'
        info = json.loads(manifest.read_text('utf-8')) if manifest.exists() else {}
        return {'counts':counts,'dataset':info.get('name','用户本地数据'),
                'demo':info.get('synthetic',False),'redistribution':info.get('redistribution','未确认')}

    def directories(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('select id,name,parent_id from file_ver_dirs where deleted_at=0 order by id')]

    def versions(self, query='', directory=None, limit=100, offset=0):
        where, args = ['v.deleted_at=0'], []
        for word in query.split():
            where.append('(v.name like ? or v.resolve_type like ?)')
            args.extend(['%'+word+'%']*2)
        if directory is not None:
            # UNION prevents cycles in malformed imported directory trees.
            where.append('v.directory_id in (with recursive d(id) as (select ? union select x.id from file_ver_dirs x join d on x.parent_id=d.id) select id from d)')
            args.append(directory)
        cond = ' and '.join(where)
        with self.db() as db:
            total = db.execute('select count(*) from file_vers v where '+cond,args).fetchone()[0]
            rows = [dict(r) for r in db.execute('''select v.id,v.name,v.resolve_type,v.directory_id,
                coalesce(m.n,0) module_count from file_vers v left join
                (select file_ver_id,count(*) n from modules where deleted_at=0 group by file_ver_id) m
                on m.file_ver_id=v.id where '''+cond+' order by v.name,v.id limit ? offset ?',args+[limit,offset])]
        return {'total':total,'items':rows,'limit':limit,'offset':offset}

    def version(self, vid):
        with self.db() as db:
            r = db.execute('select * from file_vers where id=? and deleted_at=0',(vid,)).fetchone()
            if r is None:
                raise ValueError('版本不存在')
            out = dict(r)
            out['modules'] = [dict(r) for r in db.execute('''select m.id,m.file_ver_id,m.file_name,m.oss_full_name,
                m.size,c.name,c.module_type from modules m left join module_configs c on c.id=m.module_config_id
                where m.file_ver_id=? and m.deleted_at=0 order by c.id,m.id''',(vid,))]
        for m in out['modules']:
            m['decoded'] = self.pack_path(vid,m['id']).is_file()
            m['asset_available'] = self.asset_path(m['id'], required=False) is not None
        return out

    def modules(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('''select c.id,c.name,c.module_type,count(m.id) module_count,
                count(distinct m.file_ver_id) version_count from module_configs c left join modules m
                on m.module_config_id=c.id and m.deleted_at=0 where c.deleted_at=0 group by c.id order by c.id''')]

    def faults(self, vid=None, query='', code='', limit=100, offset=0):
        where,args=['f.deleted_at=0'],[]
        if vid is not None:
            where.append('f.file_ver_id=?');args.append(vid)
        if code:
            where.append('f.code like ?');args.append('%'+code+'%')
        for word in query.split():
            where.append('f.desc like ?');args.append('%'+word+'%')
        cond=' and '.join(where)
        with self.db() as db:
            total=db.execute('select count(*) from fault_codes f where '+cond,args).fetchone()[0]
            rows=[dict(r) for r in db.execute('''select f.id,f.code,f.desc,f.file_ver_id,v.name version_name
                from fault_codes f left join file_vers v on v.id=f.file_ver_id where '''+cond+
                ' order by f.file_ver_id,f.code,f.id limit ? offset ?',args+[limit,offset])]
        return {'total':total,'items':rows,'limit':limit,'offset':offset}

    def faults_csv(self, **filters):
        stream=io.StringIO();stream.write('\ufeff')
        writer=csv.writer(stream);writer.writerow(['版本ID','数据版本','故障码','描述'])
        offset=0
        while True:
            page=self.faults(**filters,limit=5000,offset=offset)
            for r in page['items']:
                # Prevent spreadsheet formula execution in imported text.
                cells=[str(r[k] or '') for k in ('file_ver_id','version_name','code','desc')]
                writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in cells])
            offset+=len(page['items'])
            if offset>=page['total'] or not page['items']:
                break
        return stream.getvalue().encode('utf-8')

    def pack_path(self, vid, mid):
        return self.root/'map-packs'/f'{int(vid)}_{int(mid)}.json'

    def maps(self, vid, mid):
        with self.db() as db:
            if db.execute('select 1 from modules where id=? and file_ver_id=? and deleted_at=0',(mid,vid)).fetchone() is None:
                raise ValueError('模块不属于该版本')
        path=self.pack_path(vid,mid)
        if not path.is_file():
            return {'available':False,'items':[], 'message':'没有已解析的地图 JSON；可下载已有原始附件，但本项目不调用原厂后端解密'}
        maps=json.loads(path.read_text('utf-8'))
        if not isinstance(maps,list):
            raise ValueError('地图 JSON 格式错误')
        return {'available':True,'items':maps, 'message':'KP 数据快照，不代表已加载 BIN 的实际内容或地址映射'}

    def asset_path(self, mid, required=True):
        with self.db() as db:
            row=db.execute('select oss_full_name from modules where id=? and deleted_at=0',(mid,)).fetchone()
        if row and row[0]:
            name=str(row[0]).replace('\\','/').split('/')[-1]
            p=(self.root/'file-ver-mappacks'/name).resolve()
            parent=(self.root/'file-ver-mappacks').resolve()
            if p.parent==parent and p.is_file():
                return p
        if required:
            raise ValueError('附件不存在')
        return None

    def identify(self, data):
        upper=data.upper();matches=[]
        with self.db() as db:
            versions=db.execute('select id,name,required_ascii_keys,feature_code,feature_address from file_vers where deleted_at=0')
            for r in versions:
                keys=json.loads(r['required_ascii_keys'] or '[]')
                hits=[k for k in keys if isinstance(k,str) and k and k.encode('utf-8').upper() in upper]
                feature=False
                try:
                    pat=bytes.fromhex(r['feature_code'] or '')
                    off=int(str(r['feature_address']),16)
                    seg=data[off:off+len(pat)]
                    feature=bool(pat) and len(seg)==len(pat) and all(x==255 or x==y for x,y in zip(pat,seg))
                except (ValueError,TypeError):
                    pass
                if hits or feature:
                    matches.append({'id':r['id'],'name':r['name'],'keys':hits,'feature':feature,'score':len(hits)*10+40*feature})
        return sorted(matches,key=lambda x:-x['score'])[:10]


def preview_map(m):
    required=('zBit','zSigned','zLittleEndian','zFactor','zOffset','col','row','zDatas')
    warnings=[]
    if any(k not in m for k in required):
        warnings.append('地图定义缺字段，不猜测数据类型或字节序')
    cols,rows=int(m.get('col') or 1),int(m.get('row') or 1)
    if cols<=0 or rows<=0 or cols*rows>4096:
        warnings.append('维度无效或超过 4096 格，仅展示元数据')
    if int(m.get('lineSkipBytes') or 0):
        warnings.append('非零 lineSkipBytes 的快照布局未验证，仅展示元数据')
    kinds={'8':('b','B',1),'16':('h','H',2),'32':('i','I',4),'float32':('f','f',4)}
    kind=kinds.get(str(m.get('zBit')))
    if kind is None:
        warnings.append('未知数据类型')
    values=[]
    if not warnings:
        raw=bytes.fromhex(m['zDatas']);width=kind[2]
        if len(raw)<cols*rows*width:
            warnings.append('快照数据长度不足')
        else:
            fmt=('<' if m['zLittleEndian'] else '>')+kind[0 if m['zSigned'] else 1]
            factor,offset=float(m['zFactor']),float(m['zOffset'])
            for r in range(rows):
                line=[]
                for c in range(cols):
                    x=struct.unpack_from(fmt,raw,(r*cols+c)*width)[0]*factor+offset
                    line.append(round(x,6) if math.isfinite(x) else None)
                values.append(line)
    return {'name':m.get('name'),'metadata':{k:v for k,v in m.items() if k not in ('zDatas','xDatas','yDatas')},
            'values':values,'warnings':warnings,'source':'KP 数据快照（只读）','editable':False}


def preflight(data):
    strings=[{'offset':m.start(),'text':m[0].decode('ascii')} for m in re.finditer(rb'[ -~]{8,120}',data)][:60]
    return {'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'strings':strings,
            'address_mapping':'未确认；KP 地址不自动当作文件偏移',
            'warnings':['空文件'] if not data else [],'flashable':'未判定'}


def diff_report(a,b,limit=200):
    runs=[];total=0;count=0;start=None;n=max(len(a),len(b))
    for i in range(n+1):
        differs=i<n and (i>=len(a) or i>=len(b) or a[i]!=b[i])
        if differs:
            total+=1
            if start is None:start=i
        elif start is not None:
            count+=1
            if len(runs)<limit:
                runs.append({'offset':start,'length':i-start,'old':a[start:i][:32].hex(),'new':b[start:i][:32].hex()})
            start=None
    return {'size_a':len(a),'size_b':len(b),'sha256_a':hashlib.sha256(a).hexdigest(),
            'sha256_b':hashlib.sha256(b).hexdigest(),'changed_bytes':total,'range_count':count,
            'ranges':runs,'truncated':count>limit,'interpretation':'差异不自动解释为校验区或有效标定'}
