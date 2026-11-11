"""Library regression: durable lifecycle, publication, retrieval and authorization."""
from pathlib import Path
import json
import pytest

from office_library.parsers import chunks, compact_locations, parse
from office_library.providers import ProviderError
from office_library.service import DuplicateFile, LibraryService


class FakeProvider:
    fail_embedding = False
    fail_rerank = False

    def __init__(self, config):
        self.config = config

    def embed(self, texts):
        if self.fail_embedding:
            raise ProviderError('模型接口失败')
        return [[1.0, 0.1, 0.2] for _ in texts]

    def rerank(self, query, texts):
        if self.fail_rerank:
            raise ProviderError('精排失败')
        return list(range(min(8, len(texts))))


@pytest.fixture
def library(tmp_path):
    FakeProvider.fail_embedding = FakeProvider.fail_rerank = False
    service = LibraryService(tmp_path / 'library', FakeProvider)
    yield service
    service.close()
    FakeProvider.fail_embedding = FakeProvider.fail_rerank = False


def ready(service, name='规范.md', text='前端规范\n\n接口异常必须展示重试按钮，禁止泄露原始堆栈。'):
    doc = service.upload(name, text.encode())
    assert service.process_next(), service.list_documents()
    return doc


def test_duplicate_only_name_and_type_and_atomic_replace(library):
    first = ready(library)
    with pytest.raises(DuplicateFile):
        library.upload('规范.md', b'different')
    # Identical content, different name is allowed.
    ready(library, '另一份规范.md')
    ready(library, '规范.txt')
    replaced = library.upload('规范.md', '更新规范：接口错误可重试。'.encode(), first['id'], first['version_id'])
    assert next(row for row in library.search('重试')['results'] if row['document_id'] == first['id'])['version_id'] == first['version_id']
    FakeProvider.fail_embedding = True
    assert not library.process_next()
    assert library.list_documents()[0]['active'] == first['version_id']
    FakeProvider.fail_embedding = False
    library.retry(first['id'])
    assert library.process_next()
    versions = {item['version_id'] for item in library.search('重试')['results'] if item['document_id'] == first['id']}
    assert versions == {replaced['version_id']}
    # Historical references retain the actual original; never silently read latest.
    assert b'different' not in library.read_file(first['id'], first['version_id'])[1]


def test_delete_and_user_isolation(library, tmp_path):
    doc = ready(library)
    other = LibraryService(tmp_path / 'other', FakeProvider)
    with pytest.raises(FileNotFoundError):
        other.read_file(doc['id'], doc['version_id'])
    library.delete(doc['id'])
    assert library.search('前端规范')['results'] == []
    with pytest.raises(FileNotFoundError):
        library.read_file(doc['id'])
    library._cleanup()
    assert not (library.root / 'files' / doc['version_id']).exists()


def test_keyword_fallback_dedup_and_rerank_fallback(library):
    ready(library)
    result = library.search('接口异常')
    assert len(result['results']) == 1
    assert '禁止泄露' in result['results'][0]['text']
    FakeProvider.fail_embedding = True
    assert library.search('接口异常')['degraded']
    FakeProvider.fail_rerank = True
    result = library.search('接口异常')
    assert len(result['degraded']) == 2
    assert result['results']


def test_restart_recovers_pending_processing(library):
    doc = library.upload('重启.txt', '重启后还可以检索'.encode())
    with library.db() as db:
        db.execute("UPDATE versions SET status='processing'")
    reopened = LibraryService(library.root, FakeProvider)
    assert reopened.process_next()
    assert reopened.read_file(doc['id'])[1].decode() == '重启后还可以检索'
    assert reopened.search('重启')['results']


def test_parsers_and_bounded_chunks(tmp_path):
    from docx import Document
    document = Document()
    document.add_heading('规范', 1)
    document.add_paragraph('真实段落')
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '字段'
    table.cell(0, 1).text = '含义'
    path = tmp_path / 'a.docx'
    document.save(path)
    result = parse(path, '.docx')
    assert any('字段 | 含义' in row['text'] for row in result)
    pieces = chunks([{'text': '规范。' * 2000, 'location': '段落 1'}])
    assert len(pieces) > 1 and all(len(row['text']) <= 1800 for row in pieces)
    from pypdf import PdfWriter
    writer = PdfWriter(); writer.add_blank_page(100, 100)
    path = tmp_path / 'scan.pdf'; writer.write(path)
    with pytest.raises(ValueError, match='未提取到文字'):
        parse(path, '.pdf')


