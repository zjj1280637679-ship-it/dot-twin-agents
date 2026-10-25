#!/usr/bin/env python3
"""Deterministic, explicit allowlist build for the skill-only plugin."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MEMBERS = ('LICENSE', 'plugin.json', 'skills/dot-twin/SKILL.md')


def validate(source):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError('invalid_source')
    found = set()
    for path in source.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('nonregular_source')
        if stat.S_ISREG(mode):
            found.add(path.relative_to(source).as_posix())
    if found != set(MEMBERS):
        raise ValueError('unexpected_package_files')
    payload = {}
    for name in MEMBERS:
        path = source / name
        if path.stat().st_size > 32_768:
            raise ValueError('oversized_package_file')
        payload[name] = path.read_bytes()
    manifest = json.loads(payload['plugin.json'])
    if (set(manifest) != {'$schema', 'name', 'version', 'description'} or
        manifest['$schema'] != 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json' or
        manifest['name'] != 'dot-twin-agents' or manifest['version'] != '0.1.0' or
        type(manifest['description']) is not str or not manifest['description'].strip()):
        raise ValueError('invalid_manifest')
    skill = payload['skills/dot-twin/SKILL.md'].decode('utf-8')
    if not re.match(r'\A---\nname: dot-twin\ndescription: [^\n]+\n---\n', skill):
        raise ValueError('invalid_skill')
    text = '\n'.join(v.decode('utf-8') for v in payload.values())
    for marker in ('/workspace/', '/home/', '-----BEGIN PRIVATE KEY', '"access_token":'):
        if marker in text:
            raise ValueError('private_material_tripwire')
    if 'MIT License' not in payload['LICENSE'].decode('utf-8'):
        raise ValueError('missing_license')
    return payload


def build(source, output):
    payload = validate(source)
    output = Path(output)
    if output.resolve().is_relative_to(Path(source).resolve()):
        raise ValueError('output_inside_source')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in MEMBERS:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload[name], compresslevel=9)
    return {'filename': output.name, 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'members': list(MEMBERS)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'plugin')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/dot-twin-agents-0.1.0.zip')
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2))
