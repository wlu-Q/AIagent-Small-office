import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deerflow.config.app_config import AppConfig
from deerflow.config.paths import Paths
from deerflow.office.bootstrap import OfficeBootstrapService
from deerflow.office.projects import OfficeProjectService
from deerflow.office.model_binding import resolve_employee_model
from deerflow.office.model_management import PROVIDERS, THINKING_LEVELS, model_payload, verify_tool_roundtrip


@pytest.fixture
def service(tmp_path, monkeypatch):
    import deerflow.office.bootstrap as bootstrap
    import deerflow.office.model_management as management
    path = tmp_path / 'config.yaml'
    original = {'name': 'old', 'model': 'gpt-existing', 'use': 'langchain_openai:ChatOpenAI',
                'api_key': 'private-key', 'base_url': 'https://private.test/v1', 'reasoning_effort': 'high'}
    path.write_text(yaml.safe_dump({'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'}, 'models': [original]}), encoding='utf-8')
    paths = Paths(tmp_path / 'data')
    user = paths.user_dir('default'); user.mkdir(parents=True)
    (user / 'office-bootstrap.yaml').write_text(yaml.safe_dump({'company_name': '办公室', 'default_model_profile_id': 'old'}), encoding='utf-8')
    employee = SimpleNamespace(default_model_profile_id=None)
    employee.list_employees = AsyncMock(return_value=[])
    employee.is_employee_working = AsyncMock(return_value=False)
    employee.set_default_model_profile_id = lambda value: setattr(employee, 'default_model_profile_id', value)
    monkeypatch.setattr(bootstrap, 'reload_app_config', lambda path: AppConfig.from_file(path))
    monkeypatch.setattr(management, 'reload_app_config', lambda path: AppConfig.from_file(path))
    result = OfficeBootstrapService(paths=paths, user_id='default', config_path=path, employee_service=employee,
                                    project_service=OfficeProjectService(paths=paths, user_id='default'))
    async def verify(*args): pass
    result.models.verify = verify
    return result


def payload(provider='openai'):
    return {'requestId': str(uuid.uuid4()), 'provider': provider, 'modelName': 'model-test',
            'baseUrl': 'https://example.test/v1', 'apiKey': 'test-key', 'supportsVision': False}


@pytest.mark.asyncio
async def test_add_is_independent_idempotent_and_default_switch_preserves_old_profile(service):
    before = yaml.safe_load(service.config_path.read_text())['models'][0]
    draft = payload()
    first = await service.models.add(draft)
    second = await service.models.add(draft)
    assert first == second
    assert len(second['models']) == 2
    assert second['models'][0]['isDefault'] is True
    assert second['models'][0]['createdAt'] is None
    assert second['models'][1]['createdAt']
    assert 'apiKey' not in json.dumps(second) and 'test-key' not in json.dumps(second)
    new_id = second['models'][1]['id']
    switched = await service.models.set_default(new_id)
    assert [m['id'] for m in switched['models'] if m['isDefault']] == [new_id]
    assert yaml.safe_load(service.config_path.read_text())['models'][0] == before
    assert service.employee_service.default_model_profile_id == new_id
    await service.update_settings({'companyName': '新名字'})
    assert service.get_status()['defaultModel']['name'] == new_id
    restarted = OfficeBootstrapService(paths=service.paths, user_id='default', config_path=service.config_path,
                                      employee_service=service.employee_service, project_service=service.project_service)
    assert restarted.models.list() == switched


@pytest.mark.asyncio
async def test_failed_probe_never_saves_and_never_leaks_provider_error(service):
    async def fail(*args):
        raise RuntimeError('Bearer test-key leaked by provider')
    service.models.verify = fail
    before = service.config_path.read_bytes()
    with pytest.raises(ValueError, match='无法完成模型验证') as exc:
        await service.models.add(payload())
    assert 'test-key' not in str(exc.value)
    assert service.config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_reload_failure_rolls_back_model_and_default(service, monkeypatch):
    import deerflow.office.model_management as management
    before = yaml.safe_load(service.config_path.read_text())
    calls = 0
    def reload(path):
        nonlocal calls
        calls += 1
        if calls == 1: raise RuntimeError('cannot reload')
        return AppConfig.from_file(path)
    monkeypatch.setattr(management, 'reload_app_config', reload)
    with pytest.raises(ValueError, match='已恢复原配置'):
        await service.models.add(payload())
    assert yaml.safe_load(service.config_path.read_text()) == before


