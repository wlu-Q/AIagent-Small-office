import json
from types import SimpleNamespace

import pytest

from deerflow.agents.lead_agent import prompt
from deerflow.config.paths import Paths
from deerflow.sandbox import tools
from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.sandbox.patch_tool import apply_patch_tool
from deerflow.skills.access import bind_skill_read_tools
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage
from patch_helpers import add_patch


@pytest.fixture
def skill_files(tmp_path, monkeypatch):
    paths = Paths(tmp_path / 'data')
    monkeypatch.setattr('deerflow.config.paths.get_paths', lambda: paths)
    public = tmp_path / 'skills'
    (public / 'public').mkdir(parents=True)
    custom = paths.user_custom_skills_dir('alice')
    custom.mkdir(parents=True)
    storage = UserScopedSkillStorage(user_id='alice', host_path=str(public))
    app = SimpleNamespace(skills=SimpleNamespace(container_path='/mnt/skills', get_skills_path=lambda: public))
    monkeypatch.setattr(prompt, 'get_or_new_user_skill_storage', lambda *args, **kwargs: storage)
    monkeypatch.setattr(tools, '_get_skills_container_path', lambda: '/mnt/skills')
    monkeypatch.setattr(tools, '_get_skills_host_path', lambda: str(public))
    sandbox = LocalSandbox('skills-test', path_mappings=[
        PathMapping('/mnt/skills/custom', str(custom), read_only=False),
        PathMapping('/mnt/skills/public', str(public / 'public'), read_only=True),
    ])
    runtime = SimpleNamespace(context={'user_id': 'alice'}, tool_call_id='skill-write', state={
        'thread_data': {'office_work': {'work_id': 'work'}, 'workspace_path': str(tmp_path / 'project'),
                        'outputs_path': str(tmp_path / 'outputs'), 'uploads_path': str(tmp_path / 'uploads')},
    })
    monkeypatch.setattr(tools, 'ensure_sandbox_initialized', lambda _: sandbox)
    monkeypatch.setattr(tools, 'is_local_sandbox', lambda _: True)
    monkeypatch.setattr(tools, 'ensure_thread_directories_exist', lambda _: None)
    return paths, custom, storage, app, runtime


def test_create_discover_read_and_uninstall_without_registration(skill_files):
    paths, custom, storage, app, runtime = skill_files
    assert prompt.get_enabled_skills_for_config(app, user_id='alice') == []
    virtual = '/mnt/skills/custom/local-demo/SKILL.md'
    content = '---\nname: local-demo\ndescription: 本地测试技能\n---\n\n写一份简短工作记录。\n'
    result = apply_patch_tool.func(runtime, add_patch(virtual, content))
    assert json.loads(result.content)['status'] == 'success', result.content
    assert (custom / 'local-demo' / 'SKILL.md').is_file()
    skills = prompt.get_enabled_skills_for_config(app, user_id='alice')
    assert [skill.name for skill in skills] == ['local-demo']
    assert [skill.name for skill in prompt.get_enabled_skills_for_config(user_id='alice')] == ['local-demo']
    reader = bind_skill_read_tools([tools.read_file_tool], [], app_config=app, user_id='alice', manage_custom=True)[0]
    result = reader.func(runtime, description='核对技能文件', path=virtual)
    assert '写一份简短工作记录' in str(result)
    other = UserScopedSkillStorage(user_id='bob', host_path=str(app.skills.get_skills_path()))
    assert other.load_skills() == []
    (custom / 'local-demo' / 'SKILL.md').write_text(content.replace('本地测试技能', '更新后的说明'), encoding='utf-8')
    assert prompt.get_enabled_skills_for_config(app, user_id='alice')[0].description == '更新后的说明'
    result = apply_patch_tool.func(runtime, f'*** Begin Patch\n*** Delete File: {virtual}\n*** End Patch')
    assert json.loads(result.content)['status'] == 'success'
    assert prompt.get_enabled_skills_for_config(app, user_id='alice') == []
    assert not paths.user_custom_skills_dir('bob').exists()


@pytest.mark.parametrize('path', [
    '/mnt/skills/public/builtin/SKILL.md', '/mnt/skills/custom/../public/builtin/SKILL.md',
    '/mnt/skills/custom/.history/data', '/mnt/skills/custom/../../other-user/SKILL.md',
])
def test_skill_management_keeps_path_boundaries(skill_files, path):
    runtime = skill_files[-1]
    result = apply_patch_tool.func(runtime, add_patch(path, 'no'))
    assert json.loads(result.content)['status'] == 'error'


def test_non_office_tool_path_still_rejects_custom_skill_writes(skill_files):
    runtime = skill_files[-1]
    runtime.state['thread_data'].pop('office_work')
    result = apply_patch_tool.func(runtime, add_patch('/mnt/skills/custom/no/SKILL.md', 'no'))
    assert json.loads(result.content)['status'] == 'error'
