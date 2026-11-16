"""Release runtime integrity and replacement behavior, using tiny local archives."""
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtimes(tmp_path, monkeypatch):
    filename = ROOT / 'desktop/packaging/prepare-runtimes.py'
    spec = importlib.util.spec_from_file_location('office_packaging_runtimes', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(module, 'RESOURCES', tmp_path / 'resources')
    return module


def zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_pinned_archive_replaces_stale_files_and_reuses_verified_cache(runtimes, monkeypatch):
    content = zip_bytes({'node-version/node.exe': b'pinned executable'})
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(runtimes, 'RUNTIMES', {'node': {
        'url': 'https://example.test/node.zip', 'sha256': digest,
    }})
    downloads = []

    def request(url):
        downloads.append(url)
        return io.BytesIO(content)

    monkeypatch.setattr(runtimes, 'request', request)
    stale = runtimes.RESOURCES / 'node/old.exe'
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'stale')
    destination = runtimes.install_runtime('node', runtimes.RUNTIMES)
    assert not stale.exists()
    assert (destination / 'node.exe').read_bytes() == b'pinned executable'
    assert runtimes.install_runtime('node', runtimes.RUNTIMES) == destination
    assert downloads == ['https://example.test/node.zip']


def test_wrong_digest_is_rejected_before_runtime_replacement(runtimes, monkeypatch):
    stale = runtimes.RESOURCES / 'node/keep.exe'
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'keep')
    monkeypatch.setattr(runtimes, 'request', lambda url: io.BytesIO(b'corrupt download'))
    monkeypatch.setattr(runtimes, 'RUNTIMES', {'node': {
        'url': 'https://example.test/node.zip', 'sha256': '0' * 64,
    }})
    with pytest.raises(RuntimeError, match='Checksum mismatch'):
        runtimes.install_runtime('node', runtimes.RUNTIMES)
    assert stale.read_bytes() == b'keep'
    assert not (runtimes.CACHE / 'node.zip').exists()


def test_runtime_cleanup_and_archive_cannot_escape_resources(runtimes, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    with pytest.raises(ValueError, match='outside resources'):
        runtimes.reset_runtime(outside)
    archive = tmp_path / 'unsafe.zip'
    archive.write_bytes(zip_bytes({'../escaped.txt': b'unsafe'}))
    with pytest.raises(ValueError, match='escapes'):
        runtimes.extract_zip(archive, runtimes.RESOURCES)
    assert outside.is_dir()
    assert not (tmp_path / 'escaped.txt').exists()


def test_build_and_runtime_versions_have_one_explicit_contract(runtimes):
    package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    client = json.loads((ROOT / 'client/package.json').read_text(encoding='utf-8'))
    assert package['engines'] == client['engines']
    assert package['engines']['node'] == (ROOT / '.node-version').read_text().strip()
    assert package['engines']['node'] == runtimes.RUNTIMES['node']['version']
    for runtime in runtimes.RUNTIMES.values():
        assert runtime['version'] in runtime['url']
        assert '/latest' not in runtime['url']
        assert len(bytes.fromhex(runtime['sha256'])) == 32