@pytest.mark.asyncio
async def test_global_switch_does_not_change_saved_employee_model(service):
    agent = service.paths.user_agent_dir('default', 'front')
    agent.mkdir(parents=True)
    pinned = resolve_employee_model({'model': 'old'})
    saved = await service.models.add(payload())
    new_id = saved['models'][1]['id']
    await service.models.set_default(new_id)
    assert pinned == 'old'
    assert resolve_employee_model({'model': None}) is None
    assert resolve_employee_model({'model': 'old'}) == 'old'


@pytest.mark.asyncio
async def test_office_scope_and_rejected_settings_cannot_change_credentials(service):
    source = yaml.safe_load(service.config_path.read_text())
    source['models'].append({**source['models'][0], 'name': 'other-user'})
    service._write_yaml_atomically(service.config_path, source)
    assert all(m['id'] != 'other-user' for m in service.models.list()['models'])
    with pytest.raises(ValueError, match='不属于'):
        await service.models.set_default('other-user')
    with pytest.raises(ValueError, match='只接受'):
        await service.update_settings({'companyName': 'x', 'apiKey': 'unexpected'})


def test_model_routes_and_validation(service):
    from app.gateway.routers.office import router
    app = FastAPI(); app.include_router(router); app.state.office_bootstrap_service = service
    client = TestClient(app)
    assert {p['id'] for p in client.get('/api/office/models').json()['providers']} == {p[0] for p in PROVIDERS}
    draft = payload('glm')
    result = client.post('/api/office/models', json=draft)
    assert result.status_code == 200
    model_id = result.json()['models'][-1]['id']
    switched = client.put('/api/office/models/default', json={'modelId': model_id})
    assert switched.status_code == 200
    assert switched.json()['models'][-1]['isDefault'] is True
    detail = client.get(f'/api/office/models/{model_id}')
    assert detail.status_code == 200
    assert detail.json()['apiKeyConfigured'] is True
    assert 'test-key' not in detail.text
    edit = {'modelName': 'updated', 'baseUrl': draft['baseUrl'], 'apiKey': '', 'supportsVision': True}
    updated = client.put(f'/api/office/models/{model_id}', json=edit)
    assert updated.status_code == 200
    assert updated.json()['models'][-1]['modelName'] == 'updated'
    assert updated.json()['models'][-1]['isDefault'] is True
    service.employee_service.list_employees.return_value = [{'id': 'pm', 'modelProfileId': model_id}]
    service.employee_service.is_employee_working.return_value = True
    assert client.get(f'/api/office/models/{model_id}').json()['editable'] is False
    assert client.put(f'/api/office/models/{model_id}', json=edit).status_code == 409
    assert client.get('/api/office/models/missing').status_code == 404
    assert client.post('/api/office/models', json={**payload(), 'provider': 'custom'}).status_code == 400
    assert client.post('/api/office/models', json={**payload(), 'baseUrl': 'file:///secret'}).status_code == 400


@pytest.mark.asyncio
async def test_terminal_mode_requires_warning_confirmation_without_touching_models(service):
    before = service.config_path.read_bytes()
    assert service.get_status()['terminalAccessMode'] == 'approval'
    with pytest.raises(ValueError, match='确认完全访问'):
        await service.update_settings({'companyName': 'Office', 'terminalAccessMode': 'full_access'})
    assert service.get_status()['terminalAccessMode'] == 'approval'
    result = await service.update_settings({'companyName': 'Office', 'terminalAccessMode': 'full_access', 'confirmFullAccess': True})
    assert result['terminalAccessMode'] == 'full_access'
    result = await service.update_settings({'companyName': 'Office', 'terminalAccessMode': 'approval'})
    assert result['terminalAccessMode'] == 'approval'
    assert service.config_path.read_bytes() == before


