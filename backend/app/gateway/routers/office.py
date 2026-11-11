# 本文件属于 Office HTTP 边界模块：只做认证、请求校验、字段映射和窄服务调用。
# 不负责消息路由规则、员工排队、Run 启动实现、历史存储或状态机维护。
# 关联：deps.py 提供进程级服务，Office 各领域模块完成业务，DeerFlow Gateway 返回 Run 与 Event 事实。
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.gateway.deps import (
    get_run_manager,
)
from app.gateway.services import start_run
from deerflow.office.employees import OfficeEmployeeService
from deerflow.office.group_messages import GroupMessage, GroupMessageStore
from deerflow.office.message_pipeline import OfficeMessagePipeline
from deerflow.office.messages import OfficeMessageCommand
from deerflow.office.model_management import connection_error_detail
from deerflow.office.projects import OfficeProjectService
from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun
from deerflow.office.run_view import OfficeRunView
from deerflow.office.templates import list_employee_templates
from deerflow.runtime.runs.manager import CancelOutcome
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api/office", tags=["office"])


def get_office_workflow_service(request: Request):
    service = getattr(request.app.state, 'office_workflow_service', None)
    if service is None:
        raise HTTPException(503, 'Workflow service unavailable')
    if service.store.user_id != get_effective_user_id():
        raise HTTPException(403, 'Workflow user mismatch')
    return service


@router.get('/workflows/current')
async def current_workflow(service=Depends(get_office_workflow_service)):
    wf = service.get()
    return {'workflow': wf.to_record() if wf else None}


@router.get('/workflows/{workflow_id}')
async def get_workflow(workflow_id: str, service=Depends(get_office_workflow_service)):
    try:
        wf = service.get(workflow_id)
        return {'workflow': wf.to_record() if wf else None}
    except Exception as error:
        _raise_http_error(error)


@router.post('/workflows/{workflow_id}/cancel')
async def cancel_workflow(workflow_id: str, service=Depends(get_office_workflow_service)):
    try:
        return {'workflow': (await service.cancel(workflow_id)).to_record()}
    except Exception as error:
        _raise_http_error(error)


def get_office_bootstrap_service(request: Request) -> Any:
    service = getattr(request.app.state, "office_bootstrap_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Office bootstrap service not available",
        )
    return service


def get_office_employee_service(request: Request) -> OfficeEmployeeService:
    service = getattr(request.app.state, "office_employee_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Office employee service not available")
    if service.user_id != get_effective_user_id():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Office employee service user mismatch")
    return service


def get_office_project_service(request: Request) -> OfficeProjectService:
    service = getattr(request.app.state, 'office_project_service', None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Office project service not available',
        )
    if service.user_id != get_effective_user_id():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Office project service user mismatch',
        )
    return service


def get_office_group_message_store(request: Request) -> GroupMessageStore:
    store = getattr(request.app.state, "office_group_message_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Office group message store not available",
        )
    if store.user_id != get_effective_user_id():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Office group message store user mismatch",
        )
    return store


def get_office_employee_run_gate(request: Request) -> EmployeeRunGate:
    gate = getattr(request.app.state, "office_employee_run_gate", None)
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Office employee run gate not available",
        )
    return gate


def get_office_run_view(request: Request) -> OfficeRunView:
    view = getattr(request.app.state, "office_run_view", None)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Office run view not available",
        )
    if view.user_id != get_effective_user_id():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Office run view user mismatch",
        )
    return view


async def _start_office_run(body: Any, thread_id: str, request: Request):
    return await start_run(
        body,
        thread_id,
        request,
        trusted_context=getattr(body, 'context', None),
    )


def get_office_message_pipeline(request: Request) -> OfficeMessagePipeline:
    pipeline = getattr(request.app.state, "office_message_pipeline", None)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Office message pipeline not available",
        )
    if pipeline.user_id != get_effective_user_id():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Office message pipeline user mismatch",
        )
    return pipeline


