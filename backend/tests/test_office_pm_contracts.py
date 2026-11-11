import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from deerflow.config.paths import Paths

from deerflow.runtime.runs.execution import RunExecutionSpecV1, RunLaunchContractV1


def identity(**changes):
    from deerflow.office.execution_facts import WorkIdentity

    return WorkIdentity(**(dict(user_id="alice", project_id="p", thread_id="t", work_id="w") | changes))


def test_resume_preserves_work_identity(tmp_path):
    from deerflow.office.execution_facts import WorkIdentity

    work = identity()
    contract = RunLaunchContractV1.build(
        model_profile_id="model",
        execution_spec=RunExecutionSpecV1(version=1, mode="new", input={"messages": []}),
        config={},
        context={},
        trusted_context={"office_work": work.to_record()},
        stream_modes=["values"],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    )
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract.to_dict()), encoding="utf-8")
    restored = RunLaunchContractV1.parse(json.loads(path.read_text(encoding="utf-8")))
    assert WorkIdentity.from_record(restored.trusted_context["office_work"]) == work
    assert WorkIdentity.from_record(restored.trusted_context["office_work"]).work_id == "w"


def test_delegation_identity_survives_parent_run_change():
    from deerflow.office.execution_facts import DelegationIdentity

    first = DelegationIdentity(work=identity(), ai_message_id="ai-1", tool_call_id="call-1")
    replay = DelegationIdentity(work=identity(), ai_message_id="ai-1", tool_call_id="call-1")
    assert first.key == replay.key
    for other in (replace(first, tool_call_id="call-2"), replace(first, ai_message_id="ai-2"), replace(first, work=identity(work_id="new")), replace(first, work=identity(user_id="bob"))):
        assert first.key != other.key
    with pytest.raises(ValueError):
        DelegationIdentity(work=identity(), ai_message_id="", tool_call_id="call-1")


def test_pending_question_is_not_inferred_from_answer_message():
    from deerflow.office.execution_facts import PendingAsk

    source = SimpleNamespace(run_id="r", user_id="alice", thread_id="t", assistant_id="pm", status="interrupted")
    question = {"kind": "human_input_request", "source": "ask_clarification", "request_id": "q"}
    pending = PendingAsk.from_interrupt(work=identity(), record=source, payload=question)
    assert pending.source_run_id == "r" and pending.request_id == "q"
    for payload in ({"kind": "human_input_response", "request_id": "q"}, {"content": "answer"}):
        with pytest.raises(ValueError):
            PendingAsk.from_interrupt(work=identity(), record=source, payload=payload)
    source.user_id = "bob"
    with pytest.raises(ValueError):
        PendingAsk.from_interrupt(work=identity(), record=source, payload=question)


@pytest.mark.anyio
async def test_launcher_persists_work_identity_and_resume_keeps_it(tmp_path, monkeypatch):
    from test_office_run_launch import CapturingStarter, CheckpointerStub, EmployeeServiceStub, ProjectBindingStub

    from deerflow.office.run_launch import OfficeRunLauncher

    EmployeeServiceStub.paths = Paths(tmp_path / "state")
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setattr(ProjectBindingStub, 'path', project)
    starter = CapturingStarter()
    launcher = OfficeRunLauncher(employee_service=EmployeeServiceStub(), checkpointer=CheckpointerStub())
    await launcher.launch(
        "employee-one",
        "profile-primary",
        RunExecutionSpecV1(version=1, mode="new", input={"messages": [{"role": "user", "content": "build"}]}),
        start_run=starter,
        request=object(),
        thread_id="office__default__employee-one__private",
        office_lease_id="lease",
        project_binding=ProjectBindingStub(),
        message_metadata={"office_message_id": "m1"},
    )
    body = starter.calls[-1][0]
    assert body.context["office_work"]["work_id"] == body.metadata["office_work_id"]
    contract = RunLaunchContractV1.build(
        model_profile_id="profile-primary",
        execution_spec=RunExecutionSpecV1.parse(body.execution_spec),
        config=body.config,
        context={key: value for key, value in body.context.items() if key not in {"office_work", "office_work_context"}},
        trusted_context={key: body.context[key] for key in ("office_work", "office_work_context")},
        stream_modes=["values"],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    )
    source = SimpleNamespace(run_id="run-interrupted", thread_id="office__default__employee-one__private", assistant_id="employee-one", user_id="default", kwargs={"launch_contract": contract.to_dict()}, metadata=body.metadata)
    await launcher.resume(
        source,
        {"version": 1, "kind": "human_input_response", "source": "ask_clarification", "request_id": "q", "response_kind": "text", "value": "use department"},
        start_run=starter,
        request=object(),
        office_lease_id="lease-2",
        office_queue_id="resume-q",
    )
    resumed = starter.calls[-1][0]
    assert resumed.context["office_work"] == body.context["office_work"]
    assert resumed.metadata["office_work_id"] == body.metadata["office_work_id"]
    assert resumed.context["office_work_context"]["latest_user_message"] == "use department"


@pytest.mark.anyio
async def test_resume_does_not_trust_work_identity_in_public_contract_context(tmp_path):
    from test_office_run_launch import CapturingStarter, CheckpointerStub, EmployeeServiceStub, ProjectBindingStub

    from deerflow.office.run_launch import OfficeRunLauncher

    spoofed = dict(user_id="default", project_id="project-one", thread_id="office__default__employee-one__private", work_id="spoofed")
    contract = RunLaunchContractV1.build(
        model_profile_id="profile-primary",
        execution_spec=RunExecutionSpecV1(version=1, mode="new", input={"messages": [{"role": "user", "content": "original goal"}]}),
        config={},
        context={"office_work": spoofed, "office_work_context": {**spoofed, "goal": "spoofed goal"}},
        trusted_context=ProjectBindingStub.to_context(),
        stream_modes=["values"],
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    )
    source = SimpleNamespace(run_id="run-interrupted", thread_id=spoofed["thread_id"], assistant_id="employee-one", user_id="default", kwargs={"launch_contract": contract.to_dict()}, metadata={})
    EmployeeServiceStub.paths = Paths(tmp_path / "state")
    starter = CapturingStarter()
    launcher = OfficeRunLauncher(employee_service=EmployeeServiceStub(), checkpointer=CheckpointerStub())
    await launcher.resume(
        source,
        {"version": 1, "kind": "human_input_response", "source": "ask_clarification", "request_id": "q", "response_kind": "text", "value": "answer"},
        start_run=starter,
        request=object(),
        office_lease_id="lease",
        office_queue_id="resume-q",
    )
    resumed = starter.calls[-1][0]
    assert resumed.context["office_work"]["work_id"] == "run:run-interrupted"
    assert resumed.context["office_work_context"]["goal"] == "original goal"
