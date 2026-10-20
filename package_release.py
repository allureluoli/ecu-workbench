"""Build a complete project ZIP with the integrated dataset."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
SOURCE_FILES={'app.py','core.py','checksum.py','import_data.py','make_demo.py','package_release.py',
              'README.md','LICENSE','NOTICE.md','DATA_POLICY.md','requirements.txt','pyproject.toml',
              '.gitignore','.gitattributes','run.bat','run.sh'}
SOURCE_DIRS={'web','tests','docs'}


def public_files(root):
    files=[root/n for n in SOURCE_FILES if (root/n).is_file()]
    for folder in SOURCE_DIRS|{'data/demo','data/local'}:
        files.extend(p for p in (root/folder).rglob('*') if p.is_file() and
                     '__pycache__' not in p.parts and p.suffix!='.pyc')
    return sorted(files,key=lambda p:p.relative_to(root).as_posix())


def build_source(output,root=ROOT):
    output,root=Path(output),Path(root)
    if output.exists():raise ValueError('输出文件已存在，拒绝覆盖')
    files=public_files(root)
    expected=SOURCE_FILES-{'run.sh'}
    if not expected<={p.name for p in files}:raise ValueError('源码包缺少必要文件')
    for p in files:
        if p.is_symlink():raise ValueError('源码包不能包含符号链接')
        relative=p.relative_to(root).as_posix()
        if relative.startswith('state/'):raise ValueError('发布清单误含运行状态')
        if p.suffix in ('.py','.js','.json','.html'):
            text=p.read_text('utf-8')
            for marker in ('-----BEGIN '+'PRIVATE KEY-----','-----BEGIN RSA '+'PRIVATE KEY-----','SCZ'+'1.'):
                if marker in text:raise ValueError('源码清单发现不应发布的凭据或密钥：'+relative)
    inventory={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in files:z.write(p,'ecu-workbench-open/'+p.relative_to(root).as_posix())
        z.writestr('ecu-workbench-open/SOURCE_MANIFEST.json',json.dumps({'kind':'source-and-integrated-dataset',
            'files':inventory},ensure_ascii=False,indent=2))
    return {'path':str(output),'files':len(files)+1,'bytes':output.stat().st_size,
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


def build_local_data(output,root=ROOT):
    from core import Catalog
    data=Path(root)/'data/local';summary=Catalog(data).stats()
    output=Path(output)
    if output.exists():raise ValueError('输出文件已存在，拒绝覆盖')
    files=sorted(p for p in data.rglob('*') if p.is_file())
    allowed_suffix={'.db','.kp','.ecfg','.json'}
    if any(p.is_symlink() or p.suffix.lower() not in allowed_suffix for p in files):
        raise ValueError('本地包中存在非数据文件')
    inventory={p.relative_to(data).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for p in files:z.write(p,p.relative_to(data).as_posix())
        z.writestr('DATA_MANIFEST.json',json.dumps({'kind':'third-party-dataset',
            'redistribution':'License not specified; see DATA_POLICY.md',
            'summary':summary,'files':inventory},ensure_ascii=False,indent=2))
    return {'path':str(output),'files':len(files)+1,'bytes':output.stat().st_size,
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',type=Path,default=ROOT/'dist')
    parser.add_argument('--with-local-data',action='store_true')
    args=parser.parse_args();args.out_dir.mkdir(parents=True,exist_ok=True)
    result=[build_source(args.out_dir/'ecu-workbench-open.zip')]
    if args.with_local_data:result.append(build_local_data(args.out_dir/'ecu-data.zip'))
    (args.out_dir/'SHA256SUMS.txt').write_text('\n'.join(r['sha256']+'  '+Path(r['path']).name for r in result)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