def _raise_http_error(error: Exception) -> None:
    if getattr(error, 'public_code', None):
        code = 404 if isinstance(error, FileNotFoundError) else 409 if isinstance(error, RuntimeError) else 400
        raise HTTPException(code, {'code': error.public_code, 'params': error.public_params}) from None
    if isinstance(error, APIStatusError):
        raise HTTPException(
            status_code=error.status_code,
            detail={'code': 'errors.provider_http' if error.status_code >= 500 else connection_error_detail(error)[0], 'params': {'status': error.status_code}},
        ) from error
    if isinstance(error, APITimeoutError):
        raise HTTPException(status_code=504, detail={'code': 'errors.provider_timeout', 'params': {}}) from error
    if isinstance(error, APIConnectionError):
        raise HTTPException(status_code=502, detail={'code': 'errors.provider_verification', 'params': {}}) from error
    if isinstance(error, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    if isinstance(error, FileNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(error, FileExistsError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, RuntimeError) and "active run" in str(error).lower():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, RuntimeError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    raise error


def _require_boolean(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field} must be a boolean")
    return value


def _group_message_payload(message: GroupMessage) -> dict[str, Any]:
    return {
        "messageId": message.message_id,
        "sequence": message.sequence,
        "senderKind": message.sender_kind,
        "senderId": message.sender_id,
        "senderDisplayName": message.sender_display_name,
        "senderAvatar": message.sender_avatar,
        "content": message.content,
        "mentionEmployeeIds": list(message.mention_employee_ids),
        "sourceRunId": message.source_run_id,
        "createdAt": message.created_at,
    }


def _queue_item_payload(item: QueuedOfficeRun) -> dict[str, Any]:
    return {
        "queueId": item.queue_id,
        "targetEmployeeId": item.target_employee_id,
        "sourceKind": item.source_kind,
        "conversationKind": item.conversation_kind,
        "text": item.text,
        "enqueuedAt": item.enqueued_at,
    }


@router.get('/group/execution')
async def group_execution_state(pipeline: OfficeMessagePipeline = Depends(get_office_message_pipeline)):
    try:
        return await pipeline.group_execution.state()
    except Exception as error:
        _raise_http_error(error)


@router.post('/group/pause')
async def pause_group_execution(pipeline: OfficeMessagePipeline = Depends(get_office_message_pipeline)):
    try:
        # Client disconnect must not leave half the group running.
        return await asyncio.shield(pipeline.group_execution.pause())
    except Exception as error:
        _raise_http_error(error)


def get_office_group_reset(request: Request) -> Any:
    service = getattr(request.app.state, "office_group_reset", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Office group reset service not available",
        )
    if service.user_id != get_effective_user_id():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Office group reset user mismatch",
        )
    return service


@router.post('/group/reset')
async def reset_group_conversation(service: Any = Depends(get_office_group_reset)):
    try:
        # Client disconnect must not leave the reset half applied.
        return await asyncio.shield(service.reset())
    except Exception as error:
        _raise_http_error(error)


@router.post("/messages")
async def send_office_message(
    payload: dict[str, Any],
    request: Request,
    pipeline: OfficeMessagePipeline = Depends(get_office_message_pipeline),
) -> dict[str, Any]:
    try:
        allowed = {
            "messageId",
            "conversationKind",
            "text",
            "targetEmployeeId",
            "mentionEmployeeIds",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"message contains unsupported fields: {sorted(unknown)}")
        mentions = payload.get("mentionEmployeeIds", [])
        if not isinstance(mentions, list) or not all(isinstance(value, str) for value in mentions):
            raise ValueError("mentionEmployeeIds must be an array of employee ids")
        return await pipeline.send(
            payload.get("messageId"),
            OfficeMessageCommand(
                conversation_kind=payload.get("conversationKind"),
                text=payload.get("text"),
                target_employee_id=payload.get("targetEmployeeId"),
                mention_employee_ids=tuple(mentions),
            ),
            start_run=_start_office_run,
            request=request,
        )
    except Exception as error:
        _raise_http_error(error)


@router.get("/group/messages")
def list_office_group_messages(
    after_sequence: int = Query(default=0, alias="afterSequence", ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    store: GroupMessageStore = Depends(get_office_group_message_store),
) -> dict[str, Any]:
    try:
        messages = store.list(after_sequence=after_sequence, limit=limit)
        return {
            "messages": [_group_message_payload(message) for message in messages],
            "nextAfterSequence": messages[-1].sequence if messages else after_sequence,
        }
    except Exception as error:
        _raise_http_error(error)


@router.get("/employees/{employee_id}/conversations/{kind}/messages")
async def list_employee_conversation_messages(
    employee_id: str,
    kind: str,
    limit: int = Query(default=100, ge=1, le=500),
    view: OfficeRunView = Depends(get_office_run_view),
) -> dict[str, Any]:
    try:
        if kind not in {"private", "group"}:
            raise ValueError(f"Unsupported conversation kind: {kind}")
        return await view.list_conversation_messages(employee_id, kind, limit=limit)
    except Exception as error:
        _raise_http_error(error)


@router.get("/queue")
async def list_office_queue(
    gate: EmployeeRunGate = Depends(get_office_employee_run_gate),
) -> dict[str, Any]:
    return {"items": [_queue_item_payload(item) for item in await gate.list_pending()]}


@router.patch("/queue/{queue_id}")
async def edit_office_queue_item(
    queue_id: str,
    payload: dict[str, Any],
    gate: EmployeeRunGate = Depends(get_office_employee_run_gate),
) -> dict[str, Any]:
    try:
        if set(payload) != {"text"}:
            raise ValueError("Only queued message text can be edited")
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        return {"item": _queue_item_payload(await gate.edit(queue_id, text))}
    except Exception as error:
        _raise_http_error(error)


@router.delete("/queue/{queue_id}")
async def delete_office_queue_item(
    queue_id: str,
    gate: EmployeeRunGate = Depends(get_office_employee_run_gate),
) -> dict[str, Any]:
    try:
        return {"item": _queue_item_payload(await gate.delete(queue_id))}
    except Exception as error:
        _raise_http_error(error)


@router.get('/project')
def get_current_project(
    service: OfficeProjectService = Depends(get_office_project_service),
) -> dict[str, Any]:
    try:
        return {'workspace': service.get_summary()}
    except Exception as error:
        _raise_http_error(error)


@router.put('/project')
def bind_current_project(
    payload: dict[str, Any],
    service: OfficeProjectService = Depends(get_office_project_service),
) -> dict[str, Any]:
    try:
        unknown = set(payload) - {'path', 'ifUnbound'}
        if unknown:
            raise ValueError(f'project contains unsupported fields: {sorted(unknown)}')
        if_unbound = payload.get('ifUnbound', False)
        if not isinstance(if_unbound, bool):
            raise ValueError('ifUnbound must be a boolean')
        return {
            'workspace': service.bind(
                payload.get('path'),
                if_unbound=if_unbound,
            )
        }
    except Exception as error:
        _raise_http_error(error)


@router.get('/project/context')
def get_project_context(
    task: str | None = None,
    actor: str | None = None,
    service: OfficeProjectService = Depends(get_office_project_service),
) -> dict[str, Any]:
    try:
        return {'context': service.get_context(task=task, actor=actor)}
    except Exception as error:
        _raise_http_error(error)


@router.post('/project/files/read')
def read_project_files(
    payload: dict[str, Any],
    service: OfficeProjectService = Depends(get_office_project_service),
) -> dict[str, Any]:
    try:
        return {'files': service.read_files(payload.get('paths'))}
    except Exception as error:
        _raise_http_error(error)


async def _resolve_presented_file(payload: dict[str, Any], service: OfficeProjectService,
                                  view: OfficeRunView) -> str:
    if set(payload) - {'path', 'employeeId', 'runId', 'projectId'}:
        raise ValueError('文件请求包含不支持的字段')
    thread_id = None
    workspace_context = None
    project_id = payload.get('projectId')
    if project_id is not None and (not isinstance(project_id, str) or not project_id):
        raise ValueError('项目标识无效')
    if payload.get('employeeId') or payload.get('runId'):
        if not isinstance(payload.get('employeeId'), str) or not isinstance(payload.get('runId'), str):
            raise ValueError('员工与 Run 必须一起提供')
        _, record = await view.resolve_run(payload['employeeId'], payload['runId'])
        workspace_context = (record.kwargs.get('launch_contract') or {}).get('trusted_context') or {}
        thread_id = record.thread_id
        run_project = (record.metadata or {}).get('office_project_id')
        raw = str(payload.get('path', '')).replace('\\', '/')
        if not raw.startswith('/mnt/user-data/') or raw == '/mnt/user-data/workspace' or raw.startswith('/mnt/user-data/workspace/'):
            if not run_project:
                raise ValueError('无法确认此 Run 的文件所属项目')
            if project_id is not None and project_id != run_project:
                raise ValueError('文件项目与所属 Run 不一致')
            project_id = run_project
    return await asyncio.to_thread(service.resolve_file_location, payload.get('path'),
                                   owned_thread_id=thread_id, expected_project_id=project_id,
                                   workspace_context=workspace_context)


@router.post('/files/resolve')
async def resolve_office_file(
    payload: dict[str, Any],
    service: OfficeProjectService = Depends(get_office_project_service),
    view: OfficeRunView = Depends(get_office_run_view),
) -> dict[str, str]:
    try:
        return {'path': await _resolve_presented_file(payload, service, view)}
    except Exception as error:
        _raise_http_error(error)


@router.post('/files/preview')
async def preview_office_file(
    payload: dict[str, Any],
    service: OfficeProjectService = Depends(get_office_project_service),
    view: OfficeRunView = Depends(get_office_run_view),
) -> dict[str, Any]:
    from deerflow.office.file_preview import preview_file
    try:
        reference = dict(payload)
        metadata_only = reference.pop('metadataOnly', False)
        if not isinstance(metadata_only, bool):
            raise ValueError('metadataOnly 必须为布尔值')
        path = await _resolve_presented_file(reference, service, view)
        return await asyncio.to_thread(preview_file, Path(path), metadata_only=metadata_only)
    except Exception as error:
        _raise_http_error(error)


@router.get('/project/search')
def search_project(
    query: str,
    limit: int = Query(default=8, ge=1, le=50),
    service: OfficeProjectService = Depends(get_office_project_service),
) -> dict[str, Any]:
    try:
        return {'results': service.search(query, limit=limit)}
    except Exception as error:
        _raise_http_error(error)


@router.get("/employee-templates")
def list_creatable_employee_templates() -> dict[str, Any]:
    return {
        "templates": [
            {
                "templateId": template.template_id,
                "role": template.role,
                "category": template.category,
                "tags": list(template.tags),
                "specialties": list(template.specialties),
                "prompt": template.prompt,
                "skills": list(template.skills),
            }
            for template in list_employee_templates()
            if not template.is_pm
        ]
    }


@router.get("/bootstrap")
def get_office_bootstrap(
    service: Any = Depends(get_office_bootstrap_service),
) -> dict[str, Any]:
    return service.get_status()


@router.post("/bootstrap/test-model")
async def test_office_bootstrap_model(
    payload: dict[str, Any],
    service: Any = Depends(get_office_bootstrap_service),
) -> dict[str, Any]:
    try:
        return await service.test_connection(payload)
    except Exception as error:
        _raise_http_error(error)


@router.post("/bootstrap")
async def initialize_office_bootstrap(
    payload: dict[str, Any],
    service: Any = Depends(get_office_bootstrap_service),
) -> dict[str, Any]:
    try:
        return await service.initialize(payload)
    except Exception as error:
        _raise_http_error(error)


@router.get("/settings")
def get_office_settings(
    service: Any = Depends(get_office_bootstrap_service),
) -> dict[str, Any]:
    return service.get_status()


@router.patch("/settings")
async def update_office_settings(
    payload: dict[str, Any],
    service: Any = Depends(get_office_bootstrap_service),
) -> dict[str, Any]:
    try:
        return await service.update_settings(payload)
    except Exception as error:
        _raise_http_error(error)


@router.post("/employees", status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: dict[str, Any],
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    try:
        if "modelProfileId" in payload:
            raise ValueError("Employees follow the office model; modelProfileId cannot be supplied")
        return {"employee": await service.create_employee(payload)}
    except Exception as error:
        _raise_http_error(error)


@router.get('/models')
def list_office_models(service: Any = Depends(get_office_bootstrap_service)):
    return service.models.list()


@router.get('/models/{model_id}')
async def get_office_model(model_id: str, service: Any = Depends(get_office_bootstrap_service)):
    try:
        return await service.models.detail(model_id)
    except Exception as error:
        _raise_http_error(error)


@router.post('/models')
async def add_office_model(payload: dict[str, Any], service: Any = Depends(get_office_bootstrap_service)):
    try:
        return await service.models.add(payload)
    except Exception as error:
        _raise_http_error(error)


@router.put('/models/default')
async def set_office_default_model(payload: dict[str, Any], service: Any = Depends(get_office_bootstrap_service)):
    try:
        if set(payload) != {'modelId'} or not isinstance(payload['modelId'], str):
            raise ValueError('请选择有效的模型')
        return await service.models.set_default(payload['modelId'])
    except Exception as error:
        _raise_http_error(error)


@router.put('/models/{model_id}')
async def update_office_model(model_id: str, payload: dict[str, Any], service: Any = Depends(get_office_bootstrap_service)):
    try:
        return await service.models.update(model_id, payload)
    except Exception as error:
        _raise_http_error(error)


@router.get('/tools')
def list_employee_tool_catalog(service: OfficeEmployeeService = Depends(get_office_employee_service)):
    from dataclasses import asdict
    from deerflow.office.tool_contract import build_tool_catalog
    return {'tools': [asdict(entry) for entry in build_tool_catalog().values()]}


def _execution_approvals(service):
    from deerflow.office.execution_facts import ExecutionReceiptStore
    from deerflow.office.terminal import manager_for
    return manager_for(ExecutionReceiptStore(paths=service.paths, user_id=service.user_id)).approvals


@router.get('/execution-approvals')
async def list_execution_approvals(request: Request, service: OfficeEmployeeService = Depends(get_office_employee_service)):
    items = _execution_approvals(service).pending()
    manager = get_run_manager(request)
    runs = {}
    for item in items:
        if item['run_id'] not in runs:
            runs[item['run_id']] = await manager.get(item['run_id'], user_id=service.user_id)
        run = runs[item['run_id']]
        metadata = (run.metadata or {}) if run else {}
        item['conversation_kind'] = metadata.get('office_conversation_kind')
        item['conversation_employee_id'] = run.assistant_id if run else None
        item['project_id'] = metadata.get('office_project_id')
        try:
            item['employee_name'] = service.get_employee_summary(item['employee_id'])['displayName']
        except (FileNotFoundError, ValueError):
            item['employee_name'] = item['employee_id']
    return {'items': items}


@router.post('/execution-approvals/{approval_id}/decision')
def decide_execution_approval(approval_id: str, payload: dict[str, Any], service: OfficeEmployeeService = Depends(get_office_employee_service)):
    try:
        if set(payload) != {'request_hash', 'decision'}:
            raise ValueError('Expected request_hash and decision')
        return _execution_approvals(service).decide(approval_id, payload['request_hash'], payload['decision'])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/employees/{employee_id}/tools')
def get_employee_tools(employee_id: str, service: OfficeEmployeeService = Depends(get_office_employee_service)):
    try:
        return service.get_employee_tools(employee_id)
    except Exception as error:
        _raise_http_error(error)


@router.put('/employees/{employee_id}/tools')
async def save_employee_tools(employee_id: str, payload: dict[str, Any], service: OfficeEmployeeService = Depends(get_office_employee_service)):
    try:
        return await service.save_employee_tools(employee_id, payload)
    except Exception as error:
        _raise_http_error(error)


@router.get("/employees")
async def list_employees(
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    employees = await service.list_employees()
    return {
        "employeeIds": [employee["id"] for employee in employees],
        "employees": employees,
    }


@router.get("/employees/{employee_id}")
def get_employee(
    employee_id: str,
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    try:
        return service.get_employee_detail(employee_id)
    except Exception as error:
        _raise_http_error(error)


@router.put("/employees/{employee_id}/personal-info")
async def save_employee_personal_info(
    employee_id: str,
    payload: dict[str, Any],
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    try:
        detail = await service.save_personal_info(employee_id, payload)
        return {"employee": detail["employee"]}
    except Exception as error:
        _raise_http_error(error)


@router.delete("/employees/{employee_id}")
async def delete_employee(
    employee_id: str,
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    try:
        return {"deleted": await service.delete_employee(employee_id)}
    except Exception as error:
        _raise_http_error(error)


@router.post("/employees/{employee_id}/disable")
def disable_employee(
    employee_id: str,
    payload: dict[str, Any],
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    try:
        reason = str(payload.get("reason") or payload.get("disabledReason") or "")
        return {"employee": service.disable_employee(employee_id, reason)}
    except Exception as error:
        _raise_http_error(error)


@router.post("/employees/{employee_id}/enable")
def enable_employee(
    employee_id: str,
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    try:
        return {"employee": service.enable_employee(employee_id)}
    except Exception as error:
        _raise_http_error(error)


@router.get("/employees/{employee_id}/capabilities")
def get_employee_capabilities(
    employee_id: str,
    service: OfficeEmployeeService = Depends(get_office_employee_service),
) -> dict[str, Any]:
    try:
        return {"capabilityIndex": service.get_capability_index(employee_id)}
    except Exception as error:
        _raise_http_error(error)


@router.get("/employees/{employee_id}/runs")
async def list_employee_runs(
    employee_id: str,
    conversation_kind: str = Query(alias="conversationKind"),
    limit: int = Query(default=20, ge=1, le=100),
    view: OfficeRunView = Depends(get_office_run_view),
) -> dict[str, Any]:
    try:
        return await view.list_runs(
            employee_id,
            conversation_kind,
            limit=limit,
        )
    except Exception as error:
        _raise_http_error(error)


@router.get("/employees/{employee_id}/runs/{run_id}")
async def get_employee_run(
    employee_id: str,
    run_id: str,
    view: OfficeRunView = Depends(get_office_run_view),
) -> dict[str, Any]:
    try:
        return await view.get_run(employee_id, run_id)
    except Exception as error:
        _raise_http_error(error)


@router.get("/employees/{employee_id}/runs/{run_id}/events")
async def list_employee_run_events(
    employee_id: str,
    run_id: str,
    after_seq: int | None = Query(default=None, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    view: OfficeRunView = Depends(get_office_run_view),
) -> dict[str, Any]:
    try:
        return await view.list_events(
            employee_id,
            run_id,
            after_seq=after_seq,
            limit=limit,
        )
    except Exception as error:
        _raise_http_error(error)


@router.post("/employees/{employee_id}/runs/{run_id}/cancel")
async def cancel_employee_run(
    employee_id: str,
    run_id: str,
    request: Request,
    view: OfficeRunView = Depends(get_office_run_view),
    gate: EmployeeRunGate = Depends(get_office_employee_run_gate),
) -> dict[str, Any]:
    try:
        normalized, record = await view.resolve_run(employee_id, run_id)
        is_pm_run = view.is_pm(normalized)
        run_manager = get_run_manager(request)
        if record.status in {'pending', 'running', 'interrupted'}:
            pipeline = get_office_message_pipeline(request)
            await pipeline.cancel_wait(record)
        outcome = await run_manager.cancel(
            record.run_id,
            action="interrupt",
            user_id=view.user_id,
        )
        if outcome in {CancelOutcome.unknown, CancelOutcome.not_active_locally}:
            raise RuntimeError(f"Run cannot be cancelled locally: {run_id}")
        if is_pm_run and outcome == CancelOutcome.cancelled:
            await gate.cancel_parent(record.run_id)
        current = await run_manager.get(
            record.run_id,
            user_id=view.user_id,
        )
        return view.serialize_run(normalized, current or record)
    except Exception as error:
        _raise_http_error(error)


@router.post("/employees/{employee_id}/runs/{run_id}/resume")
async def resume_employee_run(
    employee_id: str,
    run_id: str,
    payload: dict[str, Any],
    request: Request,
    pipeline: OfficeMessagePipeline = Depends(get_office_message_pipeline),
) -> dict[str, Any]:
    try:
        source = payload.get("source")
        request_id = payload.get("requestId")
        text = payload.get("text")
        if not isinstance(source, str):
            raise ValueError("source must be a string")
        if not isinstance(request_id, str):
            raise ValueError("requestId must be a string")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        return await pipeline.resume(
            employee_id,
            run_id,
            source=source,
            request_id=request_id,
            text=text,
            start_run=_start_office_run,
            request=request,
        )
    except Exception as error:
        _raise_http_error(error)
