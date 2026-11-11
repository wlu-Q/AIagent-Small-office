"""Text patches using the public Codex Begin/Add/Update/Delete/Move format.

Independent parser; no shell, fuzzy matching, file locks or model-specific API.
All files are planned before mutation. Disk failures can still partially apply.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_PATCH_BYTES = 80 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024


@dataclass
class Chunk:
    anchor: str | None = None
    lines: list[str] = field(default_factory=list)
    eof: bool = False


@dataclass
class Edit:
    kind: str
    path: str
    destination: str | None = None
    chunks: list[Chunk] = field(default_factory=list)
    content: str = ''

    @property
    def paths(self):
        return [self.path] + ([self.destination] if self.destination else [])


def parse_patch(patch: str) -> list[Edit]:
    if not isinstance(patch, str) or len(patch.encode('utf-8')) > MAX_PATCH_BYTES:
        raise ValueError(f'Patch exceeds {MAX_PATCH_BYTES} bytes; split edits and re-read between calls')
    lines = patch.strip('\r\n').replace('\r\n', '\n').split('\n')
    if not lines or lines[0] != '*** Begin Patch' or lines[-1] != '*** End Patch':
        raise ValueError('Patch must start with *** Begin Patch and end with *** End Patch')
    edits, i = [], 1
    while i < len(lines) - 1:
        match = re.fullmatch(r'\*\*\* (Add|Update|Delete) File: (.+)', lines[i])
        if not match:
            raise ValueError(f'Expected Add/Update/Delete File header at patch line {i + 1}')
        edit = Edit(match[1].lower(), match[2])
        edits.append(edit)
        i += 1
        if edit.kind == 'update' and lines[i].startswith('*** Move to: '):
            edit.destination = lines[i][13:]
            i += 1
        while i < len(lines) - 1 and not lines[i].startswith(('*** Add File:', '*** Update File:', '*** Delete File:')):
            line = lines[i]
            if edit.kind == 'add' and line.startswith('+'):
                edit.content += line[1:] + '\n'
            elif edit.kind == 'update':
                if line == '@@' or line.startswith('@@ '):
                    edit.chunks.append(Chunk(line[3:] if line.startswith('@@ ') else None))
                elif line == '*** End of File' and edit.chunks:
                    edit.chunks[-1].eof = True
                elif line and line[0] in ' +-' and edit.chunks and not edit.chunks[-1].eof:
                    edit.chunks[-1].lines.append(line)
                else:
                    raise ValueError(f'Invalid update at patch line {i + 1}; use @@ and prefixed context/-old/+new lines')
            else:
                raise ValueError(f'Invalid {edit.kind} body at patch line {i + 1}')
            i += 1
        if edit.kind == 'update' and (not edit.chunks and not edit.destination or any(not c.lines for c in edit.chunks)):
            raise ValueError('Update requires changes or a Move to destination')
    if not edits:
        raise ValueError('Patch contains no files')
    seen = set()
    for edit in edits:
        for path in edit.paths:
            # Reject Windows aliases/ADS/devices as well as traversal on all hosts.
            parts = path.replace('\\', '/').split('/')
            if (not path or '\\' in path or not path.startswith('/') or
                any(p in {'.', '..'} or p.rstrip(' .') != p or ':' in p or
                    re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?', p) or
                    p.lower() in {'.git', 'git~1'} for p in parts) or
                any(ord(c) < 32 for c in path) or any(c in path for c in '<>"|?*')):
                raise ValueError('Patch paths must be absolute virtual file paths without traversal, metadata or Windows aliases')
            key = path.casefold()
            if key in seen:
                raise ValueError(f'Duplicate patch target: {path}; combine changes in one Update File')
            seen.add(key)
    return edits


def update_text(text: str, edit: Edit) -> str:
    bom = '\ufeff' if text.startswith('\ufeff') else ''
    text = text.removeprefix(bom) if bom else text
    lines = text.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in text else '\n'
    cursor = 0
    for chunk in edit.chunks:
        values = [line.rstrip('\r\n') for line in lines]
        start = cursor
        if chunk.anchor is not None:
            matches = [i for i in range(cursor, len(values)) if values[i] == chunk.anchor]
            if not matches:
                raise ValueError(f'Anchor not found in {edit.path}; re-read and provide exact context')
            start = matches[0] + 1
        old = [line[1:] for line in chunk.lines if line[0] != '+']
        if old:
            matches = [i for i in range(start, len(values) - len(old) + 1)
                       if values[i:i + len(old)] == old and (not chunk.eof or i + len(old) == len(values))]
            if len(matches) != 1:
                raise ValueError(f'Context missing or ambiguous in {edit.path}; re-read and include unique context')
            index = matches[0]
        else:
            index = len(lines)  # Codex insertion-only chunk appends.
        replaced, offset = [], index
        for line in chunk.lines:
            if line[0] == ' ':
                replaced.append(lines[offset])
                offset += 1
            elif line[0] == '-':
                offset += 1
            else:
                replaced.append(line[1:] + newline)
        # Preserve no-final-newline for edits; insertions create a line boundary.
        if index and not lines[index - 1].endswith(('\n', '\r')):
            lines[index - 1] += newline
        if old and index + len(old) == len(lines) and text and not text.endswith(('\n', '\r')) and replaced:
            replaced[-1] = replaced[-1].removesuffix(newline)
        for position in range(len(replaced) - 1):
            if not replaced[position].endswith(('\n', '\r')):
                replaced[position] += newline
        lines[index:index + len(old)] = replaced
        cursor = index + len(replaced)
    return bom + ''.join(lines)


def _read(path: Path) -> bytes | None:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if not path.is_file() or size > MAX_FILE_BYTES:
        raise ValueError('Patch supports regular text files up to 8 MiB')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES or b'\x00' in data:
        raise ValueError('Binary or oversized file cannot be patched')
    data.decode('utf-8')  # Invalid encoding fails before any mutation.
    return data


def _write(path: Path, content: bytes, *, create: bool, mode: int | None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.office-patch-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(name, mode)
        if create:
            # Publish complete bytes exclusively; never truncate an unexpected file.
            os.link(name, path)
        else:
            os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def apply_edits(edits, *, resolve, cancelled=lambda: False, verify=lambda path: None):
    """Return real mutations; never roll back other files or claim atomic batches."""
    applied, plans, touched = [], [], []
    try:
        for edit in edits:
            resolved = [resolve(p) for p in edit.paths]
            for path in resolved:
                identity = Path(str(path).casefold())
                if any(identity == other or identity in other.parents or other in identity.parents for other in touched):
                    raise ValueError('Patch targets overlap or resolve to the same file')
                touched.append(identity)
            source = resolved[0]
            before = _read(source)
            if edit.kind == 'add' and before is not None:
                raise ValueError(f'File already exists: {edit.path}; use Update File after read_file')
            if edit.kind != 'add' and before is None:
                raise ValueError(f'File not found: {edit.path}')
            verify(edit.path)
            if edit.destination and resolved[1].exists():
                raise ValueError(f'Move destination already exists: {edit.destination}')
            after = (edit.content if edit.kind == 'add' else update_text(before.decode('utf-8'), edit)).encode('utf-8') if edit.kind != 'delete' else None
            if after is not None and (len(after) > MAX_FILE_BYTES or b'\x00' in after):
                raise ValueError('Result is binary or exceeds 8 MiB')
            plans.append((edit, resolved, before, after))
        for edit, resolved, before, after in plans:
            if cancelled():
                raise RuntimeError('Operation cancelled; remaining files were not modified')
            if [resolve(p) for p in edit.paths] != resolved or _read(resolved[0]) != before:
                raise ValueError(f'File changed during patch: {edit.path}; re-read before retry')
            source = resolved[0]
            mode = stat.S_IMODE(source.stat().st_mode) if before is not None else None
            def write_target(target, virtual, create):
                change = {'action': 'created' if create else 'modified', 'path': virtual}
                try:
                    _write(target, after, create=create, mode=mode)
                except Exception:
                    # Publication may have succeeded before temporary-file cleanup
                    # failed. Report the actual file rather than an empty receipt.
                    try:
                        if _read(target) == after:
                            applied.append(change)
                    except Exception:
                        pass
                    raise
                applied.append(change)
            if edit.kind == 'delete':
                source.unlink()
                applied.append({'action': 'deleted', 'path': edit.path})
            elif edit.destination:
                write_target(resolved[1], edit.destination, True)
                source.unlink()
                applied.append({'action': 'deleted', 'path': edit.path})
            else:
                write_target(source, edit.path, before is None)
        return {'status': 'success', 'applied': applied}
    except Exception as exc:
        return {'status': 'partial' if applied else 'error', 'applied': applied, 'error': str(exc)}
