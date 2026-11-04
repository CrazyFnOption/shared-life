"""Fixed Git inputs, conservative compatibility policy and safe release bundles."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import tomllib


class ReleaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(path)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE)


def ignored_location(repo, path):
    path = Path(path).resolve()
    try:
        relative = path.relative_to(repo.resolve())
    except ValueError:
        return
    require(subprocess.run(['git', '-C', str(repo), 'check-ignore', '-q', str(relative)],
                           capture_output=True).returncode == 0,
            'Private config/output must be outside Git or in a Git-ignored location')


def config_load(repo, path):
    path = Path(path).resolve()
    ignored_location(repo, path)
    require(path.is_file() and not path.is_symlink(), 'Config must be a regular local file')
    require(path.stat().st_mode & 0o077 == 0, 'Private config permissions must be 0600')
    config = read_json(path)
    require(config.get('version') == 1, 'Unsupported release config version')
    for name in ('wheelhouse',):
        p = Path(config[name]).expanduser()
        config[name] = str((path.parent / p).resolve() if not p.is_absolute() else p.resolve())
    return config


def safe_name(name):
    p = PurePosixPath(name)
    require(bool(name) and not p.is_absolute() and '..' not in p.parts and '\\' not in name
            and str(p) == name and not any(ord(c) < 32 for c in name), 'Unsafe archive path: ' + name)
    forbidden = {'.git', '.local', '.env', 'credentials.json', 'compose.trial.yaml',
                 'node_modules', '__pycache__', 'dist', 'backups', 'runtime-secrets'}
    require(not forbidden.intersection(p.parts) and not any(v.startswith('.env.') for v in p.parts) and not name.endswith(('.pem', '.key', '.db', '.sqlite', '.sqlite3')),
            'Private/build data rejected: ' + name)


def source_path(name):
    if name in {'.dockerignore', 'Dockerfile', 'compose.yaml', 'deploy/requirements.txt',
                'pyproject.toml', 'uv.lock'}:
        return True
    if name.startswith('server/tests/') or (name.startswith('server/') and PurePosixPath(name).name.startswith('test_')
                                               and name.endswith('.py')):
        return False
    if name.startswith('scripts/release'):
        return False  # Release tooling is not application runtime.
    return name.startswith(('server/', 'scripts/', 'web/'))


def ui_path(name):
    if name.startswith('web/src/'):
        return name.endswith(('.vue', '.css'))
    if name.startswith('web/public/'):
        return name.endswith(('.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico'))
    return name == 'web/index.html' or name.startswith('web/tests/')


def tree(repo, commit):
    result = {}
    entries = git(repo, 'ls-tree', '-rz', '--full-tree', commit).split(b'\0')
    for entry in filter(None, entries):
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        name = raw_name.decode()
        if source_path(name):
            safe_name(name)
            require(kind == 'blob' and mode in {'100644', '100755'}, 'Symlinks/submodules rejected: ' + name)
            result[name] = (git(repo, 'cat-file', 'blob', oid), int(mode[-3:], 8))
    require({'Dockerfile', '.dockerignore', 'deploy/requirements.txt', 'web/package-lock.json',
             'scripts/web.sh'}.issubset(result), 'Missing required source/build inputs')
    return result


def version_only(name, before, after):
    """Only the application's own metadata version; dependency resolution stays exact."""
    if not before or not after or before[1] != after[1]:
        return False
    try:
        a, b = ((json.loads(v[0]) if name.endswith('.json') else tomllib.loads(v[0].decode()))
                for v in (before, after))
        def strip(value):
            if name == 'pyproject.toml':
                require(value['project']['name'] == 'shared-life', 'Unexpected project identity')
                versions = [value['project'].pop('version')]
            elif name == 'uv.lock':
                own = [v for v in value['package'] if v['name'] == 'shared-life' and v.get('source') == {'virtual': '.'}]
                require(len(own) == 1, 'Expected exactly one local project in uv.lock')
                versions = [own[0].pop('version')]
            else:
                require(value['name'] == 'shared-life-web', 'Unexpected web package identity')
                versions = [value.pop('version')]
                if name == 'web/package-lock.json':
                    require(value['packages']['']['name'] == 'shared-life-web', 'Unexpected root package')
                    versions.append(value['packages'][''].pop('version'))
            require(all(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?', v) for v in versions),
                    'Invalid own project version')
            require(len(set(versions)) == 1, 'Package and root lock versions disagree')
            return value
        return strip(a) == strip(b)
    except (ValueError, KeyError, TypeError, ReleaseError):
        return False


REVIEWABLE_RUNTIME_PATHS = frozenset({'server/memos/service.py', 'server/plans/service.py',
                                     'server/memos/migration_guard.py'})


def reviewed_changes(before, after, config=None, baseline=None, commit=None):
    """Carry exact independently reviewed service fixes; config is evidence, never authority."""
    records = (config or {}).get('reviewed_runtime_changes', [])
    require(isinstance(records, list), 'reviewed_runtime_changes must be a list of exact reviewed records')
    seen = set()
    expected_fields = {'baseline_commit', 'candidate_commit', 'path', 'old_sha256', 'new_sha256', 'mode'}
    for record in records:
        require(isinstance(record, dict) and set(record) == expected_fields, 'Incomplete/unknown reviewed runtime fields')
        require(all(isinstance(record[field], str) for field in expected_fields), 'Reviewed runtime fields must be exact strings')
        name = record['path']
        require(isinstance(name, str) and name in REVIEWABLE_RUNTIME_PATHS and name not in seen,
                'Reviewed runtime path is not one exact supported runtime file, or is duplicated')
        seen.add(name)
        if name == 'server/memos/migration_guard.py':
            require(config.get('schema') == 9 and config.get('backup_format') == 2,
                    'Reviewed legacy guard exception requires existing schema9/format2; no migration')
        require(re.fullmatch(r'[0-9a-f]{40}', record['baseline_commit'] or '') and
                re.fullmatch(r'[0-9a-f]{40}', record['candidate_commit'] or '') and
                record['baseline_commit'] == baseline and record['candidate_commit'] == commit,
                'Reviewed runtime baseline/candidate commit mismatch: ' + name)
        require(name in before and name in after and before[name][1] == after[name][1] and
                record['mode'] == '100' + format(before[name][1], '03o'),
                'Reviewed runtime file added/deleted or mode differs: ' + name)
        require(re.fullmatch(r'[0-9a-f]{64}', record['old_sha256'] or '') and
                re.fullmatch(r'[0-9a-f]{64}', record['new_sha256'] or '') and
                record['old_sha256'] != record['new_sha256'] and
                sha(before[name][0]) == record['old_sha256'] and sha(after[name][0]) == record['new_sha256'],
                'Reviewed runtime exact old/new SHA mismatch: ' + name)
    return [dict(record) for record in sorted(records, key=lambda item: item['path'])]


FRONTEND_PACKAGE_FILES = frozenset({'web/package.json', 'web/package-lock.json'})
EXACT_VERSION = r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?'


def frontend_additions_only(before, after):
    """Prove additive npm declarations/lock records; preserve all existing metadata and resolution."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key in reviewed frontend package inputs')
            result[key] = value
        return result
    package_before, package_after = [json.loads(tree['web/package.json'][0], object_pairs_hook=unique)
                                     for tree in (before, after)]
    lock_before, lock_after = [json.loads(tree['web/package-lock.json'][0], object_pairs_hook=unique)
                               for tree in (before, after)]
    added = {}
    for group in ('dependencies', 'devDependencies'):
        old, new = package_before.get(group, {}), package_after.get(group, {})
        require(isinstance(old, dict) and isinstance(new, dict) and
                all(name in new and new[name] == value for name, value in old.items()),
                'Existing declared frontend dependency changed/removed: ' + group)
        additions = {name: value for name, value in new.items() if name not in old}
        require(all(isinstance(value, str) and re.fullmatch(EXACT_VERSION, value) for value in additions.values()),
                'New frontend dependencies must use exact package versions')
        added[group] = additions
    require(any(added.values()), 'Reviewed frontend addition record contains no new declared dependency')
    old_names = set(package_before.get('dependencies', {})) | set(package_before.get('devDependencies', {}))
    new_names = list(added['dependencies']) + list(added['devDependencies'])
    require(len(set(new_names)) == len(new_names) and not old_names.intersection(new_names),
            'Frontend dependency addition duplicates an existing declaration/group')
    for package, lock in ((package_before, lock_before), (package_after, lock_after)):
        require(package.get('name') == lock.get('name') == 'shared-life-web' and lock.get('lockfileVersion') == 3,
                'Unexpected frontend package identity/lock format')
        require(isinstance(lock.get('packages'), dict) and '' in lock['packages'], 'Missing npm root lock record')
        root = lock['packages']['']
        require(root.get('name') == package['name'] and package.get('version') == lock.get('version') == root.get('version')
                and isinstance(package.get('version'), str) and re.fullmatch(EXACT_VERSION, package['version']),
                'Frontend package/root lock project versions disagree')
        for group in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'peerDependenciesMeta'):
            require(package.get(group, {}) == root.get(group, {}), 'Frontend package/root lock declarations disagree: ' + group)
    for old, new in ((package_before, package_after), (lock_before['packages'][''], lock_after['packages'][''])):
        strip = lambda value: {k: v for k, v in value.items() if k not in {'version', 'dependencies', 'devDependencies'}}
        require(strip(old) == strip(new), 'Frontend scripts/name/engines/other metadata changed')
    strip_lock = lambda value: {k: v for k, v in value.items() if k not in {'version', 'packages'}}
    require(strip_lock(lock_before) == strip_lock(lock_after), 'Frontend lock metadata changed')
    old_packages, new_packages = lock_before['packages'], lock_after['packages']
    require(all(name in new_packages and new_packages[name] == value for name, value in old_packages.items() if name),
            'Existing frontend lock package record changed/removed')
    new_records = sorted(new_packages.keys() - old_packages.keys())
    require(bool(new_records), 'No new frontend lock package records')
    for name in new_records:
        record = new_packages[name]
        path = PurePosixPath(name)
        require(name.startswith('node_modules/') and not path.is_absolute() and '..' not in path.parts and str(path) == name,
                'Unsupported frontend lock package path')
        require(isinstance(record, dict) and not record.get('link') and
                isinstance(record.get('version'), str) and re.fullmatch(EXACT_VERSION, record['version']) and
                isinstance(record.get('resolved'), str) and record['resolved'].startswith('https://registry.npmjs.org/') and
                isinstance(record.get('integrity'), str) and re.fullmatch(r'sha(?:256|384|512)-[A-Za-z0-9+/]+=*', record['integrity']),
                'New frontend lock record must be an exact registry package with integrity')
    for dependencies in added.values():
        for name, version in dependencies.items():
            require('node_modules/' + name in new_packages and
                    new_packages['node_modules/' + name].get('version') == version,
                    'New frontend declaration does not match exact lock version: ' + name)
    return {'added_dependencies': added, 'added_lock_records': new_records}


def reviewed_frontend_additions(before, after, config=None, baseline=None, commit=None):
    """Bind one independent review of both package files; never bypass semantic comparison."""
    records = (config or {}).get('reviewed_frontend_dependency_additions', [])
    require(isinstance(records, list) and len(records) <= 1, 'Expected zero or one frontend dependency review')
    if not records:
        return []
    record = records[0]
    require(isinstance(record, dict) and set(record) == {'baseline_commit', 'candidate_commit', 'files'},
            'Incomplete/unknown frontend dependency review fields')
    require(all(isinstance(record[k], str) and re.fullmatch(r'[0-9a-f]{40}', record[k])
                for k in ('baseline_commit', 'candidate_commit')) and
            record['baseline_commit'] == baseline and record['candidate_commit'] == commit,
            'Reviewed frontend baseline/candidate commit mismatch')
    require(isinstance(record['files'], dict) and set(record['files']) == FRONTEND_PACKAGE_FILES,
            'Frontend dependency review must bind exactly both web package files')
    for name, proof in record['files'].items():
        require(isinstance(proof, dict) and set(proof) == {'old_sha256', 'new_sha256', 'mode'} and
                all(isinstance(value, str) for value in proof.values()), 'Incomplete frontend file SHA/mode proof')
        require(name in before and name in after and before[name][1] == after[name][1] and
                proof['mode'] == '100' + format(before[name][1], '03o'), 'Frontend package file mode/add/delete changed')
        require(all(re.fullmatch(r'[0-9a-f]{64}', proof[k]) for k in ('old_sha256', 'new_sha256')) and
                proof['old_sha256'] != proof['new_sha256'] and sha(before[name][0]) == proof['old_sha256'] and
                sha(after[name][0]) == proof['new_sha256'], 'Reviewed frontend exact old/new SHA mismatch: ' + name)
    semantics = frontend_additions_only(before, after)
    return [{**record, **semantics}]


def compatibility(before, after, *, config=None, baseline=None, commit=None):
    changed = sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))
    reviewed = {record['path'] for record in reviewed_changes(before, after, config, baseline, commit)}
    if reviewed_frontend_additions(before, after, config, baseline, commit):
        reviewed.update(FRONTEND_PACKAGE_FILES)
    version_files = {'pyproject.toml', 'uv.lock', 'web/package.json', 'web/package-lock.json'}
    blocked = [n for n in changed if n not in reviewed and not ui_path(n) and not
               (n in version_files and version_only(n, before.get(n), after.get(n)))]
    require(not blocked, 'Incompatible release: backend/DDL/dependency/storage/build inputs changed: '
            + ', '.join(blocked) + '. This CLI never runs migrations; use a separately reviewed procedure.')
    return changed


def pack(path, files):
    # Reproducible gzip and tar; no symlinks, owner metadata or untracked files.
    with Path(path).open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for name, (data, mode) in sorted(files.items()):
                safe_name(name)
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(data), mode, 0
                archive.addfile(member, io.BytesIO(data))


def unpack_checked(path, hashes):
    files = {}
    with tarfile.open(path) as archive:
        for member in archive:
            safe_name(member.name)
            require(member.isfile() and member.name not in files, 'Non-file or duplicate archive member')
            data = archive.extractfile(member).read()
            files[member.name] = (data, member.mode)
    require({n: sha(d) for n, (d, _) in files.items()} == hashes, 'Archive contents differ from manifest')
    return files


def offline_context(files, config):
    files = dict(files)
    wheels = Path(config['wheelhouse'])
    manifest = read_json(wheels / 'wheelhouse-manifest.json')
    require(sha(files['deploy/requirements.txt'][0]) == manifest['requirements_sha256'],
            'Runtime requirements differ from offline wheelhouse; rebuild/review wheels separately')
    original = files['Dockerfile'][0].decode()
    pinned = original
    images = config['base_images']
    for image in ('node:24-bookworm-slim', 'python:3.13-slim-bookworm'):
        digest = images.get(image, '')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', digest), 'Base image digest required: ' + image)
        require(pinned.count('FROM ' + image) == 1, 'Unexpected Dockerfile base: ' + image)
        pinned = pinned.replace('FROM ' + image, 'FROM ' + image + '@' + digest)
    needle = 'RUN pip install --no-cache-dir --require-hashes -r deploy/requirements.txt \\\n'
    require(pinned.count(needle) == 1, 'Offline Dockerfile transform no longer matches; review build inputs')
    replacement = ('COPY offline-wheelhouse/ /opt/build-wheels/\n'
                   'RUN pip install --no-cache-dir --no-index --find-links=/opt/build-wheels/artifacts '
                   '--require-hashes -r /opt/build-wheels/requirements.install.txt \\\n'
                   '    && rm -rf /opt/build-wheels \\\n')
    files['Dockerfile.offline'] = (pinned.replace(needle, replacement).encode(), 0o644)
    ignore = files['.dockerignore'][0] + (b'\n!offline-wheelhouse/\n!offline-wheelhouse/artifacts/\n'
        b'!offline-wheelhouse/artifacts/*.whl\n!offline-wheelhouse/requirements.install.txt\n')
    files['Dockerfile.offline.dockerignore'] = (ignore, 0o644)
    install = (wheels / 'requirements.install.txt').read_bytes()
    locked = lambda text: dict(re.findall(r'^([a-zA-Z0-9_.-]+)==([^\s\\]+)', text, re.M))
    require(locked(install.decode()) == locked(files['deploy/requirements.txt'][0].decode()),
            'Offline install lock package/version set differs from runtime requirements')
    install_hashes = set(re.findall(r'--hash=sha256:([0-9a-f]{64})', install.decode()))
    seen = set()
    for item in manifest['artifacts']:
        name = item['file']
        safe_name(name)
        require(re.fullmatch(r'artifacts/[^/]+\.whl', name) and name not in seen,
                'Invalid/duplicate offline wheel path')
        seen.add(name)
        p = wheels / name
        require(not p.is_symlink() and p.resolve().is_relative_to(wheels.resolve()), 'Wheel symlink rejected')
        data = p.read_bytes()
        require(sha(data) == item['verified_sha256'] and sha(data) in install_hashes,
                'Offline wheel hash/install lock mismatch: ' + name)
        files['offline-wheelhouse/' + name] = (data, 0o644)
    require(bool(seen), 'Empty wheelhouse')
    files['offline-wheelhouse/requirements.install.txt'] = (install, 0o644)
    return files


def prepare(repo, commit, baseline, config, output):
    commit = git(repo, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    baseline = git(repo, 'rev-parse', '--verify', baseline + '^{commit}').decode().strip()
    require(config['baseline']['commit'] == baseline, 'Baseline must match private config verified deployment')
    before, files = tree(repo, baseline), tree(repo, commit)
    changed = compatibility(before, files, config=config, baseline=baseline, commit=commit)
    reviewed = reviewed_changes(before, files, config, baseline, commit)
    frontend_reviewed = reviewed_frontend_additions(before, files, config, baseline, commit)
    output = Path(output).resolve()
    ignored_location(repo, output)
    require(not output.exists(), 'Output already exists; use plan/status, do not overwrite release evidence')
    output.mkdir(parents=True, mode=0o700)
    # Persist the immutable input identity first so failed prepare cannot be mistaken for complete.
    write_json(output / 'prepare-state.json', {'status': 'preparing', 'commit': commit, 'baseline': baseline})
    pack(output / 'source.tar.gz', files)
    hashes = {n: sha(d) for n, (d, _) in files.items()}
    unpack_checked(output / 'source.tar.gz', hashes)
    with tempfile.TemporaryDirectory(prefix='shared-life-build-') as temporary:
        stage = Path(temporary)
        for name, (data, mode) in files.items():
            p = stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            p.chmod(mode)
        with (output / 'local-build.log').open('wb') as log:
            # The exported candidate gets its own dependency tree, uses npm cache, and never touches working dist.
            for command in (['sh', 'scripts/web.sh', 'ci', '--offline', '--no-audit', '--no-fund'],
                            ['sh', 'scripts/web.sh', 'run', 'build']):
                result = subprocess.run(command, cwd=stage, stdout=log, stderr=subprocess.STDOUT)
                require(result.returncode == 0, 'Fixed-candidate web build failed; see local-build.log')
        dist = stage / 'web/dist'
        runtime = {str(p.relative_to(dist)): sha(p.read_bytes()) for p in dist.rglob('*') if p.is_file()}
        require('index.html' in runtime and 'sw.js' in runtime, 'Incomplete PWA runtime build')
    context = offline_context(files, config)
    pack(output / 'context.tar.gz', context)
    context_hashes = {n: sha(d) for n, (d, _) in context.items()}
    unpack_checked(output / 'context.tar.gz', context_hashes)
    manifest = {'version': 1, 'commit': commit, 'baseline': baseline, 'changed_files': changed,
                'reviewed_runtime_changes': reviewed, 'reviewed_frontend_dependency_additions': frontend_reviewed,
                'source_files': hashes, 'baseline_files': {n: sha(d) for n, (d, _) in before.items()},
                'runtime': runtime, 'context_files': context_hashes,
                'source_sha256': sha((output / 'source.tar.gz').read_bytes()),
                'context_sha256': sha((output / 'context.tar.gz').read_bytes()),
                'config_sha256': sha(json.dumps(config, sort_keys=True).encode()),
                'schema': config['schema'], 'backup_format': config['backup_format'],
                'release_id': commit[:12] + '-' + sha((output / 'context.tar.gz').read_bytes())[:12]}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'prepare-state.json', {'status': 'prepared', 'commit': commit, 'baseline': baseline})
    return manifest


def verify_bundle(output, config, repo):
    output = Path(output)
    manifest = read_json(output / 'manifest.json')
    require(manifest['config_sha256'] == sha(json.dumps(config, sort_keys=True).encode()),
            'Config changed since prepare; prepare a new output for the new target')
    for filename, key in [('source.tar.gz', 'source'), ('context.tar.gz', 'context')]:
        require(sha((output / filename).read_bytes()) == manifest[key + '_sha256'], 'Bundle hash mismatch: ' + filename)
        unpack_checked(output / filename, manifest[key + '_files'])
    require(manifest['version'] == 1, 'Unsupported manifest version')
    before, after = tree(repo, manifest['baseline']), tree(repo, manifest['commit'])
    require(compatibility(before, after, config=config, baseline=manifest['baseline'], commit=manifest['commit'])
            == manifest['changed_files'], 'Fixed Git compatibility proof differs')
    require(reviewed_changes(before, after, config, manifest['baseline'], manifest['commit'])
            == manifest['reviewed_runtime_changes'], 'Manifest reviewed runtime proof differs')
    require(reviewed_frontend_additions(before, after, config, manifest['baseline'], manifest['commit'])
            == manifest['reviewed_frontend_dependency_additions'], 'Manifest reviewed frontend dependency proof differs')
    require({n: sha(d) for n, (d, _) in after.items()} == manifest['source_files'] and
            {n: sha(d) for n, (d, _) in before.items()} == manifest['baseline_files'],
            'Manifest source/baseline differs from fixed Git blobs')
    return manifest