def test_text_source_lines_preserve_blank_lines_and_noncontiguous_ranges(tmp_path):
    path = tmp_path / 'lines.md'
    path.write_bytes('\r\n\r\n# 规范\r\n\r\n  第一行\r\n第二行\r\n\r\n结尾'.encode('utf-8-sig'))
    blocks = parse(path, '.md')
    assert [block['location'] for block in blocks] == ['第3行', '第5行-第6行', '第8行']
    assert chunks(blocks)[0]['location'] == '第3行-第8行'
    assert compact_locations(['第13行-第15行', '第15行-第17行', '第19行']) == '第13行-第17行、第19行'
    assert compact_locations(['第 2 页', '第 3 页']) == '第2页-第3页'


def test_oversized_text_chunks_keep_exact_source_lines(tmp_path):
    text = '\n'.join(f'{index:04d} 行的规范内容' for index in range(500))
    path = tmp_path / 'long.txt'
    path.write_bytes(text.encode('utf-8'))
    for piece in chunks(parse(path, '.txt')):
        offset = text.index(piece['text'])
        first = text[:offset].count('\n') + 1
        last = first + piece['text'].rstrip().count('\n')
        assert piece['location'] == f'第{first}行-第{last}行'


def test_citation_uses_original_version_lines_even_for_existing_index(library):
    doc = ready(library, '引用[规范].md', '\n' * 12 + '开发规范\n必须测试\n必须验收\n必须交付\n保留记录\n\n补充规范')
    with library.db() as db:
        blocks = json.loads(db.execute('SELECT blocks FROM versions WHERE id=?', (doc['version_id'],)).fetchone()[0])
        for index, block in enumerate(blocks, 1):
            block.pop('line_start')
            block['location'] = f'段落 {index}'
        db.execute('UPDATE versions SET blocks=? WHERE id=?', (json.dumps(blocks), doc['version_id']))
    result = library.search('开发规范')['results'][0]
    assert result['location'] == '第13行-第19行'
    assert result['citation'] == f"引用来自：[引用\\[规范\\].md【第13行-第19行】]({result['source']})"
    replacement = library.upload('引用[规范].md', b'new version', doc['id'], doc['version_id'])
    assert library.process_next()
    assert library.locate(doc['id'], doc['version_id'], 0)['location'] == '第13行-第19行'
    assert library.locate(doc['id'], replacement['version_id'], 0)['location'] == '第1行'


def test_context_neighbour_citation_covers_only_returned_whole_chunks(library):
    text = '\n\n'.join(f'第{index}项规范' + '需要验收。' * 300 for index in range(6))
    doc = ready(library, '长规范.md', text)
    results = library.search('规范')['results']
    with library.db() as db:
        pieces = library._source_pieces(db, doc['id'], doc['version_id'])
    for result in results:
        selected = [piece for piece in pieces if piece['text'] in result['text']]
        assert len(result['text']) <= 4500
        assert result['location'] == compact_locations(piece['location'] for piece in selected)
        first = int(result['source'].split('?part=')[1])
        assert result['text'].startswith(pieces[first]['text'])


def test_filename_path_validation_and_empty_document(library):
    for name in ('../a.txt', 'C:\\a.txt', 'a.exe'):
        with pytest.raises(ValueError):
            library.upload(name, b'x')
    library.upload('空.txt', b'  ')
    assert not library.process_next()
    assert library.list_documents()[0]['status'] == 'failed'
    assert library.search('资料')['status'] == 'empty'


def test_embedding_rebuild_publishes_all_together(library):
    library.save_settings({'embedding_api_key': 'local-test-key', 'rerank_api_key': 'local-rerank-key'})
    one, two = ready(library), ready(library, '规则.txt')
    library.save_settings({'embedding_model': 'new-test-embedding'})
    assert library.settings()['rebuilding']
    assert library.process_next()
    assert {doc['active'] for doc in library.list_documents()} == {one['version_id'], two['version_id']}
    assert library.process_next()
    assert not library.settings()['rebuilding']
    assert library.settings()['embedding_model'] == 'new-test-embedding'
    assert {doc['active'] for doc in library.list_documents()}.isdisjoint({one['version_id'], two['version_id']})
    assert library.search('规范')['results']


