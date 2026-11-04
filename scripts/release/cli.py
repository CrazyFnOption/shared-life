"""Small JSON-output release CLI."""
import argparse
import fcntl
import json
from pathlib import Path
import sys

from .core import ReleaseError, config_load, prepare, verify_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'plan', 'dry-run', 'execute', 'status'])
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit')
    parser.add_argument('--baseline')
    parser.add_argument('--approve', help='Full prepared commit; required for remote writes')
    args = parser.parse_args()
    try:
        config = config_load(args.repo, args.config)
        if args.command == 'prepare':
            if not args.commit or not args.baseline:
                parser.error('prepare requires --commit and --baseline')
            manifest = prepare(args.repo, args.commit, args.baseline, config, args.output)
            result = {'status': 'prepared', 'commit': manifest['commit'], 'release_id': manifest['release_id'],
                      'source_files': len(manifest['source_files']), 'runtime_files': len(manifest['runtime']),
                      'changed_files': manifest['changed_files'], 'network': 'none'}
        else:
            manifest = verify_bundle(args.output, config, args.repo)
            from .runner import Runner
            runner = Runner(config, manifest, args.output)
            if args.command in ('plan', 'dry-run'):
                result = runner.plan()
            elif args.command == 'status':
                result = runner.status()
            else:
                if args.approve != manifest['commit']:
                    raise ReleaseError('execute requires --approve equal to the full prepared commit')
                with (args.output / '.execute.lock').open('w') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise ReleaseError('Another local execute is active for this output')
                    result = runner.execute()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ReleaseError, OSError, ValueError, KeyError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
