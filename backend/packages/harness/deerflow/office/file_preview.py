"""Bounded, read-only presentation of an already authorized local file."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

TEXT_LIMIT = 2 * 1024 * 1024
IMAGE_LIMIT = 10 * 1024 * 1024
_LANGUAGES = {
    'js': 'javascript', 'jsx': 'javascript', 'mjs': 'javascript', 'cjs': 'javascript',
    'ts': 'typescript', 'tsx': 'typescript', 'css': 'css', 'scss': 'scss', 'less': 'less',
    'html': 'html', 'htm': 'html', 'vue': 'html', 'svelte': 'html', 'xml': 'xml', 'svg': 'xml',
    'md': 'markdown', 'markdown': 'markdown', 'json': 'json', 'jsonc': 'json',
    'yaml': 'yaml', 'yml': 'yaml', 'py': 'python', 'java': 'java', 'sql': 'sql',
    'sh': 'shell', 'bash': 'shell', 'ps1': 'powershell', 'go': 'go', 'rs': 'rust',
    'c': 'c', 'h': 'c', 'cpp': 'cpp', 'hpp': 'cpp', 'cs': 'csharp', 'php': 'php',
    'txt': 'plaintext', 'log': 'plaintext', 'csv': 'plaintext', 'toml': 'plaintext',
    'ini': 'ini', 'conf': 'plaintext', 'gitignore': 'plaintext', 'env': 'plaintext',
}
_IMAGES = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
           'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp'}


def _image_matches(payload: bytes, mime: str) -> bool:
    return {
        'image/png': payload.startswith(b'\x89PNG\r\n\x1a\n'),
        'image/jpeg': payload.startswith(b'\xff\xd8\xff'),
        'image/gif': payload.startswith((b'GIF87a', b'GIF89a')),
        'image/webp': payload.startswith(b'RIFF') and payload[8:12] == b'WEBP',
        'image/bmp': payload.startswith(b'BM'),
    }.get(mime, False)


def preview_file(path: Path, *, metadata_only: bool = False) -> dict[str, Any]:
    suffix = path.suffix.lower().lstrip('.')
    language = _LANGUAGES.get(suffix, 'plaintext')
    kind = 'image' if suffix in _IMAGES else 'text' if suffix in _LANGUAGES or not suffix else 'unsupported'
    size = path.stat().st_size
    result: dict[str, Any] = {'path': str(path), 'name': path.name, 'size': size,
                              'kind': kind, 'language': language}
    limit = IMAGE_LIMIT if kind == 'image' else TEXT_LIMIT
    if kind == 'unsupported':
        return {**result, 'reason': '此格式暂不支持预览，可在文件夹中打开。'}
    if size > limit:
        return {**result, 'kind': 'unsupported', 'reason': f'文件超过 {limit // (1024 * 1024)} MB，暂不支持预览。'}
    if metadata_only:
        return result
    # Read at most the limit even when a file grows after stat. Never execute HTML/SVG.
    with path.open('rb') as source:
        payload = source.read(limit + 1)
    if len(payload) > limit:
        return {**result, 'kind': 'unsupported', 'reason': '文件内容已增大，超过预览大小限制。'}
    result['size'] = len(payload)
    if kind == 'image':
        mime = _IMAGES[suffix]
        if not _image_matches(payload, mime):
            return {**result, 'kind': 'unsupported', 'reason': '文件内容不是有效的对应图片格式。'}
        return {**result, 'dataUrl': f'data:{mime};base64,{base64.b64encode(payload).decode("ascii")}'}
    try:
        encoding = 'utf-16' if payload.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        content = payload.decode(encoding)
    except UnicodeDecodeError:
        try:
            content = payload.decode('gb18030')
        except UnicodeDecodeError:
            return {**result, 'kind': 'unsupported', 'reason': '无法识别文件编码，可在文件夹中打开。'}
    if any(ord(char) < 32 and char not in '\n\r\t\f' for char in content):
        return {**result, 'kind': 'unsupported', 'reason': '此文件包含二进制内容，无法作为文本预览。'}
    return {**result, 'content': content}
