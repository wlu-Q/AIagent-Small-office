# 本文件属于 DeerFlow Runtime 上下文键模块：只定义跨 Runtime 组件共享的内部键名。
# 不负责消息路由、状态保存、Thread 管理或业务决策。
# 关联：Gateway 写入运行模式，Run Worker 注入 Runtime，Middleware 读取运行边界。
"""Private runtime context keys shared across DeerFlow runtime components."""

from typing import Final

OFFICE_PROJECT_ID_CONTEXT_KEY: Final[str] = 'office_project_id'
OFFICE_PROJECT_PATH_CONTEXT_KEY: Final[str] = 'office_project_path'
OFFICE_WORKFLOW_CONTEXT_KEY: Final[str] = 'office_workflow'
OFFICE_PROJECT_KNOWLEDGE_CONTEXT_KEY: Final[str] = 'office_project_knowledge'
OFFICE_WORK_CONTEXT_KEY: Final[str] = 'office_work_context'
OFFICE_WORK_IDENTITY_CONTEXT_KEY: Final[str] = 'office_work'

CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY: Final[str] = "__deerflow_pre_run_message_ids"
RUN_EXECUTION_MODE_CONTEXT_KEY: Final[str] = "__deerflow_execution_mode"
RUNTIME_BOUND_TOOL_NAMES_METADATA_KEY: Final[str] = "runtime_bound_tool_names"
RUNTIME_MODEL_ID_METADATA_KEY: Final[str] = "runtime_model_id"
