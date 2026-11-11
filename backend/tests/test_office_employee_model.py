import pytest

from test_office_employee_lifecycle import build_employee_service, frontend_employee_payload, MODEL_PROFILE_ID, employee_app_config
from deerflow.config.app_config import AppConfig
from deerflow.office.packages import resolve_employee_subagent_package
from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, manifest_revision, save_tool_manifest


@pytest.mark.asyncio
async def test_save_model_is_scoped_and_refreshes_delegation_without_changing_old_package(tmp_path, monkeypatch):
    service, _, _ = build_employee_service(tmp_path)
    service.model_profile_exists = lambda model: model in {MODEL_PROFILE_ID, 'vision', 'foreign'}
    service.model_profile_selectable = lambda model: model in {MODEL_PROFILE_ID, 'vision'}
    await service.create_employee(frontend_employee_payload())
    await service.create_employee(frontend_employee_payload('front-b'))
    monkeypatch.setattr('deerflow.config.paths._paths', service.paths)
    payload = employee_app_config().model_dump()
    payload['models'][0]['supports_vision'] = False
    payload['models'].append({**payload['models'][0], 'name': 'vision', 'supports_vision': True})
    config = AppConfig.model_validate(payload)
    monkeypatch.setattr('deerflow.config.get_app_config', lambda: config)
    folder = service.paths.user_agent_dir('default', 'frontend-a')
    manifest = load_tool_manifest(folder)
    save_tool_manifest(folder, EmployeeToolManifest(1, tuple(dict.fromkeys((*manifest.tools, 'view_image')))), expected_revision=manifest_revision(manifest))
    tools_before = (folder / 'tools.yaml').read_bytes()
    assert 'view_image' not in service.get_capability_index('frontend-a')['runtimeAvailableTools']
    old = resolve_employee_subagent_package('frontend-a', 'default', config)
    before = service.get_employee_detail('front-b')
    for invalid in ['foreign', 'missing', '', None, 1]:
        with pytest.raises(ValueError):
            await service.save_personal_info('frontend-a', {'modelProfileId': invalid, 'displayName': 'bad'})
    assert service.get_employee_summary('frontend-a')['displayName'] == '周礼'
    result = await service.save_personal_info('frontend-a', {'modelProfileId': 'vision', 'displayName': 'New', 'prompt': 'New prompt', 'skills': []})
    assert result['employee']['modelProfileId'] == 'vision'
    assert service.get_employee_detail('frontend-a')['personalInfo']['modelProfileId'] == 'vision'
    assert service._read_office('frontend-a')['model_profile_id'] == 'vision'
    assert service._read_config('frontend-a')['model'] == 'vision'
    assert 'view_image' in service.get_capability_index('frontend-a')['runtimeAvailableTools']
    assert (folder / 'tools.yaml').read_bytes() == tools_before
    assert service.get_employee_detail('front-b') == before
    assert old.config.model == MODEL_PROFILE_ID
    assert resolve_employee_subagent_package('frontend-a', 'default', config).config.model == 'vision'


@pytest.mark.asyncio
async def test_model_and_personal_info_rollback_together(tmp_path, monkeypatch):
    service, _, _ = build_employee_service(tmp_path)
    service.model_profile_exists = lambda model: True
    await service.create_employee(frontend_employee_payload())
    folder = service.paths.user_agent_dir('default', 'frontend-a')
    original = {name: (folder / name).read_bytes() for name in ['config.yaml', 'office.yaml', 'SOUL.md']}
    def fail(*args, **kwargs):
        raise OSError('disk failed')
    monkeypatch.setattr(service, '_save_office', fail)
    with pytest.raises(OSError):
        await service.save_personal_info('frontend-a', {'modelProfileId': 'new', 'prompt': 'changed', 'skills': []})
    assert {name: (folder / name).read_bytes() for name in original} == original


@pytest.mark.asyncio
async def test_pm_and_delegated_working_projection_reject_model_edits(tmp_path):
    service, _, _ = build_employee_service(tmp_path)
    await service.ensure_builtin_pm(default_model_profile_id=MODEL_PROFILE_ID)
    await service.create_employee(frontend_employee_payload())
    for employee in ['pm', 'frontend-a']:
        service.update_runtime_projection(employee, {'state': 'working'})
        with pytest.raises(RuntimeError, match='正在工作'):
            await service.save_personal_info(employee, {'modelProfileId': MODEL_PROFILE_ID})
