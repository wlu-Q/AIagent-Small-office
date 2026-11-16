"""Opt-in checks against the development model, using isolated project files."""

import asyncio
import json
import os
import time
from collections import Counter

import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, ToolMessage
from test_retrieval_evidence import environment as environment

from deerflow.agents.middlewares.retrieval_evidence import RetrievalEvidenceMiddleware
from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware
from deerflow.agents.thread_state import ThreadState
from deerflow.config import get_app_config
from deerflow.models.factory import create_chat_model
from deerflow.office.execution_workspace import workspace_guidance
from deerflow.sandbox import tools
from deerflow.sandbox.patch_tool import apply_patch_tool

pytestmark = pytest.mark.skipif(os.environ.get("OFFICE_RETRIEVAL_LIVE") != "1", reason="requires explicit real-model opt-in")


@pytest.mark.asyncio
@pytest.mark.parametrize("optimized", [False, True])
async def test_live_button_edit_and_dev_command(environment, optimized):
    project, _, runtime, _ = environment
    (project / "index.html").write_text('<html><body><button id="signup">报名</button></body></html>', encoding="utf-8")
    config = get_app_config()
    selected = next(model for model in config.models if "mimo" in model.model.lower())
    model = create_chat_model(name=selected.name, thinking_enabled=None, app_config=config, attach_tracing=False)
    middlewares = [ToolOutputBudgetMiddleware.from_app_config(config)]
    if optimized:
        middlewares.append(RetrievalEvidenceMiddleware())
    agent = create_agent(model=model, tools=[tools.ls_tool, tools.glob_tool, tools.grep_tool, tools.read_file_tool, apply_patch_tool],
                         middleware=middlewares, state_schema=ThreadState,
                         system_prompt="你是前端工程师，按用户目标修改当前项目。" + workspace_guidance(None))
    started = time.monotonic()
    result = await asyncio.wait_for(agent.ainvoke({**runtime.state, "messages": [HumanMessage(
        content="把主页报名按钮改成‘立即报名’，其他地方别动。顺便告诉我这个项目怎么启动。"
    )]}, context=runtime.context), timeout=180)
    elapsed = time.monotonic() - started
    messages = result["messages"]
    calls = Counter(message.name for message in messages if isinstance(message, ToolMessage))
    usage = Counter()
    for message in messages:
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            usage[key] += (getattr(message, "usage_metadata", None) or {}).get(key, 0)
    assert (project / "index.html").read_text(encoding="utf-8") == '<html><body><button id="signup">立即报名</button></body></html>'
    assert json.loads((project / "package.json").read_text())["scripts"]["dev"] == "vite"
    assert "dev" in str(messages[-1].content)
    assert all(message.status == "success" for message in messages if isinstance(message, ToolMessage))
    print(json.dumps({"model": selected.model, "optimized": optimized, "seconds": round(elapsed, 2),
                      "tools": dict(calls), "usage": dict(usage)}, ensure_ascii=False))
