"""Read-only history audit; report labels and paths, never matched values."""
import importlib.util
import json
from pathlib import Path, PurePosixPath
import subprocess
import os

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

spec = importlib.util.spec_from_file_location('source_audit', 'scripts/audit-source.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
objects = {}
for line in subprocess.check_output(['git', 'rev-list', '--objects', 'main']).decode('utf-8').splitlines():
    oid, _, name = line.partition(' ')
    objects[oid] = name
metadata = subprocess.check_output(['git', 'cat-file', '--batch-check'], input=('\n'.join(objects)+'\n').encode()).decode().splitlines()
blobs = [line.split()[0] for line in metadata if line.split()[1] == 'blob']
process = subprocess.Popen(['git', 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
findings = []
text_count = 0
for oid in blobs:
    process.stdin.write((oid+'\n').encode())
    process.stdin.flush()
    header = process.stdout.readline().decode().split()
    raw = process.stdout.read(int(header[2]))
    assert process.stdout.read(1) == b'\n'
    name = objects[oid]
    path = PurePosixPath(name)
    lower = path.name.lower()
    issues = []
    if path.parts and (path.parts[0] in audit.PRIVATE_DIRECTORIES or '__pycache__' in path.parts
            or path.parts[:2] == ('desktop', 'bin') or lower == 'agents.local.md'
            or lower in {'auth.json', 'config.toml', 'library.json', 'migration-manifest.json'}
            or (lower.startswith('.env') and lower != '.env.example')
            or path.suffix.lower() in {'.pdf','.db','.sqlite3','.zip','.bak','.key','.pem','.p12','.pfx','.lnk'}):
        if name != 'assets/dictionaries/ecdict.sqlite3':
            issues.append('private data or generated artifact')
    if b'\0' not in raw:
        text_count += 1
        value = raw.decode('utf-8', errors='replace')
        issues.extend(label for label, pattern in audit.PATTERNS.items() if pattern.search(value))
    if issues:
        findings.append({'object': oid[:12], 'path': name, 'issues': issues})
process.stdin.close()
process.wait()
result = {'passed': not findings, 'branch': 'main', 'commits': int(subprocess.check_output(['git','rev-list','--count','main'])),
          'blobs': len(blobs), 'text_blobs': text_count, 'findings': findings}
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(bool(findings))