@pytest.mark.parametrize(('provider', 'level'), [(p, level) for p, levels in THINKING_LEVELS.items() for level in levels])
@pytest.mark.asyncio
async def test_real_adapters_serialize_tool_roundtrip_and_preserve_reasoning(provider, level, monkeypatch):
    """Actual LangChain clients, controlled HTTP responses (no vendor credentials)."""
    import deerflow.office.model_management as management
    from deerflow.models.factory import create_chat_model
    requests = []
    async def respond(request):
        body = json.loads(request.content); requests.append(body)
        if provider == 'qwen':
            assert request.url == 'https://example.test/v1/chat/completions'
            assert 'reasoning_effort' not in body and 'thinking' not in body
            if level == 'default':
                assert 'enable_thinking' not in body
            else:
                assert body['enable_thinking'] is (level == 'enabled')
        elif provider == 'mimo':
            assert body['thinking'] == {'type': level}
            assert 'reasoning_effort' not in body
        else:
            assert body['reasoning_effort'] == level
        if provider in {'deepseek', 'doubao'}:
            assert body['thinking'] == {'type': 'enabled'}
        assert 'default_thinking_enabled' not in body
        if len(requests) == 1:
            message = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call-probe', 'type': 'function',
                'function': {'name': 'office_connection_probe', 'arguments': '{}'}}]}
            if provider != 'openai' and (provider, level) != ('qwen', 'disabled'): message['reasoning_content'] = 'verify before answering'
            if provider == 'minimax':
                message['reasoning_details'] = [{'type': 'reasoning.text', 'text': 'verify before answering'}]
            finish = 'tool_calls'
        else:
            assert body['messages'][-1]['role'] == 'tool'
            assert body['messages'][-1]['tool_call_id'] == 'call-probe'
            if provider != 'openai' and (provider, level) != ('qwen', 'disabled'):
                assert body['messages'][-2]['reasoning_content'] == 'verify before answering'
            message = {'role': 'assistant', 'content': body['messages'][-1]['content']}
            finish = 'stop'
        return httpx.Response(200, json={'id': 'probe', 'object': 'chat.completion', 'created': 1, 'model': body['model'],
            'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(management, 'create_chat_model', lambda **kwargs: create_chat_model(**kwargs, http_async_client=client))
        config = AppConfig.model_validate({'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'},
            'models': [{**model_payload({**payload(provider), 'thinkingLevel': level}, 'probe'),
                        'max_retries': 4, 'request_timeout': 180}]})
        await verify_tool_roundtrip(config, 'probe')
    assert len(requests) == 2
    assert all('office_model_records' not in r for r in requests)


@pytest.mark.asyncio
async def test_thinking_edit_is_verified_persisted_and_failed_probe_keeps_previous_value(service):
    added = await service.models.add({**payload('deepseek'), 'thinkingLevel': 'max'})
    profile_id = added['models'][-1]['id']
    assert added['models'][-1]['thinkingLabel'] == '最高'
    service.models.verify = AsyncMock()
    draft = edit_payload(modelName='model-test', baseUrl='https://example.test/v1', supportsVision=False, thinkingLevel='low')
    updated = await service.models.update(profile_id, draft)
    service.models.verify.assert_awaited_once()
    assert updated['models'][-1]['thinkingLevel'] == 'low'
    assert (await service.models.detail(profile_id))['thinkingLabel'] == '低'
    before = service.config_path.read_bytes()
    service.models.verify = AsyncMock(side_effect=RuntimeError('private provider error test-key'))
    with pytest.raises(ValueError, match='无法完成模型验证'):
        await service.models.update(profile_id, {**draft, 'thinkingLevel': 'max'})
    assert service.config_path.read_bytes() == before
    for invalid in ['medium', 'none', '最高', None, True, {}, []]:
        with pytest.raises(ValueError, match='思考配置'):
            await service.models.update(profile_id, {**draft, 'thinkingLevel': invalid})
    assert service.config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_qwen_edit_preserves_key_and_restores_model_default(service):
    added = await service.models.add({**payload('qwen'), 'modelName': 'qwen-plus'})
    row = added['models'][-1]
    assert (row['provider'], row['thinkingLevel']) == ('qwen', 'default')
    for level in ('enabled', 'disabled', 'default'):
        edited = await service.models.update(row['id'], {
            'modelName': 'qwen-plus', 'baseUrl': 'https://example.test/v1',
            'apiKey': '', 'supportsVision': False, 'thinkingLevel': level})
        assert edited['models'][-1]['thinkingLevel'] == level
    saved = yaml.safe_load(service.config_path.read_text())['models'][-1]
    assert saved['api_key'] == 'test-key'
    assert 'enable_thinking' not in json.dumps(saved)
    assert 'test-key' not in json.dumps(await service.models.detail(row['id']))


def test_vendor_options_and_legacy_profile_remain_explicit(service):
    catalogue = service.models.list()
    providers = {item['id']: item for item in catalogue['providers']}
    assert providers['deepseek']['thinkingOptions'] == [
        {'value': 'low', 'label': '低'}, {'value': 'high', 'label': '高'}, {'value': 'max', 'label': '最高'}]
    assert providers['mimo']['thinkingOptions'] == [
        {'value': 'enabled', 'label': '开启思考'}, {'value': 'disabled', 'label': '关闭思考'}]
    assert catalogue['models'][0]['thinkingLabel'] == '高'


@pytest.mark.asyncio
async def test_text_only_response_is_not_a_successful_probe(monkeypatch):
    import deerflow.office.model_management as management
    from langchain_core.messages import AIMessage
    class TextOnly:
        def bind_tools(self, tools): return self
        async def ainvoke(self, messages): return AIMessage(content='OK')
    monkeypatch.setattr(management, 'create_chat_model', lambda **kwargs: TextOnly())
    with pytest.raises(ValueError, match='未正确调用测试工具'):
        await verify_tool_roundtrip(AppConfig.model_validate({'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'}, 'models': []}), 'probe')


