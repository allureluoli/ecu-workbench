"""Create synthetic, redistributable example data; no vehicle firmware included."""
import json
from pathlib import Path
import sqlite3
import zlib
from contextlib import closing


def make_demo(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    (root/'map-packs').mkdir(exist_ok=True)
    path=root/'ecu-mask.db'
    if path.exists():raise ValueError('演示数据库已存在，拒绝覆盖')
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
        create table file_ver_dirs(id integer primary key,name text,parent_id integer,deleted_at integer default 0);
        create table file_vers(id integer primary key,name text,directory_id integer,resolve_type text,
            feature_address text,feature_code text,required_ascii_keys text,deleted_at integer default 0);
        create table module_configs(id integer primary key,name text,module_type text,deleted_at integer default 0);
        create table modules(id integer primary key,file_ver_id integer,module_config_id integer,
            file_name text,oss_full_name text,size integer,deleted_at integer default 0);
        create table fault_codes(id integer primary key,file_ver_id integer,code text,desc text,deleted_at integer default 0);
        insert into file_ver_dirs(id,name,parent_id) values(1,'合成演示（非 ECU）',0);
        insert into file_vers(id,name,directory_id,resolve_type,feature_address,feature_code,required_ascii_keys)
            values(-1,'ECU-CK-DEMO!',1,'synthetic','0','45 43 55 2D 43 4B 2D 44 45 4D 4F 21','["ECU-CK-DEMO!"]');
        insert into module_configs(id,name,module_type) values(1,'合成地图快照','map_snapshot');
        insert into modules(id,file_ver_id,module_config_id,file_name,oss_full_name,size) values(1,-1,1,'合成演示 JSON','',0);
        insert into fault_codes(id,file_ver_id,code,desc) values(1,-1,'P0001','合成示例：用于查询与导出测试，不代表实际车辆诊断');
        ''')
    original=bytearray(range(128));original[:12]=b'ECU-CK-DEMO!'
    original[120:124]=zlib.crc32(original[16:96]).to_bytes(4,'little')
    edited=bytearray(original);edited[42]^=0x23
    (root/'original.bin').write_bytes(original);(root/'edited.bin').write_bytes(edited)
    profile={'schema':1,'profiles':[{'id':'synthetic-demo','name':'合成 CRC32 演示（非 ECU）',
        'version':-1,'size':128,'anchors':[{'offset':0,'hex':original[:12].hex()}],
        'evidence':'独立 stdlib CRC32 计算的合成夹具，不代表任何 ECU 支持',
        'blocks':[{'id':'calibration','algorithm':'crc32','regions':[[16,96]],'store_offset':120,'byteorder':'little'}]}]}
    (root/'checksum_profiles.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    maps=[{'name':'合成数值表（非 ECU 地图）','zAddress':'10','col':8,'row':5,'zBit':'16',
        'zSigned':False,'zLittleEndian':True,'zFactor':0.1,'zOffset':0,'lineSkipBytes':0,
        'zDatas':bytes(original[16:96]).hex(),'zIdName':'demo_table'}]
    (root/'map-packs/-1_1.json').write_text(json.dumps(maps,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'manifest.json').write_text(json.dumps({'name':'可再分发合成演示数据','synthetic':True,
        'redistribution':'MIT；由 make_demo.py 独立生成'},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    make_demo(Path(__file__).resolve().parent/'data/demo')