def test_independent_module_and_minimal_tool_schema():
    import ast
    for path in (Path(__file__).parents[1] / 'office_library').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith(('deerflow', 'app.'))
    from deerflow.office.library_adapter import search_office_library
    assert search_office_library.tool_call_schema.model_json_schema()['required'] == ['query']

@pytest.mark.asyncio
async def test_library_tool_needs_no_workspace_operation_marker(monkeypatch):
    from types import SimpleNamespace
    from deerflow.office.library_adapter import search_office_library
    from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware
    from langchain_core.messages import ToolMessage
    request = SimpleNamespace(tool=search_office_library,
        tool_call={'name': 'search_office_library', 'id': 'library-call', 'args': {'query': '规范'}},
        runtime=SimpleNamespace(context={'office_work': True, 'user_id': 'default'}))
    async def handler(_):
        return ToolMessage(content='library result', tool_call_id='library-call', status='success')
    result = await ReadBeforeWriteMiddleware().awrap_tool_call(request, handler)
    assert result.status == 'success'
    assert result.content == 'library result'

def test_provider_validation_and_irrelevant_rejection(monkeypatch):
    from office_library.config import DEFAULTS
    from office_library.providers import QwenProvider
    provider = QwenProvider(DEFAULTS)
    monkeypatch.setattr(provider, '_post', lambda *a: {'output': {'results': [
        {'index': 1, 'relevance_score': 0.9}, {'index': 0, 'relevance_score': 0.2}]}})
    assert provider.rerank('规范', ['无关', '相关']) == [1]
    monkeypatch.setattr(provider, '_post', lambda *a: {'output': {'results': [{'index': 0, 'relevance_score': 0.02}]}})
    assert provider.rerank('规范', ['无关']) == []
    monkeypatch.setattr(provider, '_post', lambda *a: {'data': ['invalid']})
    with pytest.raises(ProviderError): provider.embed(['query'])
    monkeypatch.setattr(provider, '_post', lambda *a: {'output': []})
    with pytest.raises(ProviderError): provider.rerank('query', ['text'])


def test_http_lifecycle_and_identity_boundary(tmp_path, monkeypatch):
    import base64
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.gateway.routers import office_library as routes
    from deerflow.runtime.user_context import set_current_user, reset_current_user
    services = {owner: LibraryService(tmp_path / owner, FakeProvider) for owner in ('alice', 'bob')}
    monkeypatch.setattr(routes, 'library_for_user', lambda owner: services[owner])
    app = FastAPI()
    # Test-only authentication boundary; production identity comes from Gateway auth.
    @app.middleware('http')
    async def identity(request, call_next):
        token = set_current_user(SimpleNamespace(id=request.headers.get('test-user', 'alice')))
        try: return await call_next(request)
        finally: reset_current_user(token)
    app.include_router(routes.router)
    with TestClient(app) as client:
        base = '/api/office/library'
        from office_library.config import DEFAULTS
        response = client.put(base + '/settings', json={**DEFAULTS,
            'embedding_api_key': 'http-embedding-key', 'rerank_api_key': 'http-rerank-key'})
        assert response.status_code == 200
        assert response.json()['embeddingApiKeyConfigured'] and response.json()['rerankApiKeyConfigured']
        assert 'http-embedding-key' not in response.text and 'http-rerank-key' not in response.text
        assert client.put(base + '/settings', json={**DEFAULTS, 'rerank_api_key': 'http-rerank-updated'}).status_code == 200
        assert services['alice']._config()['embedding_api_key'] == 'http-embedding-key'
        assert services['alice']._config()['rerank_api_key'] == 'http-rerank-updated'
        assert not client.get(base + '/settings', headers={'test-user': 'bob'}).json()['embeddingApiKeyConfigured']
        body = {'filename': '规范.txt', 'content': base64.b64encode('接口重试规范'.encode()).decode(), 'user_id': 'bob'}
        response = client.post(base + '/documents', json=body)
        assert response.status_code == 200
        doc = response.json()
        assert services['alice'].process_next()
        assert client.get(base+'/documents', headers={'test-user':'bob'}).json()['documents'] == []
        assert client.get(base+f"/documents/{doc['id']}/file", headers={'test-user':'bob'}).status_code == 404
        assert client.post(base+'/documents',json=body).status_code == 409
        file = client.get(base+f"/documents/{doc['id']}/file",params={'version':doc['version_id'],'part':0})
        assert file.status_code == 200 and '接口重试' in file.json()['quote']
        assert client.post(base+'/documents',json={**body,'content':'not base64'}).status_code == 400
        original_name = client.get(base+'/documents').json()['documents'][0]['display_name']
        assert client.patch(base+f"/documents/{doc['id']}",json={'name':'新名称'}).status_code == 405
        assert client.get(base+'/documents').json()['documents'][0]['display_name'] == original_name
        assert client.delete(base+f"/documents/{doc['id']}").status_code == 200
        assert client.get(base+f"/documents/{doc['id']}/file").status_code == 404
    for service in services.values(): service.close()


