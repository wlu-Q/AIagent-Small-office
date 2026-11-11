import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_function
from pydantic import ValidationError

from deerflow.agents.middlewares.todo_middleware import OFFICE_TODO_TOOL_DESCRIPTION, TodoMiddleware
from deerflow.office.library_adapter import search_office_library
from deerflow.office.routing import build_search_employee_registry_tool
from deerflow.office.tool_contract import build_tool_catalog, compact_office_tool_descriptions
from deerflow.sandbox.powershell_tools import powershell_run
from deerflow.tools.builtins.review_skill_package_tool import review_skill_package
from deerflow.tools.builtins.tool_search import DeferredToolCatalog, build_tool_search_tool


def described_tools():
    return compact_office_tool_descriptions([
        build_search_employee_registry_tool('default'),
        search_office_library,
        *TodoMiddleware(enforce_completion=False, tool_description=OFFICE_TODO_TOOL_DESCRIPTION).tools,
        build_tool_search_tool(DeferredToolCatalog(tools=())),
        review_skill_package,
        powershell_run,
    ])


@pytest.mark.parametrize('selected_tool', described_tools(), ids=lambda selected_tool: selected_tool.name)
def test_model_visible_parameters_have_descriptions_and_hide_injected_fields(selected_tool):
    definition = convert_to_openai_function(selected_tool)
    properties = definition['parameters']['properties']
    assert definition['description']
    assert all(field.get('description') for field in properties.values())
    assert not {'runtime', 'tool_call_id', 'config'} & properties.keys()
    if selected_tool.name == 'write_todos':
        item = properties['todos']['items']
        assert all(field.get('description') for field in item['properties'].values())
        assert item['required'] == ['content', 'status']
        assert item['properties']['status']['enum'] == ['pending', 'in_progress', 'completed']


def test_todo_description_matches_catalog_and_preserves_native_functions():
    actual = TodoMiddleware(enforce_completion=False, tool_description=OFFICE_TODO_TOOL_DESCRIPTION).tools[0]
    native = TodoListMiddleware().tools[0]
    assert actual.func is native.func
    assert actual.coroutine is native.coroutine
    assert actual.description == build_tool_catalog()['write_todos'].description == OFFICE_TODO_TOOL_DESCRIPTION
    assert actual.args_schema.model_validate({'todos': []}).model_dump() == {'todos': []}


class TodoModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.asyncio
@pytest.mark.parametrize('async_run', [False, True])
@pytest.mark.parametrize('final_todos', [[], [{'content': 'Finish the edit', 'status': 'completed'}]])
async def test_todo_native_loop_preserves_runtime_injection_and_replaces_full_list(async_run, final_todos):
    model = TodoModel(responses=[
        AIMessage(content='', tool_calls=[{
            'name': 'write_todos', 'id': 'plan-1',
            'args': {'todos': [{'content': 'Read the file', 'status': 'in_progress'}, {'content': 'Finish the edit', 'status': 'pending'}]},
        }]),
        AIMessage(content='', tool_calls=[{'name': 'write_todos', 'id': 'plan-2', 'args': {'todos': final_todos}}]),
        AIMessage(content='Done'),
    ])
    graph = create_agent(model, middleware=[TodoMiddleware(enforce_completion=False, tool_description=OFFICE_TODO_TOOL_DESCRIPTION)])
    initial = {'messages': [HumanMessage(content='Track the small edit')]}
    result = await graph.ainvoke(initial) if async_run else graph.invoke(initial)
    assert result['todos'] == final_todos
    messages = [message for message in result['messages'] if isinstance(message, ToolMessage)]
    assert [message.tool_call_id for message in messages] == ['plan-1', 'plan-2']
    assert all(message.status == 'success' for message in messages)


