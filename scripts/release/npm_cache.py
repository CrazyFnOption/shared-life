"""Assemble npm's content cache from integrity-locked registry tarballs."""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


NAS_PLATFORM = ('linux', 'x64', 'glibc')
CONTEXT_PREFIX = 'offline-npm-cache/'


def host_platform():
    machine = platform.machine()
    cpu = {'aarch64': 'arm64', 'x86_64': 'x64', 'AMD64': 'x64'}.get(
        machine, machine)
    libc = (platform.libc_ver()[0] or 'glibc') if sys.platform == 'linux' else ''
    return sys.platform, cpu, libc


def supports(values, target):
    if not values:
        return True
    if isinstance(values, str):
        values = [values]
    if values == ['any']:
        return True
    allowed = [value for value in values if not value.startswith('!')]
    return '!' + target not in values and (not allowed or target in allowed)


def locked_tarballs(lock, target):
    root = json.loads(lock)
    if root.get('lockfileVersion') != 3:
        raise ValueError('Expected npm lockfile version 3')
    tarballs = {}
    for name, package in root['packages'].items():
        if not name or not all(supports(package.get(key), value)
                               for key, value in zip(('os', 'cpu', 'libc'), target)):
            continue
        url = package.get('resolved', '')
        address = urlsplit(url)
        integrity = re.fullmatch(r'(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})', package.get('integrity', ''))
        if (package.get('link') or address.scheme != 'https' or address.netloc != 'registry.npmjs.org'
                or not address.path.endswith('.tgz') or address.query or address.fragment or not integrity):
            raise ValueError('Expected a locked npm registry tarball with integrity for ' + name)
        algorithm, encoded = integrity.groups()
        digest = base64.b64decode(encoded, validate=True).hex()
        if len(digest) != hashlib.new(algorithm).digest_size * 2:
            raise ValueError('Invalid npm integrity digest for ' + name)
        relative = f'_cacache/content-v2/{algorithm}/{digest[:2]}/{digest[2:4]}/{digest[4:]}'
        tarballs[relative] = url
    return tarballs


def valid_content(relative, data):
    _, _, algorithm, *digest = relative.split('/')
    return hashlib.new(algorithm, data).hexdigest() == ''.join(digest)


class NoRegistryRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected npm registry redirect for ' + req.full_url)


def download(url):
    with build_opener(NoRegistryRedirect()).open(url, timeout=30) as response:
        return response.read()


def fill_cache(tarballs, cache, seed_cache):
    """Use the shared release cache first, then npm's cache, then the registry."""
    for relative, url in tarballs.items():
        destination = cache / relative
        if destination.is_file() and valid_content(relative, destination.read_bytes()):
            continue
        seed = seed_cache / relative
        data = seed.read_bytes() if seed.is_file() else None
        if data is None or not valid_content(relative, data):
            data = download(url)
        if not valid_content(relative, data):
            raise ValueError('Downloaded npm tarball fails lockfile integrity: ' + url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A concurrent prepare can only replace this path with the same verified bytes.
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(data)
                output.close()
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)


def context_cache(tarballs, cache):
    return {CONTEXT_PREFIX + relative: ((cache / relative).read_bytes(), 0o644)
            for relative in tarballs}


def verify_context_cache(tarballs, context):
    cached = {name for name in context if name.startswith(CONTEXT_PREFIX)}
    if cached != {CONTEXT_PREFIX + relative for relative in tarballs}:
        raise ValueError('Bundled npm cache differs from the Linux lockfile tarball set')
    for relative in tarballs:
        data, mode = context[CONTEXT_PREFIX + relative]
        if mode != 0o644 or not valid_content(relative, data):
            raise ValueError('Bundled npm tarball fails lockfile integrity: ' + relative)
