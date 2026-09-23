"""Batch Lean checks with persistent Lake artifacts and bounded diagnostics."""
import re
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from lean_constellation.domain.restructure import DeclStatus, DeclarationSection, FormalSnapshot, RestructureStage, file_digest, utc_now_iso
from .store import metadata_lock


def diagnostic_summary(output):
    """Keep multiline compiler errors, omit progress and replayed warnings."""
    blocks, current = [], []
    for line in output.splitlines():
        if re.match(r"(?:error: |.*?:\d+:\d+: error:)", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current and not re.match(r"[✔⚠✖]|warning:|Build completed|Some required", line):
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return list(dict.fromkeys(blocks))


def source_versions(service, root):
    versions = {}
    for path in service.builds.input_files(root):
        if path.is_symlink():
            raise ValueError("build inputs must not be symlinks")
        if path.is_file():
            stat = path.stat()
            versions[path.relative_to(root).as_posix()] = [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino]
    return versions


def sync_sources(service, directory, root, sources):
    """Stat-based preflight synchronization; final frozen builds still hash content."""
    metadata = service.store.repo_metadata_root(directory)
    work = metadata / 'build_work'
    work.mkdir(parents=True, exist_ok=True)
    index = metadata / 'check_sync.json'
    previous = json.loads(index.read_text()) if index.exists() else {}
    if any(key in previous and previous[key]['source'] != sources.get(key)
           for key in ['lakefile.toml', 'lakefile.lean', 'lean-toolchain']):
        if 'lake-manifest.json' not in sources:
            (work / 'lake-manifest.json').unlink(missing_ok=True)
    for path in service.builds.input_files(work):
        relative = path.relative_to(work).as_posix()
        if path.is_file() and relative not in sources and relative != 'lake-manifest.json':
            path.unlink()
    updated = {}
    for relative, stamp in sources.items():
        destination = work / relative
        old = previous.get(relative, {})
        stat = destination.stat() if destination.exists() else None
        target = [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size] if stat else None
        if old.get('source') != stamp or old.get('target') != target:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # A formal build may already have synchronized this source.
            if not destination.exists() or destination.read_bytes() != (root / relative).read_bytes():
                shutil.copy2(root / relative, destination)
            stat = destination.stat()
            target = [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size]
        updated[relative] = dict(source=stamp, target=target)
    index.write_text(json.dumps(updated))
    return work


def check_files(service, directory, files):
    """Synchronize live sources once; Lake updates prerequisites and selected targets."""
    if not files:
        raise ValueError("at least one Lean file is required")
    root = service.store.repo_root(directory)
    for relative in files:
        path = (root / relative).resolve()
        if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.is_relative_to(root) or not path.is_file() or path.suffix != '.lean' or any(
            part in {'.lake', '.lean_constellation', '.agent_runtime', '.git'} for part in Path(relative).parts
        ) or relative == 'lakefile.lean':
            raise ValueError(f"not a project Lean file: {relative}")
    with metadata_lock(service.store.repo_metadata_root(directory) / 'build.lock'):
        with service.store._lock:
            sources = source_versions(service, root)
            operation = 'check_' + uuid.uuid4().hex
            work = sync_sources(service, directory, root, sources)
        log = service.store.repo_metadata_root(directory) / 'builds' / (operation + '.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        modules = list(dict.fromkeys('+' + f[:-5].replace('/', '.') for f in files))
        with log.open('w') as handle:
            proc = subprocess.run(['lake', 'build', *modules], cwd=work, stdout=handle,
                                  stderr=subprocess.STDOUT, check=False)
        output = log.read_text(errors='replace')
        errors = diagnostic_summary(output)
        with service.store._lock:
            current = source_versions(service, root)
            changed = sources != current
        if changed:
            errors.append('Sources changed during compilation; check the current sources again.')
        if proc.returncode and not errors:
            errors.append('Lake failed without a parsed compiler diagnostic; inspect the full report.')
        module_states = {}
        for relative in files:
            module = relative[:-5].replace('/', '.')
            if proc.returncode == 0 and not changed:
                state = 'passed'
            elif re.search(r'error: ' + re.escape(relative) + r':\d+:', output):
                state = 'failed'
            else:
                state = 'not_confirmed'
            module_states[relative] = state
        return dict(passed=proc.returncode == 0 and not changed, files=files, module_states=module_states,
                    errors=[e[:6000] for e in errors[:20]], remaining_errors=max(0, len(errors)-20),
                    report_id=operation, warning_count=sum('warning:' in l for l in output.splitlines()),
                    _sources=sources)


def check_content(service, directory, nodes, stage, *, allow_declared_repair=False):
    """Compile candidates, then register only the exact sources that passed."""
    stage = RestructureStage(stage)
    with service.store._lock:
        from .layout import refresh_projections
        files = []
        refresh_projections(service.store, directory, nodes[0] if len(nodes) == 1 else None)
        for node in nodes:
            work, _ = service.content.load(directory, node)
            files.extend(d.file for d in work.decls.values())
            files.extend(work.support_files)
    result = check_files(service, directory, list(dict.fromkeys(files)))
    sources = result.pop('_sources')
    if not result['passed']:
        return result
    with service.store._lock:
        root = service.store.repo_root(directory)
        current = source_versions(service, root)
        if sources != current:
            result.update(passed=False, errors=['Sources changed before registration; check again.'])
            return result
        issues = []
        candidates = []
        for node in nodes:
            work, version = service.content.load(directory, node)
            for decl in work.decls.values():
                data = (root / decl.file).read_bytes()
                if stage in {RestructureStage.PROVED, RestructureStage.FINAL} and decl.is_theorem_like:
                    if decl.proof is None:
                        decl.proof = DeclarationSection()
                    decl.proof.formal = FormalSnapshot(code=data.decode("utf-8"))
                elif stage is RestructureStage.DECLARED or not decl.is_theorem_like:
                    decl.statement.formal = FormalSnapshot(code=data.decode("utf-8"))
                decl.file_digest = file_digest(data)
                decl.captured_at = utc_now_iso()
                decl.status = DeclStatus.PROVED if decl.is_theorem_like and stage in {RestructureStage.PROVED, RestructureStage.FINAL} else DeclStatus.DECLARED
            candidates.append((node, work, version))
            issues.extend(service.content.check_submission(directory, node, stage=stage,
                allow_declared_repair=allow_declared_repair, work=work))
        if not issues:
            for node, work, version in candidates:
                service.store.save_content(directory, node, work, expected_version=version)
        result['errors'].extend(issues[:20])
        result['remaining_errors'] += max(0, len(issues)-20)
        result['passed'] = not issues
    return result