def test_library_query_is_forwarded_without_rewriting(monkeypatch):
    calls = []
    expected = {'results': [], 'message': '没有检索到相关资料'}

    def search(query):
        calls.append(query)
        return expected

    monkeypatch.setattr('deerflow.office.library_adapter.library_for_user', lambda user_id: SimpleNamespace(search=search))
    runtime = SimpleNamespace(context={'user_id': 'default'}, config={'configurable': {'user_id': 'default'}})
    result = search_office_library.func(query='公司提交代码前需要做什么？', runtime=runtime)
    assert result is expected
    assert calls == ['公司提交代码前需要做什么？']


def test_review_schema_documents_supported_formats_and_actual_options():
    definition = convert_to_openai_function(review_skill_package)
    fields = definition['parameters']['properties']
    assert definition['parameters']['required'] == ['target']
    assert fields['profile']['default'] == 'deerflow'
    assert fields['include_content']['default'] == 'semantic-review'
    assert fields['scope']['default'] is None
    assert fields['inline_content']['default'] is None
    assert 'HTTP/HTTPS' in fields['target']['description']
    assert 'skill://custom/<relative-path>' in fields['target']['description']
    assert 'none and facts-only currently both omit' in fields['include_content']['description']
    assert 'does not restrict which files or dimensions are scanned' in fields['scope']['description']


@pytest.mark.parametrize('include_content', ['none', 'facts-only', 'semantic-review'])
def test_review_content_options_keep_existing_semantics(include_content):
    runtime = SimpleNamespace(tool_call_id='review-content')
    response = review_skill_package.func(
        target='inline://SKILL.md', inline_content='---\nname: demo\ndescription: Review a demo.\n---\n# Demo\n',
        runtime=runtime, include_content=include_content,
    )
    message = response.update['messages'][0]
    assert message.status == 'success'
    payload = json.loads(message.content)
    assert bool(payload['artifacts']) is (include_content == 'semantic-review')
    assert payload['facts'] and payload['static_report']


def test_powershell_purpose_remains_a_fixed_enum_not_a_free_text_reason():
    fields = convert_to_openai_function(powershell_run)['parameters']['properties']
    assert fields['purpose']['enum'] == ['command', 'preview_service']
    assert fields['purpose']['default'] == 'command'
    assert 'not a free-text reason' in fields['purpose']['description']
    for purpose in ('command', 'preview_service'):
        assert powershell_run.tool_call_schema.model_validate({'command': 'Get-Location', 'purpose': purpose}).purpose == purpose
    with pytest.raises(ValidationError):
        powershell_run.tool_call_schema.model_validate({'command': 'Get-Location', 'purpose': '检查运行目录'})


def test_powershell_descriptions_keep_preview_rules_and_conditional_results():
    from deerflow.sandbox.powershell_tools import powershell_read

    definition = convert_to_openai_function(powershell_run)
    description = definition['description']
    assert 'actual HTTP response' in description
    assert 'build preview needs a rebuild' in description
    assert 'HTTP 200 alone' in description
    assert 'page functionality' in description
    assert 'never invent them' in description
    assert 'Historical' in powershell_read.description and 'unknown or stopped preview' in powershell_read.description
    assert 'Created sessions return an execution_id' in description
    assert 'explicit exit N can return N' in description
    assert 'Native non-zero exits do not stop' in description
    assert 'preview services default to the bound project' in description
    assert 'stop it before rewriting' not in description
    assert 'every failure' not in description
    purpose = definition['parameters']['properties']['purpose']['description']
    assert 'survives normal task end' in purpose and 'execution deadline' in purpose
    assert 'When a terminal result includes output_tail' in powershell_read.description


def test_tool_search_query_description_matches_regex_and_exact_name_syntax():
    query = build_tool_search_tool(DeferredToolCatalog(tools=()))
    definition = convert_to_openai_function(query)
    assert definition['parameters']['required'] == ['query']
    assert 'select:name1,name2' in definition['parameters']['properties']['query']['description']
    assert 'regular expression' in definition['parameters']['properties']['query']['description']
    assert 'not semantic search' in definition['description']