@pytest.mark.asyncio
async def test_inherited_binding_migration_pins_old_employees_and_creation_uses_current_default(tmp_path, monkeypatch):
    from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload, MODEL_PROFILE_ID
    from deerflow.office.packages import resolve_employee_subagent_package
    employees, _, _ = build_employee_service(tmp_path, workstation_ids=tuple(f'desk-{i}' for i in range(6)))
    employees.model_profile_exists = lambda name: name in {MODEL_PROFILE_ID, 'independent', 'new-default'}
    employees.set_default_model_profile_id(MODEL_PROFILE_ID)
    await employees.create_employee(frontend_employee_payload('legacy'))
    await employees.create_employee({**frontend_employee_payload('explicit'), 'modelProfileId': 'independent'})
    await employees.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)
    original_office = employees._read_office('legacy')
    inherited = employees._read_config('legacy')
    inherited['model'] = None
    employees._write_config('legacy', inherited)
    inherited_office = employees._read_office('legacy')
    inherited_office['model_profile_id'] = None
    employees._save_office('legacy', inherited_office)
    employees.migrate_global_model_bindings(MODEL_PROFILE_ID)
    assert employees._read_config('legacy')['model'] == MODEL_PROFILE_ID
    assert employees._read_office('legacy')['projection'] == original_office['projection']
    assert employees._read_config('explicit')['model'] == 'independent'
    assert employees._read_config('pm')['model'] == MODEL_PROFILE_ID
    settings = employees.paths.user_dir('default') / 'office-bootstrap.yaml'
    settings.write_text(yaml.safe_dump({'default_model_profile_id': 'new-default'}), encoding='utf-8')
    employees.set_default_model_profile_id('new-default')
    assert employees.get_employee_summary('legacy')['modelProfileId'] == MODEL_PROFILE_ID
    await employees.create_employee({'id': 'new', 'templateId': 'frontend-engineer', 'displayName': '新员工', 'avatar': 'adam'})
    assert employees._read_config('new')['model'] == 'new-default'
    assert employees.get_employee_summary('new')['modelProfileId'] == 'new-default'
    monkeypatch.setattr('deerflow.config.paths._paths', employees.paths)
    from test_office_employee_lifecycle import employee_app_config
    source = employee_app_config().model_dump()
    source['models'] = [{**source['models'][0], 'name': name} for name in ['new-default', 'independent', MODEL_PROFILE_ID]]
    config = AppConfig.model_validate(source)
    assert resolve_employee_subagent_package('legacy', user_id='default', app_config=config).config.model == MODEL_PROFILE_ID
    assert resolve_employee_subagent_package('explicit', user_id='default', app_config=config).config.model == 'independent'


