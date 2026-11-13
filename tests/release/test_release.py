"""Offline release-policy and failure/resume tests; never contacts a NAS."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from release.core import ReleaseError, compatibility, offline_context, pack, safe_name, sha, unpack_checked, version_only, source_path, prepare, verify_bundle, tree, reviewed_frontend_additions, frontend_additions_only
from release.runner import Runner, STAGES

REPO = Path(__file__).resolve().parents[2]


def blob(value):
    return (value.encode() if isinstance(value, str) else json.dumps(value).encode(), 0o644)


def config():
    c = json.loads((REPO / 'scripts/release/config.example.json').read_text())
    c['baseline']['commit'] = '1' * 40
    c['baseline']['image_id'] = 'sha256:' + '1' * 64
    return c


def manifest():
    return {'commit': '2' * 40, 'baseline': '1' * 40, 'release_id': 'test-release',
            'schema': 9, 'backup_format': 2, 'source_files': {}, 'runtime': {'index.html': 'a' * 64},
            'context_sha256': 'b' * 64}


class PolicyTests(unittest.TestCase):
    def test_backend_protocol_lock_and_new_runtime_scripts_block(self):
        for name in ('server/main.py', 'server/migrations/010.sql', 'web/src/store.ts', 'web/src/sync.ts',
                     'web/scripts/build-sw.mjs', 'deploy/requirements.txt', 'scripts/backup.py', 'Dockerfile'):
            with self.subTest(name=name), self.assertRaises(ReleaseError):
                compatibility({name: blob('old')}, {name: blob('new')})
        self.assertEqual(compatibility({'web/src/App.vue': blob('a')},
                                       {'web/src/App.vue': blob('b')}), ['web/src/App.vue'])

    def test_only_local_project_version_semantic_changes(self):
        cases = {
            'pyproject.toml': ('[project]\nname="shared-life"\nversion="0.1.0"\ndependencies=["x==1"]\n',
                               '[project]\nname="shared-life"\nversion="1.0.0"\ndependencies=["x==1"]\n'),
            'uv.lock': ('version=1\n[[package]]\nname="shared-life"\nversion="0.1.0"\nsource={virtual="."}\n'
                        '[[package]]\nname="x"\nversion="1"\n',
                        'version=1\n[[package]]\nname="shared-life"\nversion="1.0.0"\nsource={virtual="."}\n'
                        '[[package]]\nname="x"\nversion="1"\n'),
            'web/package.json': ({'name': 'shared-life-web', 'version': '0.1.0', 'dependencies': {'x': '1'}},
                                 {'name': 'shared-life-web', 'version': '1.0.0', 'dependencies': {'x': '1'}}),
            'web/package-lock.json': ({'name': 'shared-life-web', 'version': '0.1.0', 'packages': {
                '': {'name': 'shared-life-web', 'version': '0.1.0'}, 'node_modules/x': {'version': '1', 'integrity': 'fixed'}}},
                {'name': 'shared-life-web', 'version': '1.0.0', 'packages': {
                '': {'name': 'shared-life-web', 'version': '1.0.0'}, 'node_modules/x': {'version': '1', 'integrity': 'fixed'}}}),
        }
        for name, (before, after) in cases.items():
            with self.subTest(name=name):
                self.assertTrue(version_only(name, blob(before), blob(after)))
                compatibility({name: blob(before)}, {name: blob(after)})
                if isinstance(after, str):
                    bad = after.replace('x==1', 'x==2').replace('name="x"\nversion="1"', 'name="x"\nversion="2"')
                else:
                    bad = copy.deepcopy(after)
                    if 'dependencies' in bad:
                        bad['dependencies']['x'] = '2'
                    else:
                        bad['packages']['node_modules/x']['integrity'] = 'changed'
                self.assertFalse(version_only(name, blob(before), blob(bad)))
                with self.assertRaises(ReleaseError):
                    compatibility({name: blob(before)}, {name: blob(bad)})

    def test_archive_rejects_private_paths_traversal_links_and_duplicates(self):
        for name in ('../escape', '/absolute', 'web/.env.production', 'server/credentials.json',
                     'web/node_modules/x', 'a/../b', 'x\\y', 'server/ledger.sqlite3'):
            with self.subTest(name=name), self.assertRaises(ReleaseError):
                safe_name(name)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'archive.tar.gz'
            for kind in ('symlink', 'duplicate', 'traversal'):
                with tarfile.open(path, 'w:gz') as archive:
                    name = '../escape' if kind == 'traversal' else 'web/index.html'
                    member = tarfile.TarInfo(name)
                    if kind == 'symlink':
                        member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
                        archive.addfile(member)
                    else:
                        member.size = 1
                        archive.addfile(member, io.BytesIO(b'x'))
                        if kind == 'duplicate':
                            archive.addfile(member, io.BytesIO(b'x'))
                with self.subTest(kind=kind), self.assertRaises(ReleaseError):
                    unpack_checked(path, {'web/index.html': sha(b'x')})
            pack(path, {'web/index.html': blob('safe')})
            self.assertEqual(unpack_checked(path, {'web/index.html': sha(b'safe')})['web/index.html'][0], b'safe')
            with self.assertRaises(ReleaseError):
                unpack_checked(path, {'web/index.html': sha(b'changed')})

    def test_offline_wheel_lock_rejects_tamper_and_dependency_addition(self):
        with tempfile.TemporaryDirectory() as temporary:
            wheels = Path(temporary)
            (wheels / 'artifacts').mkdir()
            (wheels / 'artifacts/x.whl').write_bytes(b'wheel')
            requirements = b'x==1 --hash=sha256:' + sha(b'wheel').encode() + b'\n'
            (wheels / 'requirements.install.txt').write_bytes(requirements)
            (wheels / 'wheelhouse-manifest.json').write_text(json.dumps({
                'requirements_sha256': sha(requirements), 'artifacts': [
                    {'file': 'artifacts/x.whl', 'verified_sha256': sha(b'wheel')}]}))
            files = {'Dockerfile': blob('FROM node:24-bookworm-slim AS web-build\nFROM python:3.13-slim-bookworm\n'
                     'RUN pip install --no-cache-dir --require-hashes -r deploy/requirements.txt \\\n'),
                     '.dockerignore': blob('**\n'), 'deploy/requirements.txt': (requirements, 0o644)}
            c = config(); c['wheelhouse'] = str(wheels)
            self.assertIn('offline-wheelhouse/artifacts/x.whl', offline_context(files, c))
            (wheels / 'artifacts/x.whl').write_bytes(b'tampered')
            with self.assertRaises(ReleaseError):
                offline_context(files, c)
            (wheels / 'artifacts/x.whl').write_bytes(b'wheel')
            (wheels / 'requirements.install.txt').write_bytes(requirements + b'other==9\n')
            with self.assertRaises(ReleaseError):
                offline_context(files, c)


class ReviewedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.before = {'server/plans/service.py': blob('old service')}
        self.after = {'server/plans/service.py': blob('new reviewed service')}
        self.record = {'baseline_commit': '1' * 40, 'candidate_commit': '2' * 40,
                       'path': 'server/plans/service.py', 'old_sha256': sha(b'old service'),
                       'new_sha256': sha(b'new reviewed service'), 'mode': '100644'}
        self.config = {**config(), 'reviewed_runtime_changes': [self.record]}

    def check(self, before=None, after=None, config_value=None):
        return compatibility(before or self.before, after or self.after, config=config_value or self.config,
                             baseline='1' * 40, commit='2' * 40)

    def test_only_exact_commits_hashes_mode_and_supported_service(self):
        self.assertEqual(self.check(), ['server/plans/service.py'])
        for field, value in [('baseline_commit', '3' * 40), ('candidate_commit', '3' * 40),
                             ('old_sha256', '3' * 64), ('new_sha256', '3' * 64), ('mode', '100755')]:
            c = copy.deepcopy(self.config);c['reviewed_runtime_changes'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ReleaseError):
                self.check(config_value=c)
        with self.assertRaises(ReleaseError):
            self.check(after={'server/plans/service.py': blob('new reviewed service plus extra byte')})
        with self.assertRaises(ReleaseError):
            self.check(after={'server/plans/service.py': (b'new reviewed service', 0o755)})
        for name in ('server/migrations/010.sql', 'server/plans/schema.sql', 'server/accounting/db.py',
                     'web/src/store.ts', 'deploy/requirements.txt', 'Dockerfile', 'server/plans/routes.py'):
            c = copy.deepcopy(self.config);c['reviewed_runtime_changes'][0]['path'] = name
            with self.subTest(path=name), self.assertRaises(ReleaseError):
                self.check(before={name: blob('old service')}, after={name: blob('new reviewed service')}, config_value=c)
        with self.assertRaises(ReleaseError):
            self.check(config_value={**self.config, 'reviewed_runtime_changes': [self.record, self.record]})
        with self.assertRaises(ReleaseError):
            self.check(after={**self.after, 'server/plans/schema.sql': blob('new DDL')})

    def test_prepare_records_and_verify_reproves_exact_runtime_record_without_build(self):
        # Real pack/manifest/verification; only candidate Git I/O and npm process boundary are fictional.
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'release'
            def npm(argv, cwd, **unused):
                dist = Path(cwd) / 'web/dist';dist.mkdir(parents=True, exist_ok=True)
                (dist / 'index.html').write_text('fixture');(dist / 'sw.js').write_text('fixture sw')
                return subprocess.CompletedProcess(argv, 0)
            trees = lambda repo, commit: self.before if commit == '1' * 40 else self.after
            with patch('release.core.git', side_effect=lambda repo, *args: args[-1].split('^')[0].encode()), \
                 patch('release.core.tree', side_effect=trees), patch('release.core.subprocess.run', side_effect=npm), \
                 patch('release.core.offline_context', side_effect=lambda files, config: files):
                result = prepare(REPO, '2' * 40, '1' * 40, self.config, output)
                self.assertEqual(result['reviewed_runtime_changes'], [self.record])
                self.assertEqual(verify_bundle(output, self.config, REPO)['reviewed_runtime_changes'], [self.record])
                saved = json.loads((output / 'manifest.json').read_text())
                saved['reviewed_runtime_changes'][0]['new_sha256'] = '4' * 64
                (output / 'manifest.json').write_text(json.dumps(saved))
                with self.assertRaisesRegex(ReleaseError, 'Manifest reviewed runtime proof differs'):
                    verify_bundle(output, self.config, REPO)
                (output / 'manifest.json').write_text(json.dumps(result))
                self.after['server/plans/service.py'] = blob('unreviewed change')
                with self.assertRaisesRegex(ReleaseError, 'Reviewed runtime exact old/new SHA mismatch'):
                    verify_bundle(output, self.config, REPO)

    def test_test_files_do_not_enter_runtime_inputs(self):
        for name in ('server/tests/legacy_fixture.py', 'server/tests/nested/fake.py',
                     'server/plans/test_service.py', 'server/memos/nested/test_extra.py'):
            with self.subTest(path=name):
                self.assertFalse(source_path(name))
        for name in ('server/plans/service.py', 'server/memos/service.py', 'server/accounting/db.py'):
            self.assertTrue(source_path(name))
        fixed = tree(REPO, 'HEAD')
        self.assertTrue('server/plans/service.py' in fixed and 'server/memos/service.py' in fixed)
        self.assertFalse(any(name.startswith('server/tests/') or
                             (name.startswith('server/') and Path(name).name.startswith('test_') and name.endswith('.py'))
                             for name in fixed))


class FrontendDependencyTests(unittest.TestCase):
    def setUp(self):
        self.package = {'name': 'shared-life-web', 'version': '0.1.0', 'private': True,
                        'scripts': {'build': 'fixed-build'}, 'dependencies': {'existing': '1.0.0'},
                        'devDependencies': {'tool': '1.0.0'}}
        old_record = {'version': '1.0.0', 'resolved': 'https://registry.npmjs.org/existing/-/existing-1.0.0.tgz',
                      'integrity': 'sha512-Zml4ZWQ='}
        self.lock = {'name': 'shared-life-web', 'version': '0.1.0', 'lockfileVersion': 3, 'requires': True,
                     'packages': {'': {k: copy.deepcopy(self.package[k]) for k in
                                       ('name', 'version', 'dependencies', 'devDependencies')},
                                  'node_modules/existing': old_record,
                                  'node_modules/tool': copy.deepcopy(old_record)}}
        self.new_package, self.new_lock = copy.deepcopy(self.package), copy.deepcopy(self.lock)
        self.new_package['version'] = self.new_lock['version'] = self.new_lock['packages']['']['version'] = '1.0.0'
        self.new_package['dependencies']['editor'] = '6.1.2'
        self.new_lock['packages']['']['dependencies']['editor'] = '6.1.2'
        self.new_lock['packages']['node_modules/editor'] = {
            'version': '6.1.2', 'resolved': 'https://registry.npmjs.org/editor/-/editor-6.1.2.tgz',
            'integrity': 'sha512-bmV3', 'dependencies': {'transitive': '^1.0.0'}}
        self.new_lock['packages']['node_modules/transitive'] = {
            'version': '1.2.3', 'resolved': 'https://registry.npmjs.org/transitive/-/transitive-1.2.3.tgz',
            'integrity': 'sha512-bmV3'}

    def inputs(self):
        before = {'web/package.json': blob(self.package), 'web/package-lock.json': blob(self.lock)}
        after = {'web/package.json': blob(self.new_package), 'web/package-lock.json': blob(self.new_lock)}
        record = {'baseline_commit': '1' * 40, 'candidate_commit': '2' * 40, 'files': {
            name: {'old_sha256': sha(before[name][0]), 'new_sha256': sha(after[name][0]), 'mode': '100644'}
            for name in before}}
        c = {**config(), 'reviewed_frontend_dependency_additions': [record]}
        return before, after, c

    def test_exact_additions_need_review_and_preserve_unchanged_package_records(self):
        before, after, c = self.inputs()
        with self.assertRaises(ReleaseError):
            compatibility(before, after)
        changed = compatibility(before, after, config=c, baseline='1' * 40, commit='2' * 40)
        self.assertEqual(set(changed), {'web/package.json', 'web/package-lock.json'})
        proof = reviewed_frontend_additions(before, after, c, '1' * 40, '2' * 40)[0]
        self.assertEqual(proof['added_dependencies']['dependencies'], {'editor': '6.1.2'})
        self.assertEqual(proof['added_lock_records'], ['node_modules/editor', 'node_modules/transitive'])
        for mutation in ('commit', 'hash', 'mode', 'missing-file'):
            bad = copy.deepcopy(c)
            record = bad['reviewed_frontend_dependency_additions'][0]
            if mutation == 'commit': record['candidate_commit'] = '3' * 40
            elif mutation == 'hash': record['files']['web/package.json']['new_sha256'] = '3' * 64
            elif mutation == 'mode': record['files']['web/package.json']['mode'] = '100755'
            else: del record['files']['web/package-lock.json']
            with self.subTest(proof=mutation), self.assertRaises(ReleaseError):
                compatibility(before, after, config=bad, baseline='1' * 40, commit='2' * 40)
        for prohibited in ('deploy/requirements.txt', 'uv.lock', 'server/plans/schema.sql'):
            with self.subTest(path=prohibited), self.assertRaises(ReleaseError):
                compatibility({**before, prohibited: blob('old')}, {**after, prohibited: blob('new')},
                              config=c, baseline='1' * 40, commit='2' * 40)

    def test_semantics_reject_upgrades_old_record_changes_scripts_and_unpinned_or_inconsistent_additions(self):
        initial = copy.deepcopy((self.new_package, self.new_lock))
        for mutation in ('upgrade', 'record-hash', 'remove-record', 'scripts', 'engines', 'unpinned',
                         'root-mismatch', 'lock-version', 'direct-lock-version', 'local-link'):
            self.new_package, self.new_lock = copy.deepcopy(initial)
            if mutation == 'upgrade':
                self.new_package['dependencies']['existing'] = '2.0.0'
                self.new_lock['packages']['']['dependencies']['existing'] = '2.0.0'
            elif mutation == 'record-hash': self.new_lock['packages']['node_modules/existing']['integrity'] = 'changed'
            elif mutation == 'remove-record': del self.new_lock['packages']['node_modules/tool']
            elif mutation == 'scripts': self.new_package['scripts']['build'] = 'changed-build'
            elif mutation == 'engines': self.new_package['engines'] = {'node': '>=99'}
            elif mutation == 'unpinned':
                self.new_package['dependencies']['editor'] = '^6.1.2'
                self.new_lock['packages']['']['dependencies']['editor'] = '^6.1.2'
            elif mutation == 'root-mismatch': self.new_lock['packages']['']['dependencies']['editor'] = '6.1.3'
            elif mutation == 'lock-version': self.new_lock['lockfileVersion'] = 4
            elif mutation == 'direct-lock-version': self.new_lock['packages']['node_modules/editor']['version'] = '6.1.3'
            else: self.new_lock['packages']['node_modules/editor']['link'] = True
            before, after, c = self.inputs()  # Exact review SHA is refreshed: semantic gate must still reject.
            with self.subTest(mutation=mutation), self.assertRaises(ReleaseError):
                compatibility(before, after, config=c, baseline='1' * 40, commit='2' * 40)

    def test_manifest_reproves_dependency_review_and_collects_all_lazy_chunks(self):
        before, after, c = self.inputs()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'release'
            def npm(argv, cwd, **unused):
                dist = Path(cwd) / 'web/dist';(dist / 'assets').mkdir(parents=True, exist_ok=True)
                (dist / 'index.html').write_text('fixture');(dist / 'sw.js').write_text('fixture sw')
                for index in range(7): (dist / 'assets' / ('chunk-' + str(index) + '.js')).write_text(str(index))
                return subprocess.CompletedProcess(argv, 0)
            trees = lambda repo, commit: before if commit == '1' * 40 else after
            with patch('release.core.git', side_effect=lambda repo, *args: args[-1].split('^')[0].encode()), \
                 patch('release.core.tree', side_effect=trees), patch('release.core.subprocess.run', side_effect=npm), \
                 patch('release.core.offline_context', side_effect=lambda files, config: files):
                result = prepare(REPO, '2' * 40, '1' * 40, c, output)
                self.assertEqual(len(result['runtime']), 9)
                self.assertIn('assets/chunk-6.js', result['runtime'])
                self.assertEqual(verify_bundle(output, c, REPO)['reviewed_frontend_dependency_additions'],
                                 result['reviewed_frontend_dependency_additions'])
                saved = json.loads((output / 'manifest.json').read_text())
                saved['reviewed_frontend_dependency_additions'][0]['added_lock_records'] = []
                (output / 'manifest.json').write_text(json.dumps(saved))
                with self.assertRaisesRegex(ReleaseError, 'Manifest reviewed frontend dependency proof differs'):
                    verify_bundle(output, c, REPO)

    def test_exact_legacy_guard_exception_still_blocks_other_storage_paths(self):
        path = 'server/memos/migration_guard.py'
        before, after = {path: blob('old guard')}, {path: blob('reviewed guard')}
        record = {'baseline_commit': '1' * 40, 'candidate_commit': '2' * 40, 'path': path,
                  'old_sha256': sha(before[path][0]), 'new_sha256': sha(after[path][0]), 'mode': '100644'}
        c = {**config(), 'reviewed_runtime_changes': [record]}
        self.assertEqual(compatibility(before, after, config=c, baseline='1' * 40, commit='2' * 40), [path])
        with self.assertRaises(ReleaseError):
            compatibility(before, after, config={**c, 'schema': 8}, baseline='1' * 40, commit='2' * 40)
        for name in ('server/accounting/db.py', 'server/migrations/009_memos.sql', 'server/memos/other_guard.py'):
            bad = copy.deepcopy(c);bad['reviewed_runtime_changes'][0]['path'] = name
            with self.subTest(path=name), self.assertRaises(ReleaseError):
                compatibility({name: blob('old guard')}, {name: blob('reviewed guard')},
                              config=bad, baseline='1' * 40, commit='2' * 40)


class ComposeBoundaryTests(unittest.TestCase):
    """Call real inspect/resources/configuration/switch; mock only SSH JSON transport."""
    def fixture(self, temporary):
        c, m = config(), manifest()
        runner = Runner(c, m, temporary)
        environment = {**c['expected_environment'], 'SHARED_EXPENSE_VAPID_PRIVATE_KEY': '/run/secrets/shared-life-vapid.pem',
                       'SHARED_EXPENSE_VAPID_PUBLIC_KEY': 'fictional-public', 'SHARED_EXPENSE_VAPID_SUBJECT': 'fictional-subject'}
        mounts = [{'Type': 'volume', 'Name': c['volume'], 'Source': '/fictional/volume', 'Destination': '/data', 'RW': True},
                  {'Type': 'bind', 'Source': c['daily'], 'Destination': '/backups', 'RW': True},
                  {'Type': 'bind', 'Source': '/fictional/existing-key', 'Destination': '/run/secrets/shared-life-vapid.pem', 'RW': False}]
        network = c['project'] + '_default'
        raw = {'Config': {'Image': c['baseline']['image'], 'Env': [k + '=' + v for k, v in environment.items()], 'User': 'app'},
               'Image': c['baseline']['image_id'], 'Id': 'existing-container',
               'State': {'Health': {'Status': 'healthy'}, 'Status': 'running'},
               'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}, 'NetworkMode': network,
                              'PortBindings': {'8765/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8765'}]}},
               'Mounts': mounts, 'NetworkSettings': {'Networks': {network: {'NetworkID': 'existing-network-id'}}}}
        network_info = {'Name': network, 'Id': 'existing-network-id', 'Driver': 'bridge', 'Scope': 'local', 'Internal': False,
                        'Labels': {'com.docker.compose.project': c['project'], 'com.docker.compose.network': 'default'}}
        app = {'image': c['baseline']['image'], 'restart': 'unless-stopped', 'environment': environment,
               'ports': [{'target': 8765, 'published': '8765', 'host_ip': '127.0.0.1', 'protocol': 'tcp', 'mode': 'ingress'}],
               'networks': {'default': None}, 'volumes': [
                   {'type': 'volume', 'source': 'ledger', 'target': '/data'},
                   {'type': 'bind', 'source': c['daily'], 'target': '/backups'},
                   {'type': 'bind', 'source': '/fictional/existing-key', 'target': '/run/secrets/shared-life-vapid.pem', 'read_only': True}]}
        compose = {'name': c['project'], 'services': {'app': app}, 'volumes': {'ledger': {'name': c['volume']}},
                   'networks': {'default': {'name': network}}}
        calls = []
        def transport(command, data=None, *, stage='query'):
            calls.append((stage, command))
            if ' config --format json' in command:
                result = copy.deepcopy(compose)
                if runner.source in command:
                    result['services']['app']['image'] = runner.target
                return json.dumps(result)
            if ' network inspect ' in command:
                return json.dumps([network_info])
            if ' image inspect ' in command:
                return json.dumps([{'Id': 'candidate-id', 'Config': {'User': 'app', 'Env': []}}])
            if ' inspect ' in command:
                return json.dumps([raw])
            if ' ps --filter ' in command:
                return c['container'] + '\n'
            if stage == 'switch':
                return ''
            self.fail('Unexpected transport command: ' + command)
        runner.remote = transport
        return runner, compose, raw, network_info, calls

    def test_existing_default_bridge_shape_passes_actual_pre_switch_checks(self):
        for explicit_empty_ipam in (False, True):
            with self.subTest(explicit_empty_ipam=explicit_empty_ipam), tempfile.TemporaryDirectory() as temporary:
                runner, compose, raw, network, calls = self.fixture(temporary)
                if explicit_empty_ipam:
                    # Actual NAS Compose JSON shape: default name plus empty ipam, app default:null.
                    compose['networks']['default']['ipam'] = {}
                state = runner.inspect();runner.resources(state, runner.c['baseline']['image'], runner.c['baseline']['image_id'])
                self.assertEqual(state['networks'], {'shared-life_default': 'existing-network-id'})
                runner.configuration();runner.switch()
                self.assertEqual(len([v for v in calls if v[0] == 'switch']), 1)

    def test_unapplied_user_ports_restart_and_network_drift_refuse_before_up(self):
        cases = [('user', '0:0'), ('restart', 'no'), ('network_mode', 'host'),
                 ('ports', [{'target': 8765, 'published': '8765', 'host_ip': '0.0.0.0'}]),
                 ('networks', {'custom': None})]
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                runner, compose, raw, network, calls = self.fixture(temporary)
                compose['services']['app'][field] = value
                # Live still satisfies resources. Both old/new Compose contain identical unapplied drift.
                state = runner.inspect();runner.resources(state, runner.c['baseline']['image'], runner.c['baseline']['image_id'])
                with self.assertRaises(ReleaseError):
                    runner.switch()
                self.assertFalse([v for v in calls if v[0] == 'switch'])
        for drift in ('external', 'driver', 'network-id', 'live-host', 'nonempty-ipam'):
            with self.subTest(network_drift=drift), tempfile.TemporaryDirectory() as temporary:
                runner, compose, raw, network, calls = self.fixture(temporary)
                if drift == 'external': compose['networks']['default']['external'] = True
                elif drift == 'driver': network['Driver'] = 'macvlan'
                elif drift == 'network-id': raw['NetworkSettings']['Networks']['shared-life_default']['NetworkID'] = 'other-id'
                elif drift == 'nonempty-ipam': compose['networks']['default']['ipam'] = {'config': [{'subnet': '192.0.2.0/24'}]}
                else: raw['HostConfig']['NetworkMode'] = 'host'
                with self.assertRaises(ReleaseError): runner.switch()
                self.assertFalse([v for v in calls if v[0] == 'switch'])


class JournalRunner(Runner):
    """Fictional NAS command boundary for stage ordering and unknown-result behavior."""
    def __init__(self, output):
        super().__init__(config(), manifest(), output)
        self.j = {}; self.calls = []; self.current = 'baseline'; self.locked = False
        self.base_state = {'image': self.c['baseline']['image'], 'image_id': self.c['baseline']['image_id'],
                           'health': 'healthy', 'status': 'running', 'mounts': [{'original': True}],
                           'environment_sha256': 'same-key-and-env', 'networks': {'default': 'existing'},
                           'network_contract': {'Id': 'existing'}}
    def remote(self, command, data=None, *, stage='query'):
        self.calls.append(('remote', stage)); return ''
    def journal(self):
        self.calls.append(('query', 'journal')); return copy.deepcopy(self.j)
    def set_journal(self, value):
        self.j = copy.deepcopy(value)
    def init_remote(self):
        self.calls.append(('owner', 'init'))
    def lock_stage(self, stage):
        if self.locked:
            raise ReleaseError('another executor')
        self.locked = True
    def unlock_stage(self):
        self.locked = False
    def inspect(self):
        result = dict(self.base_state)
        if self.current == 'candidate':
            result.update(image=self.target, image_id='sha256:' + '2' * 64)
        return result
    def image_info(self, image):
        return {'Id': 'sha256:' + '2' * 64, 'Config': {'Labels': {
            'shared-life.commit': self.m['commit'], 'shared-life.context': self.m['context_sha256']}}}
    def resources(self, state, image, image_id):
        if state['image'] != image or state['image_id'] != image_id:
            raise ReleaseError('wrong live image')
    def op(self, name):
        self.calls.append(('operation', name))
        if name == 'switch':
            self.current = 'candidate'
        return {'stage': name}
    def upload(self): return self.op('upload')
    def build(self): return self.op('build')
    def image(self): return self.op('image')
    def configuration(self): return self.op('config')
    def backup_consistent(self): return self.op('backup')
    def switch(self): return self.op('switch')
    def verify(self): return self.op('verify')


class ResumeTests(unittest.TestCase):
    @patch('release.runner.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'{"status":"ok"}'))
    def test_success_and_completed_resume_never_repeat_backup_or_switch(self, invoke):
        with tempfile.TemporaryDirectory() as temporary:
            runner = JournalRunner(temporary)
            self.assertEqual(runner.execute()['status'], 'deployed')
            self.assertEqual([stage for kind, stage in runner.calls if kind == 'operation'], list(STAGES))
            runner.calls.clear()
            self.assertEqual(runner.execute()['status'], 'deployed')
            self.assertFalse([x for x in runner.calls if x[0] == 'operation'])
            self.assertEqual(runner.calls[0], ('query', 'journal'))
            self.assertEqual(invoke.call_count, 2 * len(runner.c['https_routes']))

    def test_unknown_backup_or_upload_fail_closed_before_writing(self):
        for stage in ('upload', 'backup'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                runner = JournalRunner(temporary)
                runner.j[stage] = {'status': 'started'}
                with self.assertRaisesRegex(ReleaseError, 'Unknown result'):
                    runner.stage(stage, lambda: self.fail('must not repeat unknown operation'))
                self.assertFalse(runner.locked)
                self.assertEqual(runner.j[stage]['status'], 'started')

    def test_unknown_switch_queries_target_and_does_not_issue_second_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = JournalRunner(temporary);runner.j['switch'] = {'status': 'started'}
            with self.assertRaisesRegex(ReleaseError, 'wrong live image'):
                runner.stage('switch', lambda: self.fail('must not retry switch'))
            runner.current = 'candidate'
            runner.stage('switch', lambda: self.fail('must not retry switch'))
            self.assertEqual(runner.j['switch']['status'], 'done')

    def test_unknown_build_only_reconciles_exact_labeled_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = JournalRunner(temporary);runner.j['build'] = {'status': 'started'}
            runner.stage('build', lambda: self.fail('build must not rerun'))
            runner.j['build'] = {'status': 'started'}
            with patch.object(runner, 'image_info', return_value={'Id': 'other', 'Config': {'Labels': {}}}):
                with self.assertRaisesRegex(ReleaseError, 'Unknown build result'):
                    runner.stage('build', lambda: self.fail('build must not rerun'))

    def test_remote_failure_sanitizes_private_output_and_never_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = Runner(config(), manifest(), temporary)
            result = subprocess.CompletedProcess([], 255, b'private data', b'SECRET VALUE')
            with patch('release.runner.subprocess.run', return_value=result) as invoke:
                with self.assertRaises(ReleaseError) as error:
                    runner.remote('fictional command', stage='backup')
                self.assertEqual(invoke.call_count, 1)
                self.assertNotIn('SECRET', str(error.exception))
                self.assertNotIn('private data', runner.log.read_text())

    def test_dry_run_does_not_call_ssh_or_docker(self):
        with tempfile.TemporaryDirectory() as temporary, patch('release.runner.subprocess.run') as invoke:
            result = Runner(config(), manifest(), temporary).plan()
            self.assertEqual(result['network'], 'none');invoke.assert_not_called()


class HttpsPreflightTests(unittest.TestCase):
    def test_route_failure_stops_before_any_remote_write_or_release_operation(self):
        failures = [(28, b'', b'private curl error'), (0, b'not JSON', b''),
                    (0, b'{"status":"down"}', b''), (0, b'{"status":"ok","extra":true}', b'')]
        for failed_route in range(len(config()['https_routes'])):
            for returncode, stdout, stderr in failures:
                with self.subTest(route=failed_route, returncode=returncode, stdout=stdout), \
                     tempfile.TemporaryDirectory() as temporary:
                    runner = JournalRunner(temporary)
                    results = [subprocess.CompletedProcess([], 0, b'{"status":"ok"}') for _ in range(failed_route)]
                    results.append(subprocess.CompletedProcess([], returncode, stdout, stderr))
                    with patch('release.runner.subprocess.run', side_effect=results) as invoke:
                        with self.assertRaisesRegex(ReleaseError, 'environment prerequisite not met') as error:
                            runner.execute()
                    self.assertIn('no remote writes performed', str(error.exception))
                    self.assertNotIn('private curl error', str(error.exception))
                    self.assertEqual(runner.calls, [('query', 'journal')])
                    self.assertEqual(runner.j, {})
                    self.assertFalse(runner.locked)
                    self.assertEqual(invoke.call_count, failed_route + 1)

    def test_success_checks_every_route_before_owner_and_keeps_stage_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = JournalRunner(temporary)
            def curl(argv, **kwargs):
                self.assertNotIn(('owner', 'init'), runner.calls)
                self.assertFalse([v for v in runner.calls if v[0] == 'operation'])
                return subprocess.CompletedProcess(argv, 0, b'{"status":"ok"}')
            with patch('release.runner.subprocess.run', side_effect=curl) as invoke:
                self.assertEqual(runner.execute()['status'], 'deployed')
            self.assertEqual(invoke.call_count, len(runner.c['https_routes']))
            self.assertEqual([v for k, v in runner.calls if k == 'operation'], list(STAGES))

    def test_plan_and_status_do_not_probe_https(self):
        with tempfile.TemporaryDirectory() as temporary, patch('release.runner.subprocess.run') as invoke:
            runner = JournalRunner(temporary)
            self.assertEqual(runner.plan()['network'], 'none')
            self.assertEqual(runner.status()['status'], 'queried')
            invoke.assert_not_called()
            self.assertEqual(runner.calls, [('query', 'journal')])

    def test_completed_resume_and_missing_curl_fail_before_remote_writes(self):
        for completed in (False, True):
            with self.subTest(completed=completed), tempfile.TemporaryDirectory() as temporary:
                runner = JournalRunner(temporary)
                if completed:
                    runner.current = 'candidate'
                    runner.j = {stage: {'status': 'done', 'proof': {}} for stage in STAGES}
                original = copy.deepcopy(runner.j)
                with patch('release.runner.subprocess.run', side_effect=FileNotFoundError('private executable detail')):
                    with self.assertRaisesRegex(ReleaseError, 'environment prerequisite not met') as error:
                        runner.execute()
                self.assertNotIn('private executable detail', str(error.exception))
                self.assertEqual(runner.calls, [('query', 'journal')])
                self.assertEqual(runner.j, original)

    def test_execute_does_not_retry_unknown_upload_or_backup_after_preflight(self):
        for unknown in ('upload', 'backup'):
            with self.subTest(stage=unknown), tempfile.TemporaryDirectory() as temporary:
                runner = JournalRunner(temporary)
                runner.j = {stage: {'status': 'done', 'proof': {}} for stage in STAGES[:STAGES.index(unknown)]}
                runner.j[unknown] = {'status': 'started'}
                original = copy.deepcopy(runner.j)
                with patch('release.runner.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'{"status":"ok"}')):
                    with self.assertRaisesRegex(ReleaseError, 'Unknown result for ' + unknown):
                        runner.execute()
                self.assertEqual(runner.j, original)
                self.assertFalse([v for v in runner.calls if v[0] == 'operation'])

    def test_post_switch_keeps_health_and_all_resource_hashes_with_identical_route_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = JournalRunner(temporary);runner.current = 'candidate'
            runner.m['runtime'] = {'index.html': sha(b'html'), 'assets/lazy.js': sha(b'lazy')}
            runner.c['https_routes'] = [
                {'name': 'bound', 'origin': 'https://example.test:444/', 'address': '192.0.2.5', 'interface': 'en0'},
                {'name': 'unbound', 'origin': 'https://other.test', 'address': '192.0.2.6'}]
            calls = []
            def curl(argv, **kwargs):
                calls.append(argv)
                content = b'html' if argv[-1].endswith('/index.html') else b'lazy' if argv[-1].endswith('/assets/lazy.js') else b'{"status":"ok"}'
                return subprocess.CompletedProcess(argv, 0, content)
            with patch('release.runner.subprocess.run', side_effect=curl):
                runner.https_preflight()
                result = Runner.verify(runner)
            self.assertEqual(result['https_checks'], 6)
            for index, route in enumerate(runner.c['https_routes']):
                expected = ['curl', '--silent', '--show-error', '--fail', '--noproxy', '*', '--connect-timeout', '8',
                            '--max-time', '20', '--resolve',
                            'example.test:444:192.0.2.5' if index == 0 else 'other.test:443:192.0.2.6']
                if index == 0: expected += ['--interface', 'en0']
                expected += [route['origin'].rstrip('/') + '/api/health']
                self.assertEqual(calls[index], expected)
                self.assertEqual(calls[2 + index * 3], expected)
            with patch('release.runner.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'{"status":"ok"}')):
                with self.assertRaisesRegex(ReleaseError, 'resource/health mismatch'):
                    Runner.verify(runner)


if __name__ == '__main__':
    unittest.main()