def test_migration_preserves_opt_out(tmp_path):
    import importlib.util
    from deerflow.office.tool_manifest import EmployeeToolManifest, save_tool_manifest, load_tool_manifest, manifest_revision
    path = Path(__file__).parents[1] / 'scripts/enable_office_library.py'
    spec = importlib.util.spec_from_file_location('library_migration',path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    employee = tmp_path / 'users/default/agents/test'
    employee.mkdir(parents=True)
    (employee/'office.yaml').write_text('projection:\n  state: idle\n',encoding='utf8')
    save_tool_manifest(employee,EmployeeToolManifest(1,('read_file',)),expected_revision='')
    assert module.migrate(tmp_path) == ['test']
    selected=load_tool_manifest(employee)
    assert 'search_office_library' in selected.tools
    save_tool_manifest(employee,EmployeeToolManifest(1,('read_file',)),expected_revision=manifest_revision(selected))
    assert module.migrate(tmp_path) == []
    assert load_tool_manifest(employee).tools == ('read_file',)

def test_rebuild_preserves_key_repaired_during_batch_and_deletes_all_generations(library):
    library.save_settings({'embedding_api_key':'old-local-test', 'rerank_api_key':'rerank-local-test'})
    doc=ready(library)
    old_config=library._config()
    library.save_settings({'embedding_model':'another-model'})
    pending=library.settings()
    assert pending['embedding_model']=='another-model'
    assert not any('secret' in key or key.endswith('_api_key') for key in pending)
    class RepairDuringBatch(FakeProvider):
        def embed(self,texts):
            saved = library.save_settings({**pending,'embedding_api_key':'repaired-local-test'})
            assert saved['embeddingApiKeyConfigured'] and saved['rerankApiKeyConfigured']
            return super().embed(texts)
    library.provider_factory=RepairDuringBatch
    assert library.process_next()
    assert library._config()['embedding_api_key']=='repaired-local-test'
    assert library._config()['rerank_api_key']=='rerank-local-test'
    library.delete(doc['id']);library._cleanup()
    for config in (old_config,library._config()):
        _,table=library._table(config)
        assert table.count_rows()==0

def test_office_web_boundary_copies_actual_provider_tool():
    from deerflow.office.library_adapter import library_aware_web_tool
    from deerflow.community.firecrawl.tools import web_search_tool
    from deerflow.community.ddg_search.tools import web_search_tool as fallback
    for original in (web_search_tool,fallback):
        description=original.description
        scoped=library_aware_web_tool(original)
        assert 'search_office_library' in scoped.description
        assert scoped.func is original.func
        assert scoped.args_schema is original.args_schema
        assert original.description==description
        assert scoped is not original


def test_keys_save_independently_without_rebuilding_or_returning_secrets(library):
    from office_library.config import protect
    library.save_settings({'embedding_api_key': 'embedding-original', 'rerank_api_key': 'rerank-original'})
    doc = ready(library)
    library.save_settings({**library.settings(), 'embedding_api_key': '', 'rerank_api_key': 'rerank-updated'})
    assert library._config()['embedding_api_key'] == 'embedding-original'
    assert library._config()['rerank_api_key'] == 'rerank-updated'
    assert not library.settings()['rebuilding']
    assert library.list_documents()[0]['active'] == doc['version_id']
    with library.db() as db:
        stored = json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
    assert protect(stored['embedding_secret'], decrypt=True) == 'embedding-original'
    assert protect(stored['rerank_secret'], decrypt=True) == 'rerank-updated'
    assert not any(name.endswith('_api_key') for name in stored)
    reopened = LibraryService(library.root, FakeProvider)
    assert reopened._config()['rerank_api_key'] == 'rerank-updated'
    assert not any('secret' in name or name.endswith('_api_key') for name in reopened.settings())
    reopened.close()


@pytest.mark.parametrize('missing,label', [('embedding', '向量'), ('rerank', '精排')])
def test_missing_key_is_not_borrowed_from_other_model(library, missing, label, monkeypatch):
    monkeypatch.delenv(f'OFFICE_LIBRARY_{missing.upper()}_API_KEY', raising=False)
    values = {'embedding_api_key': 'embedding-only', 'rerank_api_key': 'rerank-only'}
    del values[f'{missing}_api_key']
    with pytest.raises(ValueError, match=label):
        library.save_settings(values)
    assert not library.settings()['embeddingApiKeyConfigured']
    assert not library.settings()['rerankApiKeyConfigured']


def test_provider_sends_each_key_only_to_its_endpoint(monkeypatch):
    import httpx
    from office_library.config import DEFAULTS
    from office_library.providers import QwenProvider
    seen = []
    def handle(request):
        seen.append((str(request.url), request.headers['Authorization']))
        if request.url.host == 'embedding.example':
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.0, 0.2]}]})
        return httpx.Response(200, json={'output': {'results': [{'index': 0, 'relevance_score': 0.9}]}})
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    provider = QwenProvider({**DEFAULTS, 'embedding_url': 'https://embedding.example/v1/embeddings',
        'rerank_url': 'https://rerank.example/rerank', 'embedding_api_key': 'embedding-key', 'rerank_api_key': 'rerank-key'})
    assert provider.embed(['文本']) == [[1.0, 0.2]]
    assert provider.rerank('规范', ['文本']) == [0]
    assert seen == [('https://embedding.example/v1/embeddings', 'Bearer embedding-key'),
                    ('https://rerank.example/rerank', 'Bearer rerank-key')]
    provider.config['rerank_api_key'] = ''
    with pytest.raises(ProviderError, match='精排'):
        provider.rerank('规范', ['文本'])
    assert len(seen) == 2


