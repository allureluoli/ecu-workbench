"""Import a local catalog and assets without vendor binaries or embedded keys."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing

from core import Catalog

TABLES={'file_vers','file_ver_dirs','modules','module_configs','fault_codes','key_infos','key_info_categories'}


def locate(source):
    source=Path(source).resolve()
    for candidate in (source,source/'resources/data',source/'安装/resources/data',source/'data/local'):
        if (candidate/'ecu-mask.db').is_file():return candidate
    raise ValueError('没有找到 ecu-mask.db，支持数据目录、安装目录或项目根目录')


def safe_unzip(source,destination):
    with zipfile.ZipFile(source) as z:
        total=0;seen=set()
        for item in z.infolist():
            name=item.filename.replace('\\','/')
            parts=Path(name).parts
            if (name.startswith('/') or '..' in parts or ':' in name or
                    (item.external_attr>>16)&0o170000==0o120000):
                raise ValueError('数据包包含不安全路径或符号链接')
            folded=name.casefold()
            if folded in seen:raise ValueError('数据包包含重复文件名')
            seen.add(folded);total+=item.file_size
            if total>2*1024**3:raise ValueError('解压后数据包超过 2 GiB')
        z.extractall(destination)


def import_dataset(source,destination,maps_dir=None):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if destination.exists():raise ValueError('目标目录已存在；请指定一个新的目录，避免覆盖本地数据')
    with tempfile.TemporaryDirectory(prefix='ecu-import-') as temporary:
        unpack=Path(temporary)/'unpacked'
        if source.is_file():
            unpack.mkdir();safe_unzip(source,unpack);source=unpack
        data=locate(source);Catalog(data)
        maps=Path(maps_dir).resolve() if maps_dir else data/'map-packs'
        stage=Path(temporary)/'staged';stage.mkdir()
        with closing(sqlite3.connect((data/'ecu-mask.db').as_uri()+'?mode=ro',uri=True)) as src:
            names={r[0] for r in src.execute("select name from sqlite_master where type='table'")}
            if not names<=TABLES:
                raise ValueError('数据库含非目录数据表，请先清理后再导入：'+','.join(sorted(names-TABLES)))
            with closing(sqlite3.connect(stage/'ecu-mask.db')) as dst:src.backup(dst)
        assets=stage/'file-ver-mappacks';assets.mkdir()
        for p in sorted((data/'file-ver-mappacks').glob('*')):
            if p.is_symlink():raise ValueError('不导入符号链接附件')
            if p.is_file() and p.suffix.lower() in ('.kp','.ecfg'):
                shutil.copyfile(p,assets/p.name)
        decoded=stage/'map-packs';decoded.mkdir()
        for p in sorted(maps.glob('*.json')):
            if p.is_symlink():raise ValueError('不导入符号链接地图')
            import re
            if not re.fullmatch(r'-?\d+_-?\d+\.json',p.name):continue
            parsed=json.loads(p.read_text('utf-8'))
            if not isinstance(parsed,list):raise ValueError('地图 JSON 不是数组：'+p.name)
            shutil.copyfile(p,decoded/p.name)
        profile=data/'checksum_profiles.json'
        if profile.exists():
            import checksum
            normalized=checksum.validate_catalog(json.loads(profile.read_text('utf-8')))
        else:normalized={'schema':1,'profiles':[]}
        (stage/'checksum_profiles.json').write_text(json.dumps(normalized,ensure_ascii=False,indent=2),encoding='utf-8')
        manifest={'name':'ECU 集成数据集','synthetic':False,
            'redistribution':'未提供明确的数据许可证；不适用工具代码的 MIT 许可证',
            'imported_at':datetime.now().astimezone().isoformat(),'source_database_sha256':hashlib.sha256((data/'ecu-mask.db').read_bytes()).hexdigest()}
        (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        # Validate the resulting self-contained catalog before copying to its final location.
        summary=Catalog(stage).stats()
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(stage,destination)
        return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--destination',type=Path,default=Path(__file__).resolve().parent/'data/local')
    parser.add_argument('--maps-dir',type=Path)
    args=parser.parse_args()
    print(json.dumps(import_dataset(args.source,args.destination,args.maps_dir),ensure_ascii=False,indent=2))
