"""Read a fixed Git tree, with no checkout, index changes, fetch, or runtime."""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import subprocess

from .models import relative_path, safe_segment


@dataclass(frozen=True)
class Blob:
    mode: str
    data: bytes


class GitTree:
    def __init__(self, repo: Path, revision: str):
        self.repo = Path(repo).resolve()
        self.commit = self.git('rev-parse', '--verify', '--end-of-options', revision + '^{commit}').decode().strip()
        entries = self.git('ls-tree', '-rz', '--full-tree', self.commit).split(b'\0')
        records = []
        for entry in entries:
            if not entry:
                continue
            header, path = entry.split(b'\t', 1)
            mode, kind, oid = header.decode().split()
            path = path.decode('utf-8')
            relative_path(path)
            records.append((path, mode, kind, oid))
        ids = list(dict.fromkeys(oid for _, _, kind, oid in records if kind == 'blob'))
        payload = ('\n'.join(ids) + '\n').encode() if ids else b''
        stream = io.BytesIO(self.git('cat-file', '--batch', input=payload))
        contents = {}
        for oid in ids:
            header = stream.readline().decode().split()
            if len(header) != 3 or header[:2] != [oid, 'blob']:
                raise ValueError(f'invalid Git object response for {oid}')
            size = int(header[2])
            contents[oid] = stream.read(size)
            if stream.read(1) != b'\n':
                raise ValueError('truncated Git object response')
        self.files = {p: Blob(mode, contents.get(oid, b'')) for p, mode, _, oid in records}

    def git(self, *args: str, input: bytes | None = None) -> bytes:
        result = subprocess.run(['git', '--no-optional-locks', '-C', str(self.repo), *args],
                                input=input, capture_output=True)
        if result.returncode:
            raise ValueError(f'Git read failed ({self.repo}, {args[0]}): {result.stderr.decode(errors="replace").strip()}')
        return result.stdout

    def bytes(self, path: str) -> bytes:
        relative_path(path)
        blob = self.files.get(path)
        if blob is None:
            raise ValueError(f'{self.repo}@{self.commit}: missing {path}')
        if blob.mode not in {'100644', '100755'}:
            raise ValueError(f'unsupported Git entry {path}: mode {blob.mode}')
        return blob.data

    def json(self, path: str) -> dict:
        value = json.loads(self.bytes(path))
        if not isinstance(value, dict):
            raise ValueError(f'expected object in {path}')
        return value

    def release(self, release_id: str | None = None) -> dict:
        pointer = self.json('.lean_constellation/repo_publication.json').get('latest_release_id')
        release_id = safe_segment(release_id or pointer or '')
        path = f'.lean_constellation/releases/{release_id}.json'
        release = self.json(path)
        if release.get('release_id') != release_id:
            raise ValueError(f'Release identity mismatch: {release_id}')
        ref = f'refs/lean-constellation/releases/{release_id}'
        ref_lines = self.git('for-each-ref', '--format=%(refname) %(objectname)', ref).decode().splitlines()
        refs = [line.split()[1] for line in ref_lines if line.split()[0] == ref]
        if refs:
            if refs != [self.commit]:
                raise ValueError(f'commit does not match Release ref: {release_id}')
        else:
            if pointer != release_id:
                raise ValueError('fixed tree publication pointer differs from selected Release')
            parents = self.git('cat-file', '-p', self.commit).decode().split('\n\n', 1)[0].splitlines()
            for line in parents:
                if line.startswith('parent '):
                    parent = line.split()[1]
                    # ls-tree fails for missing parent objects; it does not treat shallow history as proof.
                    if self.git('ls-tree', parent, '--', path).strip():
                        raise ValueError('commit is not the first introduction of this Release manifest')
        return release


def exact_commit(value: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise ValueError(f'expected full fixed Git commit, got {value!r}')
    return value