def test_shared_key_offline_migration_preserves_pending_rebuild(library):
    import importlib.util
    from office_library.config import DEFAULTS, protect
    path = Path(__file__).parents[1] / 'scripts/split_library_api_keys.py'
    spec = importlib.util.spec_from_file_location('split_library_keys', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with library.db() as db:
        db.execute("INSERT INTO meta VALUES('config',?)", (json.dumps({**DEFAULTS, 'secret': protect('old-shared-key')}),))
        db.execute("INSERT INTO meta VALUES('rebuild',?)", (json.dumps({'config': {**DEFAULTS,
            'embedding_model': 'pending-model', 'secret': protect('pending-shared-key')}, 'versions': {}}),))
    database = library.root / 'library.sqlite'
    assert set(migration.migrate(database)) == {'config', 'rebuild'}
    with pytest.raises(ValueError, match='迁移'):
        library.settings()
    assert set(migration.migrate(database, apply=True)) == {'config', 'rebuild'}
    assert len(list(library.root.glob('*.before-split-keys-*'))) == 1
    assert library._config()['embedding_api_key'] == library._config()['rerank_api_key'] == 'old-shared-key'
    assert library.settings()['embedding_model'] == 'pending-model'
    library.save_settings({**library.settings(), 'rerank_api_key': 'pending-rerank-updated'})
    received = []
    class Recorder(FakeProvider):
        def __init__(self, config):
            received.append(config)
            super().__init__(config)
    library.provider_factory = Recorder
    assert library.test_connection()['ok']
    assert received[0]['embedding_api_key'] == 'pending-shared-key'
    assert received[0]['rerank_api_key'] == 'pending-rerank-updated'
    assert library._config()['rerank_api_key'] == 'old-shared-key'
    assert migration.migrate(database, apply=True) == []
