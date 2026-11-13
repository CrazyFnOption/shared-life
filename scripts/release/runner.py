"""Existing NAS/Docker flow, with protected backup and fail-closed stage journals."""
from __future__ import annotations

import datetime
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import time
from urllib.parse import urlsplit

from .core import ReleaseError, require, sha, write_json


STAGES = ('upload', 'build', 'image', 'config', 'backup', 'switch', 'verify')


def q(value):
    return shlex.quote(str(value))


class Runner:
    def __init__(self, config, manifest, output):
        self.c, self.m, self.out = config, manifest, Path(output)
        self.validate_config()
        self.root = config['remote_root']
        self.release = self.root + '/releases/' + manifest['release_id']
        self.source = self.root + '/source-' + manifest['commit']
        self.target = config['image_repository'] + ':release-' + manifest['commit']
        self.backup = self.root + '/backups/release-' + manifest['release_id']
        self.docker = q(config['docker'])
        self.log = self.out / 'execution.jsonl'

    def validate_config(self):
        c = self.c
        require(isinstance(c['ssh'], list) and c['ssh'][0] == 'ssh' and len(c['ssh']) >= 2
                and all(isinstance(v, str) and '\n' not in v for v in c['ssh']), 'Invalid SSH argv')
        for name in ('remote_root', 'docker', 'docker_config', 'daily'):
            p = PurePosixPath(c[name])
            require(p.is_absolute() and '..' not in p.parts and '\n' not in c[name], 'Invalid remote path: ' + name)
        require(c['daily'] == c['remote_root'] + '/backups/daily', 'Daily backup must remain in current service root')
        require(c['baseline']['source'].startswith(c['remote_root'] + '/source-'), 'Baseline source outside service')
        require(re.fullmatch('[0-9a-f]{40}', c['baseline']['commit']), 'Baseline requires full commit')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', c['baseline']['image_id']), 'Baseline requires exact image ID')
        for name in ('project', 'container', 'volume', 'image_repository'):
            require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:/-]*', c[name]), 'Invalid resource name: ' + name)
        require(c['compose_files'] == ['compose.yaml', c['overlay']] and
                re.fullmatch(r'[a-zA-Z0-9_.-]+\.yaml', c['overlay']) and c['overlay'] != 'compose.yaml',
                'Expected base Compose and one current private overlay')
        require(c['expected_environment']['SHARED_EXPENSE_ALLOW_MODEL_CALLS'] == '0' and
                c['expected_environment']['SHARED_EXPENSE_MODEL_CALL_LIMIT'] == '0', 'Model calls must stay disabled')
        require(c['expected_environment']['SHARED_EXPENSE_DB'] == '/data/ledger.sqlite3' and
                c['expected_environment']['SHARED_EXPENSE_BACKUP_DIR'] == '/backups', 'Storage paths changed')
        require(c['expected_environment']['SHARED_EXPENSE_ALLOW_PUSH'] == '1', 'This flow preserves current Push1 configuration')
        require(c['https_routes'], 'At least one existing HTTPS route is required')
        for route in c['https_routes']:
            parsed = urlsplit(route['origin'])
            require(parsed.scheme == 'https' and parsed.hostname and parsed.path in ('', '/') and
                    not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
                    'HTTPS origin must be a bare existing HTTPS origin')
            ipaddress.ip_address(route['address'])
            require(not route.get('interface', '').startswith('-'), 'Invalid route interface')
        require(c['schema'] == self.m['schema'] and c['backup_format'] == self.m['backup_format'], 'Backup contract changed')
        for name in ('operator_uid', 'operator_gid'):
            require(isinstance(c[name], int) and c[name] > 0, 'Explicit source config ownership required: ' + name)

    def event(self, stage, status, **details):
        value = {'time': datetime.datetime.now(datetime.UTC).isoformat(), 'stage': stage, 'status': status, **details}
        fd = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(value) + '\n')

    def remote(self, command, data=None, *, stage='query'):
        result = subprocess.run(self.c['ssh'] + [command], input=data, capture_output=True)
        if result.returncode:
            self.event(stage, 'command_failed', returncode=result.returncode)
            # No remote stdout/stderr in model-facing errors: tools may emit private runtime configuration.
            raise ReleaseError(f'Remote {stage} failed ({result.returncode}); query status before retry. '
                               'Remote logs/evidence remain on NAS; no automatic retry or rollback.')
        return result.stdout.decode()

    def tool(self, code, *, image=None, user='10001:10001', mounts=(), stage='query'):
        argv = [self.c['docker'], 'run', '--rm', '--pull=never', '-i', '--network', 'none', '--read-only',
                '--user', user, '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777']
        for source, destination, readonly in mounts:
            require(':' not in source, 'Unsupported colon in mount source')
            argv += ['-v', source + ':' + destination + (':ro' if readonly else '')]
        argv += ['--entrypoint', 'python', image or self.c['baseline']['image'], '-']
        wrapped = ('import sys,json\ntry:\n exec(' + repr(code) + ')\n'
                   'except BaseException as error:\n'
                   ' print(json.dumps({"error_type":type(error).__name__,"details":"inspect NAS-only evidence"}),file=sys.stderr)\n'
                   ' sys.exit(1)\n')
        return json.loads(self.remote(shlex.join(argv), wrapped.encode(), stage=stage))

    def compose(self, source, *args):
        argv = [self.c['docker'], 'compose', '-p', self.c['project']]
        for filename in self.c['compose_files']:
            argv += ['-f', filename]
        argv.extend(args)
        return 'export DOCKER_CONFIG=' + q(self.c['docker_config']) + '; cd ' + q(source) + ' && ' + shlex.join(argv)

    def inspect(self):
        raw = json.loads(self.remote(self.docker + ' inspect ' + q(self.c['container'])))[0]
        env = dict(v.split('=', 1) for v in raw['Config']['Env'])
        # Filter before returning or persisting; never record VAPID values or the full environment.
        state = {'image': raw['Config']['Image'], 'image_id': raw['Image'],
                 'container_id': raw['Id'], 'health': raw['State'].get('Health', {}).get('Status'),
                 'status': raw['State']['Status'], 'user': raw['Config']['User'],
                 'restart': raw['HostConfig']['RestartPolicy']['Name'],
                 'ports': raw['HostConfig']['PortBindings'],
                 'network_mode': raw['HostConfig']['NetworkMode'],
                 'networks': {name: value['NetworkID'] for name, value in raw['NetworkSettings']['Networks'].items()},
                 'mounts': sorted(raw['Mounts'], key=lambda m: m['Destination']),
                 'flags': {name: env.get(name) for name in self.c['expected_environment']},
                 'environment_sha256': sha(json.dumps(env, sort_keys=True).encode()),
                 'vapid_present': all(env.get('SHARED_EXPENSE_' + name) for name in
                                      ('VAPID_PRIVATE_KEY', 'VAPID_PUBLIC_KEY', 'VAPID_SUBJECT'))}
        state['vapid_present'] = bool(state['vapid_present'])
        state['writers'] = self.remote(self.docker + ' ps --filter ' + q('volume=' + self.c['volume']) +
                                       ' --format ' + q('{{.Names}}')).strip().splitlines()
        network = json.loads(self.remote(self.docker + ' network inspect ' + q(self.c['project'] + '_default')))[0]
        state['network_contract'] = {name: network.get(name) for name in ('Name', 'Id', 'Driver', 'Scope', 'Internal')}
        state['network_contract']['project'] = network.get('Labels', {}).get('com.docker.compose.project')
        state['network_contract']['kind'] = network.get('Labels', {}).get('com.docker.compose.network')
        return state

    def network_resources(self, state):
        name = self.c['project'] + '_default'
        network = state['network_contract']
        require(network == {'Name': name, 'Id': network['Id'], 'Driver': 'bridge', 'Scope': 'local',
                            'Internal': False, 'project': self.c['project'], 'kind': 'default'} and network['Id'],
                'Expected original project default bridge network')
        require(state['network_mode'] == name and state['networks'] == {name: network['Id']},
                'Live container is not on the single original project default network')

    def resources(self, state, image, image_id):
        c = self.c
        self.network_resources(state)
        require(state['image'] == image and state['image_id'] == image_id, 'Live image differs from fixed expected image')
        require(state['status'] == 'running' and state['restart'] == 'unless-stopped', 'Unexpected container lifecycle')
        require(state['user'] in ('app', '10001', '10001:10001'), 'Runtime UID differs from expected app UID')
        require(state['writers'] == [c['container']], 'Unexpected writer/container on original ledger volume')
        require(state['ports'] == {'8765/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8765'}]}, 'Port binding changed')
        require(state['flags'] == c['expected_environment'] and state['vapid_present'], 'Runtime flags/Push configuration changed')
        mounts = state['mounts']
        require(any(m.get('Name') == c['volume'] and m['Destination'] == '/data' and m['RW'] for m in mounts),
                'Original ledger volume missing/changed')
        require(any(m['Source'] == c['daily'] and m['Destination'] == '/backups' and m['RW'] for m in mounts),
                'Original daily backup bind missing/changed')
        require(any(m['Destination'] == '/run/secrets/shared-life-vapid.pem' and not m['RW'] for m in mounts),
                'Existing VAPID read-only mount missing')

    def image_info(self, image):
        return json.loads(self.remote(self.docker + ' image inspect ' + q(image)))[0]

    def journal(self):
        command = 'if test -f ' + q(self.release + '/state.json') + '; then cat ' + q(self.release + '/state.json') + '; else echo "{}"; fi'
        return json.loads(self.remote(command))

    def set_journal(self, state):
        data = json.dumps(state, sort_keys=True).encode()
        command = ('umask 077; cat > ' + q(self.release + '/state.json.tmp') + ' && mv ' +
                   q(self.release + '/state.json.tmp') + ' ' + q(self.release + '/state.json'))
        self.remote(command, data, stage='journal')
        write_json(self.out / 'state.json', state)

    def plan(self):
        return {'status': 'local-plan', 'commit': self.m['commit'], 'baseline': self.m['baseline'],
                'release_id': self.m['release_id'], 'stages': list(STAGES), 'network': 'none',
                'migration': 'forbidden; only compatible inputs and exact reviewed exceptions allowed',
                'source_files': len(self.m['source_files']), 'runtime_files': len(self.m['runtime']),
                'approval_command': 'execute --approve ' + self.m['commit']}

    def status(self):
        journal = self.journal()
        state = self.inspect()
        result = {'status': 'queried', 'commit': self.m['commit'], 'stages': journal,
                  'live': {'image_id': state['image_id'], 'health': state['health'], 'status': state['status']},
                  'unknown': [k for k, v in journal.items() if v.get('status') == 'started'],
                  'remote_writes': 'none (queries only)'}
        write_json(self.out / 'status.json', result)
        return result

    def init_remote(self):
        # A persistent service owner prevents another release from starting. Per-command lock prevents
        # simultaneous executors (including same release) from mutating a stage at once.
        code = ('set -eu; umask 077; '
                'mkdir -p ' + q(self.root + '/releases') + '; '
                'if mkdir ' + q(self.root + '/.release-owner') + ' 2>/dev/null; then '
                'echo ' + q(self.m['release_id']) + ' > ' + q(self.root + '/.release-owner/id') + '; '
                'else test "$(cat ' + q(self.root + '/.release-owner/id') + ')" = ' + q(self.m['release_id']) + '; fi; '
                'mkdir -p ' + q(self.release))
        self.remote(code, stage='init')

    def lock_stage(self, stage):
        self.remote('mkdir ' + q(self.root + '/.release-command-lock'), stage=stage)

    def unlock_stage(self):
        self.remote('rmdir ' + q(self.root + '/.release-command-lock'), stage='unlock')

    def stage(self, name, operation):
        self.lock_stage(name)
        try:
            journal = self.journal()
            prior = journal.get(name, {})
            if prior.get('status') == 'done':
                self.event(name, 'reused')
                return prior['proof']
            if prior.get('status') == 'started':
                if name in ('image', 'config', 'verify'):
                    proof = operation()  # Read-only validation may be retried after querying the same journal.
                elif name == 'switch':
                    image = self.image_info(self.target)
                    live = self.inspect()
                    self.resources(live, self.target, image['Id'])
                    proof = {'image_id': image['Id'], 'reconciled': 'same target container already running; no second compose up'}
                elif name == 'build':
                    info = self.image_info(self.target)
                    require(info['Config'].get('Labels', {}).get('shared-life.commit') == self.m['commit'] and
                            info['Config'].get('Labels', {}).get('shared-life.context') == self.m['context_sha256'],
                            'Unknown build result: no verified fixed candidate image; do not rerun build')
                    proof = {'image_id': info['Id'], 'reconciled': 'fixed candidate image found; build not repeated'}
                else:
                    raise ReleaseError('Unknown result for ' + name + '; status queried. Inspect same NAS release/logs '
                                       'and command lock; do not blindly rerun or remove evidence.')
            else:
                journal[name] = {'status': 'started', 'at': datetime.datetime.now(datetime.UTC).isoformat()}
                self.set_journal(journal)
                self.event(name, 'started')
                proof = operation()
            journal[name] = {'status': 'done', 'proof': proof}
            self.set_journal(journal)
            self.event(name, 'done')
            return proof
        finally:
            # The lock protects the local executor lifetime. If SSH is interrupted while a remote
            # command continues, the started journal still blocks retries even after lock release.
            self.unlock_stage()

    def upload(self):
        for name in ('source.tar.gz', 'context.tar.gz'):
            path = self.release + '/' + name
            self.remote('set -eu; umask 077; test ! -e ' + q(path) + '; test ! -e ' + q(path + '.part') +
                        '; cat > ' + q(path + '.part') + ' && mv ' + q(path + '.part') + ' ' + q(path),
                        (self.out / name).read_bytes(), stage='upload')
        self.remote('test ! -e ' + q(self.source) + ' && mkdir -m 755 ' + q(self.source), stage='upload')
        code = '''from pathlib import Path, PurePosixPath
import hashlib,tarfile,json,os,io
m=MANIFEST
source=Path('/source')
for name,key in [('source.tar.gz','source'),('context.tar.gz','context')]:
 p=Path('/bundle')/name
 assert hashlib.sha256(p.read_bytes()).hexdigest()==m[key+'_sha256']
 destination=source if key=='source' else source/'.build-context'
 destination.mkdir(exist_ok=True)
 with tarfile.open(p) as archive:
  members=archive.getmembers();seen=set()
  for v in members:
   n=PurePosixPath(v.name)
   assert v.isfile() and not n.is_absolute() and '..' not in n.parts and v.name not in seen
   assert v.name in m[key+'_files'];seen.add(v.name)
   data=archive.extractfile(v).read();assert hashlib.sha256(data).hexdigest()==m[key+'_files'][v.name]
   target=destination/v.name;target.parent.mkdir(parents=True,exist_ok=True)
   assert not target.exists();target.write_bytes(data);target.chmod(v.mode & 0o755)
  assert seen==set(m[key+'_files'])
current=Path('/current-overlay').read_text()
old=CONFIG['baseline']['image'];new=TARGET
assert current.count(old)==1 and new not in current
assert 'env_file:' in current and 'shared-life-vapid.pem:ro' in current
p=source/CONFIG['overlay'];p.write_text(current.replace(old,new));p.chmod(0o600)
os.chown(p,CONFIG['operator_uid'],CONFIG['operator_gid'])
print(json.dumps({'source_SHA':'match','context_SHA':'match','private_overlay':'image-only replacement; not in context'}))
'''.replace('MANIFEST', repr(self.m)).replace('CONFIG', repr(self.c)).replace('TARGET', repr(self.target))
        return self.tool(code, user='0:0', mounts=[(self.source, '/source', False),
            (self.release, '/bundle', True),
            (self.c['baseline']['source'] + '/' + self.c['overlay'], '/current-overlay', True)], stage='upload')

    def build(self):
        command = ('export DOCKER_CONFIG=' + q(self.c['docker_config']) + '; cd ' + q(self.source + '/.build-context') +
                   ' && ' + self.docker + ' build --pull=false --label ' + q('shared-life.commit=' + self.m['commit']) +
                   ' --label ' + q('shared-life.context=' + self.m['context_sha256']) +
                   ' -f Dockerfile.offline -t ' + q(self.target) + ' . > ' + q(self.release + '/build.log') + ' 2>&1')
        self.remote(command, stage='build')
        image = self.image_info(self.target)
        require(image['Config']['Labels'].get('shared-life.commit') == self.m['commit'] and
                image['Config']['Labels'].get('shared-life.context') == self.m['context_sha256'], 'Candidate image labels mismatch')
        return {'image_id': image['Id'], 'context_sha256': self.m['context_sha256']}

    def image(self):
        candidate, baseline = self.image_info(self.target), self.image_info(self.c['baseline']['image'])
        for key in ('User', 'Entrypoint', 'Cmd', 'Healthcheck', 'Volumes', 'ExposedPorts', 'Env', 'WorkingDir'):
            require(candidate['Config'].get(key) == baseline['Config'].get(key), 'Image runtime contract differs: ' + key)
        code = '''from pathlib import Path
import hashlib,json,os
m=MANIFEST
actual={str(p.relative_to('/app/web/dist')):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/web/dist').rglob('*') if p.is_file()}
assert actual==m['runtime'] and os.getuid()==os.getgid()==10001
for name,h in m['source_files'].items():
 if name.startswith(('server/','scripts/')) or name=='deploy/requirements.txt':
  assert hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==h
print(json.dumps({'runtime_SHA':'match frozen candidate','backend_scripts_lock_SHA':'match','uid':os.getuid()}))
'''.replace('MANIFEST', repr(self.m))
        proof = self.tool(code, image=self.target, stage='image')
        proof['image_id'] = candidate['Id']
        return proof

    def compose_runtime(self, value, live, image_config):
        """Only the existing single-app default bridge shape; reject unapplied config drift."""
        app = value['services']['app']
        self.network_resources(live)
        require((app.get('user') or image_config['User']) == live['user'] and
                live['user'] in ('app', '10001', '10001:10001'),
                'Resolved Compose user differs from current app user')
        require(app.get('restart', 'no') == live['restart'] == 'unless-stopped',
                'Resolved Compose restart differs from current unless-stopped')
        ports = app.get('ports', [])
        require(len(ports) == 1 and isinstance(ports[0], dict), 'Resolved Compose must publish only loopback8765')
        port = ports[0]
        require(set(port).issubset({'target', 'published', 'host_ip', 'protocol', 'mode'}) and
                port.get('target') == 8765 and str(port.get('published')) == '8765' and
                port.get('host_ip') == '127.0.0.1' and port.get('protocol', 'tcp') == 'tcp' and
                port.get('mode', 'ingress') == 'ingress' and
                live['ports'] == {'8765/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8765'}]},
                'Resolved Compose ports differ from current loopback8765')
        networks = value.get('networks', {})
        require(set(networks) == {'default'} and not app.get('network_mode') and
                app.get('networks') in ({'default': None}, {'default': {}}),
                'Resolved Compose must use only the original default bridge network')
        default = networks['default']
        require(isinstance(default, dict) and set(default).issubset({'name', 'driver', 'ipam'}) and
                default.get('ipam', {}) == {} and
                default.get('name') == self.c['project'] + '_default' and default.get('driver', 'bridge') == 'bridge',
                'Resolved Compose custom/external network is unsupported')
        preflight = self.out / 'preflight.json'
        if preflight.exists():
            from .core import read_json
            previous = read_json(preflight)
            require(live['networks'] == previous['networks'] and live['network_contract'] == previous['network_contract'],
                    'Original project network identity changed since preflight')

    def configuration(self):
        old = json.loads(self.remote(self.compose(self.c['baseline']['source'], 'config', '--format', 'json')))
        new = json.loads(self.remote(self.compose(self.source, 'config', '--format', 'json')))
        require(list(old['services']) == list(new['services']) == ['app'], 'Unexpected additional Compose services')
        require(old['services']['app']['image'] == self.c['baseline']['image'] and
                new['services']['app']['image'] == self.target, 'Compose image identity mismatch')
        live = self.inspect()
        baseline_config = self.image_info(self.c['baseline']['image'])['Config']
        candidate_config = self.image_info(self.target)['Config']
        self.compose_runtime(old, live, baseline_config)
        self.compose_runtime(new, live, candidate_config)
        for value in (old, new):
            value['services']['app'].pop('image')
            value['services']['app'].pop('build', None)  # resolved source path is expected to change
        configured_mounts = set()
        for mount in old['services']['app'].get('volumes', []):
            source = mount['source']
            if mount['type'] == 'volume':
                source = old['volumes'][source]['name']
            configured_mounts.add((mount['target'], source, not mount.get('read_only', False)))
        actual_mounts = {(v['Destination'], v.get('Name') if v['Type'] == 'volume' else v['Source'], v['RW'])
                         for v in live['mounts']}
        require(configured_mounts == actual_mounts, 'Current Compose no longer matches running volume/key mounts')
        image_env = dict(v.split('=', 1) for v in baseline_config['Env'])
        image_env.update({k: str(v) for k, v in old['services']['app'].get('environment', {}).items()})
        require(sha(json.dumps(image_env, sort_keys=True).encode()) == live['environment_sha256'],
                'Current Compose no longer matches running environment/Push key identity')
        require(old == new, 'Resolved Compose differs beyond image/build source; preserve current config, volumes and Push')
        return {'resolved_compose': 'identical except image/build source',
                'configuration_sha256': sha(json.dumps(old, sort_keys=True).encode())}

    def backup_consistent(self):
        before = self.inspect()
        self.resources(before, self.c['baseline']['image'], self.c['baseline']['image_id'])
        code = '''from pathlib import Path
import os,json,hashlib
p=Path('/backups')/NAME;p.mkdir(mode=0o700);os.chown(p,10001,10001)
fd=os.open(p/'compose.before.yaml',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'wb') as stream:stream.write(Path('/current-overlay').read_bytes())
os.chown(p/'compose.before.yaml',10001,10001)
print(json.dumps({'configuration_protected':True}))
'''.replace('NAME', repr(PurePosixPath(self.backup).name))
        self.tool(code, user='0:0', mounts=[(self.root + '/backups', '/backups', False),
            (self.c['baseline']['source'] + '/' + self.c['overlay'], '/current-overlay', True)], stage='backup')
        code = '''from pathlib import Path
import json,stat
from server.automation.backups import backup,sha,write_private
from server.accounting.db import connect
p=backup('/live/ledger.sqlite3','/evidence/pre-update')
m=json.loads((p/'manifest.json').read_text())
assert m['format_version']==FORMAT and m['database_schema']==SCHEMA
assert all(sha(p/n)==h for n,h in m['files'].items())
with connect(p/'business.sqlite3',read_only=True) as db:
 assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 assert not list(db.execute('PRAGMA foreign_key_check'))
for f in [Path('/evidence'),*Path('/evidence').rglob('*')]:
 assert f.stat().st_uid==10001 and stat.S_IMODE(f.stat().st_mode)==(0o700 if f.is_dir() else 0o600)
proof={'consistent_backup':'manifest/integrity/foreign-keys passed','schema':SCHEMA,'format':FORMAT,'permissions':'UID10001 dirs0700 files0600','live_mount':'read-only','migration':'none','restore_evidence':'inherited unchanged contract; no restore rerun'}
write_private(Path('/evidence/backup-validation.json'),proof)
print(json.dumps(proof))
'''.replace('FORMAT', repr(self.m['backup_format'])).replace('SCHEMA', repr(self.m['schema']))
        return self.tool(code, mounts=[(self.c['volume'], '/live', True),
                                      (self.backup, '/evidence', False)], stage='backup')

    def switch(self):
        state = self.inspect()
        self.resources(state, self.c['baseline']['image'], self.c['baseline']['image_id'])
        self.configuration()  # detect configuration changes since preflight/backup
        self.remote(self.compose(self.source, 'up', '-d', '--no-build', '--pull', 'never', 'app'), stage='switch')
        return {'image_id': self.image_info(self.target)['Id'], 'compose_up': 'returned', 'database_restore_or_migration': 'none'}

    def https_request(self, route, path):
        parsed = urlsplit(route['origin'])
        argv = ['curl', '--silent', '--show-error', '--fail', '--noproxy', '*', '--connect-timeout', '8',
                '--max-time', '20', '--resolve', f'{parsed.hostname}:{parsed.port or 443}:{route["address"]}']
        if route.get('interface'):
            argv += ['--interface', route['interface']]
        argv += [route['origin'].rstrip('/') + '/' + path]
        return subprocess.run(argv, capture_output=True)

    def https_preflight(self):
        # Use the executor's exact verification routes before any remote mutation, including owner/locks.
        for route in self.c['https_routes']:
            message = ('Execution environment prerequisite not met: HTTPS route ' + route['name'] +
                       '/api/health; no remote writes performed. Check the configured network/address/interface; '
                       'no route was skipped or changed.')
            try:
                result = self.https_request(route, 'api/health')
                require(result.returncode == 0, message + f' curl exit {result.returncode}.')
                healthy = json.loads(result.stdout) == {'status': 'ok'}
            except (OSError, ValueError):
                raise ReleaseError(message + ' Health response unavailable or invalid.') from None
            require(healthy, message + ' Health response mismatch.')

    def verify(self):
        image_id = self.image_info(self.target)['Id']
        for _ in range(60):
            state = self.inspect()
            self.resources(state, self.target, image_id)
            if state['health'] == 'healthy':
                break
            require(state['health'] != 'unhealthy', 'Updated container unhealthy; retain evidence, no automatic rollback')
            time.sleep(0.5)
        else:
            raise ReleaseError('Updated container health timeout; query same container, no second switch')
        code = '''import json,hashlib,urllib.request,os
from server.automation.providers import push_configured,configured_providers
assert os.getuid()==10001 and push_configured() and configured_providers()==(None,None)
with urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=3) as response:assert json.load(response)=={'status':'ok'}
for name,h in RUNTIME.items():
 with urllib.request.urlopen('http://127.0.0.1:8765/'+name,timeout=3) as response:assert hashlib.sha256(response.read()).hexdigest()==h
print(json.dumps({'HTTP_health':'ok','runtime_HTTP_SHA':'all match','Push':'same enabled config; no send','model':'none/limit0'}))
'''.replace('RUNTIME', repr(self.m['runtime']))
        # docker exec uses the running container, rather than a disconnected throw-away image.
        self.remote(self.docker + ' exec -i ' + q(self.c['container']) + ' python -', code.encode(), stage='verify')
        checks = 0
        for route in self.c['https_routes']:
            for path, expected in {'api/health': None, **self.m['runtime']}.items():
                result = self.https_request(route, path)
                require(result.returncode == 0, 'HTTPS route failed: ' + route['name'] + '/' + path + '; query same release')
                require(sha(result.stdout) == expected if expected else json.loads(result.stdout) == {'status': 'ok'},
                        'HTTPS resource/health mismatch: ' + route['name'] + '/' + path)
                checks += 1
        return {'image_id': image_id, 'health': 'healthy', 'runtime_SHA': 'all match', 'https_checks': checks,
                'real_device_or_push_delivery': 'not tested'}

    def execute(self):
        self.event('execute', 'started')
        journal = self.journal()  # First operation is query, including on a resumed invocation.
        state = self.inspect()
        if journal.get('switch', {}).get('status') in ('started', 'done'):
            candidate = self.image_info(self.target)
            self.resources(state, self.target, candidate['Id'])
        else:
            self.resources(state, self.c['baseline']['image'], self.c['baseline']['image_id'])
            require(state['health'] == 'healthy', 'Baseline not healthy')
        self.https_preflight()
        if not (self.out / 'preflight.json').exists():
            write_json(self.out / 'preflight.json', state)
        self.init_remote()
        operations = (self.upload, self.build, self.image, self.configuration, self.backup_consistent, self.switch, self.verify)
        for name, operation in zip(STAGES, operations):
            self.stage(name, operation)
        # Always query current deployed state, even when verification was completed on a previous invocation.
        current = self.inspect()
        self.resources(current, self.target, self.image_info(self.target)['Id'])
        from .core import read_json
        preflight = read_json(self.out / 'preflight.json')
        require(current['mounts'] == preflight['mounts'] and current['environment_sha256'] == preflight['environment_sha256']
                and current['networks'] == preflight['networks'] and current['network_contract'] == preflight['network_contract'],
                'Live mount/key/environment identity changed across switch')
        require(current['health'] == 'healthy', 'Completed release no longer healthy; inspect current service')
        self.remote('rmdir ' + q(self.root + '/.release-owner') + ' 2>/dev/null || '
                    '(test "$(cat ' + q(self.root + '/.release-owner/id') + ')" = ' + q(self.m['release_id']) +
                    ' && rm ' + q(self.root + '/.release-owner/id') + ' && rmdir ' + q(self.root + '/.release-owner') + ')', stage='complete')
        summary = {'status': 'deployed', 'commit': self.m['commit'], 'release_id': self.m['release_id'],
                   'image_id': current['image_id'], 'health': current['health'], 'backup': self.backup,
                   'migration': 'none', 'next': 'record facility facts; real phone acceptance remains separate'}
        write_json(self.out / 'summary.json', summary)
        self.event('execute', 'done')
        return summary
