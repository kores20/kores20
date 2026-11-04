"""Update application code from GitHub while preserving orders and PDF originals."""
import argparse
import io
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

REPOSITORY='kores20/kores20'
ROOT_FILES={'requirements.txt','README.md','LOCAL-START.txt','run_local.py','run-windows.bat','run-mac.command','update_local.py','update-windows.bat','Dockerfile','compose.yaml','.gitignore','.dockerignore'}

def apply_archive(raw, root):
    root=Path(root).resolve();stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f');backup=root/'.updates'/stamp
    changes=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for member in archive.infolist():
            parts=PurePosixPath(member.filename).parts
            if member.is_dir() or len(parts)<2:continue
            relative=PurePosixPath(*parts[1:])
            if '..' in relative.parts or relative.is_absolute():raise ValueError('Invalid archive path')
            path=str(relative)
            if path.startswith('app/assets/templates/') or any(p in {'.data','.venv','.git','.env','.updates','__pycache__'} for p in relative.parts):continue
            if not (relative.parts[0] in {'app','tests'} or path in ROOT_FILES):continue
            if member.file_size>5_000_000:raise ValueError('Unexpected large code file')
            target=root/Path(*relative.parts)
            if root not in target.resolve().parents:raise ValueError('Invalid target path')
            contents=archive.read(member)
            if target.exists() and target.read_bytes()==contents:continue
            changes.append((target,relative,contents))
        if not any(str(x[1])=='app/server.py' for x in changes) and not (root/'app/server.py').exists():raise ValueError('The archive does not contain the website')
    if not changes:return 0
    applied=[]
    try:
        for target,relative,contents in changes:
            old=backup/Path(*relative.parts)
            if target.exists():old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,old)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(contents);applied.append((target,old))
    except Exception:
        for target,old in reversed(applied):
            if old.exists():shutil.copy2(old,target)
            elif target.exists():target.unlink()
        raise
    return len(changes)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,help='Optional downloaded GitHub ZIP');args=parser.parse_args()
    root=Path(__file__).resolve().parent
    try:
        if args.archive:raw=args.archive.read_bytes()
        else:
            headers={'User-Agent':'Namothaj-Updater','Accept':'application/vnd.github+json'}
            token=os.getenv('GITHUB_TOKEN')
            if token:headers['Authorization']='Bearer '+token
            req=urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/zipball/main',headers=headers)
            with urllib.request.urlopen(req,timeout=60) as response:raw=response.read(20_000_001)
            if len(raw)>20_000_000:raise ValueError('Archive exceeds the update size limit')
        changed=apply_archive(raw,root)
        print(f'Updated {changed} files. Your .data, .venv and original PDF templates were preserved.')
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','-r',str(root/'requirements.txt')],check=True,cwd=root)
        print('Update complete. Start run-windows.bat again.')
    except urllib.error.HTTPError as exc:
        print(f'GitHub access failed ({exc.code}). For a private repository, use an authenticated GITHUB_TOKEN locally, or download Code > ZIP from GitHub and run:')
        print('python update_local.py --archive "path-to-downloaded.zip"');return 1
    except Exception as exc:print('Update failed:',str(exc));return 1
    return 0

if __name__=='__main__':sys.exit(main())
