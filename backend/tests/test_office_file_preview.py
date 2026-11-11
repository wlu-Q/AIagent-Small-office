"""Actual file preview contents, limits and HTTP ownership boundaries."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import office
from deerflow.config.paths import Paths
from deerflow.office.file_preview import IMAGE_LIMIT, TEXT_LIMIT, preview_file
from deerflow.office.projects import OfficeProjectService


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'gb18030'])
def test_preview_chinese_text_current_bytes_and_metadata(tmp_path, encoding):
    target = tmp_path / '中文说明.md'
    target.write_text('# 中文文档', encoding=encoding)
    metadata = preview_file(target, metadata_only=True)
    assert 'content' not in metadata
    assert metadata['size'] == target.stat().st_size
    assert metadata['language'] == 'markdown'
    assert preview_file(target)['content'] == '# 中文文档'
    target.write_text('更新后的内容', encoding=encoding)
    assert preview_file(target)['content'] == '更新后的内容'


@pytest.mark.parametrize('name,content,kind', [
    ('index.html', b'<script>alert(1)</script>', 'text'),
    ('image.svg', b'<svg onload="alert(1)"></svg>', 'text'),
    ('binary.txt', b'abc\x00def', 'unsupported'),
    ('bad.png', b'<script>alert(1)</script>', 'unsupported'),
    ('document.docx', b'PK\x00\x00', 'unsupported'),
    ('unknown.dat', b'content', 'unsupported'),
    ('empty.txt', b'', 'text'),
])
def test_content_types_do_not_execute_or_mislabel_binary(tmp_path, name, content, kind):
    target = tmp_path / name
    target.write_bytes(content)
    data = preview_file(target)
    assert data['kind'] == kind
    if kind == 'unsupported':
        assert data['reason']
        assert 'content' not in data and 'dataUrl' not in data


def test_images_are_local_and_bounded(tmp_path):
    import base64
    content = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
    target = tmp_path / 'test.png'
    target.write_bytes(content)
    assert preview_file(target)['dataUrl'] == 'data:image/png;base64,' + base64.b64encode(content).decode()
    assert 'dataUrl' not in preview_file(target, metadata_only=True)
    for name, limit in [('large.txt', TEXT_LIMIT), ('large.png', IMAGE_LIMIT)]:
        target = tmp_path / name
        with target.open('wb') as stream:
            stream.truncate(limit + 1)
        assert preview_file(target)['kind'] == 'unsupported'


@pytest.fixture
def preview_api(tmp_path):
    project = tmp_path / '项目A'
    project.mkdir()
    (project / 'hello.md').write_text('# hello', encoding='utf-8')
    service = OfficeProjectService(paths=Paths(tmp_path / 'data'), user_id='default')
    original_project = service.bind(project)['projectId']

    class View:
        async def resolve_run(self, employee_id, run_id):
            if (employee_id, run_id) != ('pm', 'run-a'):
                raise FileNotFoundError('Run not found')
            return employee_id, SimpleNamespace(thread_id='thread-a', kwargs={}, metadata={'office_project_id': original_project})

    app = FastAPI()
    app.include_router(office.router)
    app.dependency_overrides[office.get_office_project_service] = lambda: service
    app.dependency_overrides[office.get_office_run_view] = lambda: View()
    with TestClient(app) as client:
        yield client, service, project


def test_preview_http_ownership_rebinding_and_current_file(preview_api, tmp_path):
    client, service, project = preview_api
    reference = {'path': 'hello.md', 'employeeId': 'pm', 'runId': 'run-a'}
    assert client.post('/api/office/files/preview', json=reference).json()['content'] == '# hello'
    virtual = {**reference, 'path': '/mnt/user-data/workspace/hello.md'}
    assert client.post('/api/office/files/preview', json=virtual).json()['content'] == '# hello'
    assert 'content' not in client.post('/api/office/files/preview', json={**reference, 'metadataOnly': True}).json()
    for payload, status in [
        ({**reference, 'employeeId': 'other'}, 404),
        ({'path': 'hello.md', 'runId': 'run-a'}, 400),
        ({**reference, 'metadataOnly': 'true'}, 400),
        ({**reference, 'unknown': True}, 400),
        ({**reference, 'projectId': 'other'}, 400),
        ({**reference, 'path': '../outside.txt'}, 400),
        ({**reference, 'path': '//server/share/file'}, 400),
        ({**reference, 'path': 'missing.txt'}, 404),
        ({**reference, 'path': '.'}, 404),
    ]:
        assert client.post('/api/office/files/preview', json=payload).status_code == status
    other = tmp_path / '项目B'
    other.mkdir()
    (other / 'hello.md').write_text('wrong project', encoding='utf-8')
    service.bind(other)
    for route in ['preview', 'resolve']:
        response = client.post(f'/api/office/files/{route}', json=reference)
        assert response.status_code == 400
        assert response.json()['detail']['code'] == 'errors.this_file_belongs_to_another_project_switch_back_to_view_it'
        assert client.post(f'/api/office/files/{route}', json=virtual).status_code == 400


def test_preview_outputs_and_symlink_escape(preview_api, tmp_path):
    client, service, project = preview_api
    virtual = '/mnt/user-data/outputs/report.md'
    target = service.paths.resolve_virtual_path('thread-a', virtual, user_id='default')
    target.parent.mkdir(parents=True)
    target.write_text('owned output', encoding='utf-8')
    reference = {'path': virtual, 'employeeId': 'pm', 'runId': 'run-a'}
    assert client.post('/api/office/files/preview', json=reference).json()['content'] == 'owned output'
    assert client.post('/api/office/files/preview', json={'path': virtual}).status_code == 400
    assert client.post('/api/office/files/preview', json={**reference, 'runId': 'run-other'}).status_code == 404
    assert client.post('/api/office/files/preview', json={**reference, 'path': '/mnt/user-data/../../private'}).status_code == 400
    outside = tmp_path / 'private.txt'
    outside.write_text('private', encoding='utf-8')
    try:
        (project / 'escape.txt').symlink_to(outside)
    except OSError:
        pytest.skip('Host does not permit symlink creation')
    assert client.post('/api/office/files/preview', json={'path': 'escape.txt'}).status_code == 400
