"""Opt-in relocation without rewriting content-addressed training evidence.

An immutable NFS profile maps old absolute roots to verified hot copies.
Missing hot files never fall back to cold storage. No profile means no change.
"""
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

ENVIRONMENT = 'BEHAVE_RUNTIME_LOCATIONS'
DEFAULT_PROFILE = None
EXPECTED_PROFILE_SHA256 = None


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path == Path(path.anchor):
        raise ValueError('runtime locations require explicit non-root absolute paths')
    return path


@lru_cache(maxsize=8)
def _profile(filename):
    path = _absolute(filename)
    if path.resolve() != path:
        raise ValueError('runtime location profile cannot traverse symlinks')
    data = path.read_bytes()
    if EXPECTED_PROFILE_SHA256 is not None and hashlib.sha256(data).hexdigest() != EXPECTED_PROFILE_SHA256:
        raise ValueError('runtime location profile differs from the frozen snapshot')
    row = json.loads(data)
    expected={'schema','mappings','deny_unmapped_roots'}
    if row.get('schema')=='behave.runtime-locations/2':expected.add('direct_roots')
    if set(row) != expected or row['schema'] not in ('behave.runtime-locations/1','behave.runtime-locations/2'):
        raise ValueError('invalid runtime location profile')
    direct=tuple(_absolute(value) for value in row.get('direct_roots',()))
    if len(set(direct))!=len(direct) or any(root.resolve()!=root for root in direct):
        raise ValueError('direct runtime roots must be unique non-symlink directories')
    mappings = []
    for item in row['mappings']:
        if set(item) != {'source', 'destination'}:
            raise ValueError('invalid runtime location mapping')
        source, destination = _absolute(item['source']), _absolute(item['destination'])
        if source == destination or destination.resolve() != destination:
            raise ValueError('runtime destination must be a different, non-symlink directory')
        mappings.append((source, destination))
    if len({source for source, _ in mappings}) != len(mappings):
        raise ValueError('duplicate runtime location source')
    if any(destination.is_relative_to(source) for _, destination in mappings for source, _ in mappings):
        raise ValueError('chained runtime location mappings are forbidden')
    denied = tuple(_absolute(value) for value in row['deny_unmapped_roots'])
    return tuple(sorted(mappings, key=lambda pair:len(pair[0].parts), reverse=True)), denied, direct, hashlib.sha256(data).hexdigest()


def runtime_path(value):
    path = Path(value).absolute()
    filename = os.environ.get(ENVIRONMENT) or DEFAULT_PROFILE
    if not filename:
        return path
    if '..' in path.parts:
        raise ValueError('runtime path cannot contain parent traversal')
    mappings, denied, direct, _ = _profile(filename)
    if any(path.is_relative_to(root) for root in direct):
        if path.resolve()!=path:raise ValueError('direct runtime path traverses a symlink')
        return path
    for source, destination in mappings:
        if path.is_relative_to(source):
            target = destination / path.relative_to(source)
            if target.resolve() != target:
                raise ValueError('mapped runtime path traverses a symlink')
            return target
    if any(path.is_relative_to(root) for root in denied):
        raise ValueError('unmapped cold/legacy runtime path: ' + str(path))
    return path


def profile_identity():
    filename = os.environ.get(ENVIRONMENT) or DEFAULT_PROFILE
    return None if not filename else {'path':filename, 'sha256':_profile(filename)[3]}