@pytest.mark.asyncio
async def test_invalid_candidate_config_is_sanitized(service, monkeypatch):
    import deerflow.office.model_management as management
    def reject(*args):
        raise ValueError('invalid config includes private-key')
    monkeypatch.setattr(management.AppConfig, 'model_validate', reject)
    with pytest.raises(ValueError, match='无法完成模型验证') as exc:
        await service.models.add(payload())
    assert 'private-key' not in str(exc.value)


def edit_payload(**overrides):
    return {'modelName': 'gpt-existing', 'baseUrl': 'https://private.test/v1',
            'apiKey': '', 'supportsVision': True, **overrides}


@pytest.mark.asyncio
async def test_edit_preserves_secret_identity_metadata_and_provider_options(service):
    added = await service.models.add(payload('minimax'))
    profile = added['models'][-1]
    before = service._read_config_payload()
    service.models.verify = AsyncMock()
    result = await service.models.update(profile['id'], edit_payload(modelName='changed', apiKey=''))
    service.models.verify.assert_awaited_once()
    after = service._read_config_payload()
    saved = after['models'][-1]
    assert saved['api_key'] == 'test-key'
    assert saved['name'] == profile['id']
    assert saved['use'] == before['models'][-1]['use']
    assert saved['extra_body'] == {'reasoning_split': True}
    assert after['office_model_records'] == before['office_model_records']
    assert len(result['models']) == len(added['models'])
    assert result['models'][-1]['createdAt'] == profile['createdAt']
    assert result['models'][0]['isDefault'] is True
    assert 'test-key' not in json.dumps(await service.models.detail(profile['id']))


@pytest.mark.asyncio
async def test_edit_legacy_vision_only_skips_probe_and_keeps_reasoning(service):
    service.models.verify = AsyncMock()
    await service.models.update('old', edit_payload())
    service.models.verify.assert_not_awaited()
    saved = service._read_config_payload()['models'][0]
    assert saved['api_key'] == 'private-key'
    assert saved['reasoning_effort'] == 'high'
    assert saved['supports_vision'] is True
    assert (await service.models.detail('old'))['isDefault'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('fields', [{'modelName': 'other'}, {'baseUrl': 'https://new.test/v1'}, {'apiKey': 'replacement'}])
async def test_connection_edits_are_verified(service, fields):
    service.models.verify = AsyncMock()
    await service.models.update('old', edit_payload(**fields))
    service.models.verify.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_failure_and_busy_recheck_do_not_write(service, monkeypatch):
    import deerflow.office.model_management as management
    before = service.config_path.read_bytes()
    service.models.verify = AsyncMock(side_effect=RuntimeError('provider exposed private-key'))
    with pytest.raises(ValueError, match='无法完成模型验证') as exc:
        await service.models.update('old', edit_payload(modelName='changed'))
    assert 'private-key' not in str(exc.value)
    assert service.config_path.read_bytes() == before
    service.employee_service.list_employees.return_value = [{'id': 'pm', 'modelProfileId': 'old'}]
    async def become_busy(*args):
        service.employee_service.is_employee_working.return_value = True
    service.models.verify = become_busy
    with pytest.raises(RuntimeError, match='正在使用'):
        await service.models.update('old', edit_payload(modelName='changed'))
    assert service.config_path.read_bytes() == before
    service.employee_service.is_employee_working.return_value = False
    reload = iter([RuntimeError('private-key'), None])
    def reload_config(path):
        error = next(reload)
        if error: raise error
    monkeypatch.setattr(management, 'reload_app_config', reload_config)
    with pytest.raises(ValueError, match='已恢复原配置'):
        await service.models.update('old', edit_payload())
    assert service.config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_edit_rejects_other_office_and_unknown_fields(service):
    source = service._read_config_payload()
    source['models'].append({**source['models'][0], 'name': 'other-user'})
    service._write_yaml_atomically(service.config_path, source)
    before = service.config_path.read_bytes()
    for profile in ['missing', 'other-user']:
        with pytest.raises(FileNotFoundError): await service.models.detail(profile)
        with pytest.raises(FileNotFoundError): await service.models.update(profile, edit_payload())
    for fields in [{'provider': 'kimi'}, {'apiKey': None}, {'baseUrl': 'file:///tmp'}, {'supportsVision': 'yes'}]:
        with pytest.raises(ValueError): await service.models.update('old', edit_payload(**fields))
    assert service.config_path.read_bytes() == before
