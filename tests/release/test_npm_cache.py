"""Lockfile-bound npm cache tests without registry or NAS access."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.release import core, npm_cache


def package(name, data, **platform):
    integrity = base64.b64encode(hashlib.sha512(data).digest()).decode()
    return {
        'resolved': f'https://registry.npmjs.org/{name}/-/{name}-1.0.0.tgz',
        'integrity': 'sha512-' + integrity,
        **platform,
    }


def lockfile(packages, version='1.0.0'):
    records = {f'node_modules/{name}': record for name, record in packages.items()}
    return json.dumps({'lockfileVersion': 3, 'version': version, 'packages': {'': {'version': version}, **records}})


class NpmCacheTests(unittest.TestCase):
    def test_version_only_reuses_packages_and_new_lock_fetches_only_missing_content(self):
        linux = package('linux', b'linux', os=['linux'], cpu='any')
        darwin = package('darwin', b'darwin', os=['darwin'])
        new = package('new', b'new')
        selected = lambda packages, version: npm_cache.locked_tarballs(lockfile(packages, version), npm_cache.NAS_PLATFORM)
        initial = {'linux': linux, 'darwin': darwin}
        first = selected(initial, '1.0.0')
        second = selected(initial, '1.0.1')
        third = selected({**initial, 'new': new}, '1.0.2')
        self.assertEqual(first, second)
        self.assertEqual(list(first.values()), [linux['resolved']])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            cache, seed = path / 'release', path / 'existing'
            seeded = seed / next(iter(first))
            seeded.parent.mkdir(parents=True)
            seeded.write_bytes(b'linux')
            with patch.object(npm_cache, 'download', return_value=b'new') as fetch:
                npm_cache.fill_cache(first, cache, seed)
                npm_cache.fill_cache(second, cache, seed)
                fetch.assert_not_called()
                npm_cache.fill_cache(third, cache, seed)
                fetch.assert_called_once_with(new['resolved'])
            context = core.add_offline_npm({
                'Dockerfile.offline': (b'RUN npm ci\n', 0o644),
                'Dockerfile.offline.dockerignore': (b'**\n', 0o644),
            }, npm_cache.context_cache(third, cache))
            self.assertIn(b'RUN --network=none npm ci --offline ', context['Dockerfile.offline'][0])
            npm_cache.verify_context_cache(third, context)
            member = npm_cache.CONTEXT_PREFIX + next(iter(first))
            context[member] = (b'wrong', 0o644)
            with self.assertRaisesRegex(ValueError, 'fails lockfile integrity'):
                npm_cache.verify_context_cache(third, context)
            del context[member]
            with self.assertRaisesRegex(ValueError, 'differs from the Linux lockfile'):
                npm_cache.verify_context_cache(third, context)

    def test_bad_cache_and_bad_download_fail_without_installing_unverified_content(self):
        record = package('linux', b'expected')
        tarballs = npm_cache.locked_tarballs(lockfile({'linux': record}), npm_cache.NAS_PLATFORM)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / 'release'
            cached = cache / next(iter(tarballs))
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b'corrupt')
            seed = Path(temporary) / 'existing'
            with patch.object(npm_cache, 'download', return_value=b'invalid'):
                with self.assertRaisesRegex(ValueError, 'Downloaded npm tarball fails'):
                    npm_cache.fill_cache(tarballs, cache, seed)
            self.assertEqual(cached.read_bytes(), b'corrupt')
            with patch.object(npm_cache, 'download', return_value=b'expected'):
                npm_cache.fill_cache(tarballs, cache, seed)
            self.assertEqual(cached.read_bytes(), b'expected')

    def test_lock_rejects_nonregistry_and_invalid_integrity(self):
        record = package('bad', b'placeholder')
        for change in ({'resolved': 'https://registry.npmjs.org.evil/bad.tgz'}, {'integrity': 'sha512-YQ=='}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                npm_cache.locked_tarballs(lockfile({'bad': {**record, **change}}), npm_cache.NAS_PLATFORM)
