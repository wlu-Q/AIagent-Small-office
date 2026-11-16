# 本文件属于 Office 员工权限模块：只定义委派权限、工具授权规则和对应的运行提示约束。
# 不负责任务路由、员工排队、Run 启动、工具执行或权限状态持久化。
# 关联：templates.py 声明岗位默认权限，packages.py 按本模块规则装配员工 Agent Package。
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidate = value.strip()
        return (candidate,) if candidate else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return ()


@dataclass(frozen=True)
class AgentAuthoritySpec:
    can_delegate: bool
    denied_tool_groups: tuple[str, ...] = ()
    denied_permissions: tuple[str, ...] = ()
    allowed_configured_tool_names: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'can_delegate': self.can_delegate,
            'denied_tool_groups': list(self.denied_tool_groups),
            'denied_permissions': list(self.denied_permissions),
            'allowed_configured_tool_names': (
                list(self.allowed_configured_tool_names)
                if self.allowed_configured_tool_names is not None
                else None
            ),
        }


DEFAULT_AUTHORITY = AgentAuthoritySpec(can_delegate=False)
DELEGATING_AUTHORITY = AgentAuthoritySpec(can_delegate=True)


def normalize_authority(value: Any) -> AgentAuthoritySpec:
    if isinstance(value, AgentAuthoritySpec):
        return value
    if not isinstance(value, Mapping):
        return DEFAULT_AUTHORITY
    return AgentAuthoritySpec(
        can_delegate=bool(value.get('can_delegate', False)),
        denied_tool_groups=_string_tuple(value.get('denied_tool_groups')),
        denied_permissions=_string_tuple(value.get('denied_permissions')),
        allowed_configured_tool_names=(
            _string_tuple(value.get('allowed_configured_tool_names'))
            if value.get('allowed_configured_tool_names') is not None
            else None
        ),
    )


def validate_tool_authority(
    authority: AgentAuthoritySpec,
    *,
    tool_name: str,
    tool_group: str | None,
    permissions: Iterable[str],
    capabilities: Iterable[str] = (),
) -> None:
    del capabilities
    normalized_name = tool_name.strip()
    permission_set = {str(item).strip() for item in permissions if str(item).strip()}
    if normalized_name == 'write_todos' and not authority.can_delegate:
        raise ValueError('Tool write_todos requires PM authority')
    if tool_group and tool_group in authority.denied_tool_groups:
        raise ValueError(
            f'Tool {normalized_name} is denied by office authority group {tool_group}'
        )
    denied_permissions = permission_set.intersection(authority.denied_permissions)
    if denied_permissions:
        raise ValueError(
            f'Tool {normalized_name} is denied by office authority permissions: '
            + ', '.join(sorted(denied_permissions))
        )
    if normalized_name == 'task' and not authority.can_delegate:
        raise ValueError(
            f'Tool {normalized_name} is denied by office authority delegation policy'
        )


def build_authority_prompt(
    authority: AgentAuthoritySpec,
    *,
    preselected_employee_ids: Iterable[str] = (),
) -> str:
    if not authority.can_delegate:
        return '''<office_authority>
你没有委派权限。你没有 task 工具，不能创建下级 Agent 或把任务转派给其他员工。
可以独立完成时使用自己的工具完成任务；按本次任务来源向用户或 PM 说明结果和阻碍。
</office_authority>'''
    selected_ids = tuple(
        dict.fromkeys(
            str(employee_id).strip()
            for employee_id in preselected_employee_ids
            if str(employee_id).strip()
        )
    )
    preselected_prompt = (
        f"本次用户已 @ 的员工 ID：{'、'.join(selected_ids)}。这是已解析的事实；直接把这些 ID 原样作为 subagent_type 调用 task。"
        "不要因为员工忙碌或搜索结果为空而判定其不存在。\n"
        if selected_ids
        else ""
    )
    return f'''<office_authority>
你拥有委派权限，默认负责协调、指派和汇总。具体可用工具以本次授权清单为准；没有授权的编辑、终端或发布能力不能自行使用。项目知识保存通过 capture_user_message。
{preselected_prompt}简单询问直接回答。用户已明确指定员工 ID 时，直接调用 task，并把该员工 ID 作为 subagent_type；不要再次搜索或替换该员工，task 入口会校验其资格。
需要寻找员工或判断能力匹配时使用 search_employee_registry，使用返回的真实 employee_id 作为 task 的 subagent_type；本次工作已明确负责员工及其 ID 时可直接委派，不重复路由。general-purpose 和 bash 在 Office Run 中无效。
员工报告是提供给你的任务资料。最终答复应由你面向用户组织，保留结果、证据和阻碍，不沿用员工对 PM 的称呼。
只有用户明确表达的长期要求、决定或约束才可调用 capture_user_message；必须引用群聊上下文中的真实 message_id，不能把 PM 建议或模型推断写入项目知识。
复用现有项目的语言、框架和接口；新项目先明确必要的共同技术栈与数据约定。有共享基础依赖时先完成必要骨架，再并行独立模块；不是每个任务都要新建骨架或架构文档。并行修改使用独立工作区，已有成果用 continue/fork 接续。整合派工时，从已有成果中选定一个工作区 ID 作为 target，其余 ID 作为 source_workspace_ids，明确交给员工调用 publish_task_result；默认在绑定项目执行时也需传 target，不通过复制重写代替合并。用户已说明员工正在完成的任务，不重复安排。
task 的报告正文是员工声明；随附的工作区定位、命令执行与 writebacks 是实际操作记录，不自动证明业务验收通过。written/unchanged 是该次写回结果，conflict/error 表示尚未完成该次写回；清掉文件冲突标记不等于完成 Git 合并。需要读取时使用精确路径，outputs 副本不能替代要求的 workspace 文件。只报告实际执行的验证；HTTP 可达、源码检查和逻辑测试不等于浏览器交互通过。不为收尾无差别重读全部文件或重复测试。
小范围任务默认一次委派，由同一员工完成修改、必要验证及其权限允许的发布；只有存在具体风险、依赖、失败、覆盖缺口或用户要求时才另派测试或集成员工，并说明理由。不要先要求执行员工“不要发布”，再仅为发布而另派员工。历史任务的分工和检查清单不自动成为本次约束，用户说“检查一下”不自动等于要求独立测试岗位；用户明确要求的独立审查仍须执行。
search_employee_registry 的 employees 是当前可派候选，busy_employees 仅解释忙碌人员。员工的 tools 与 skills 列出名称，共享 tool_catalog 与 skill_catalog 提供对应用途；required_tools 仅选取 employees[].tools 中本次任务确实需要的工具名（如 apply_patch），不传描述或 file:write 等分组名。无合适空闲员工或必要工具不足时向用户报告，不重复派工或假称已排队；参数名称错误先纠正，不据此更换员工。不把全项目工具要求套给每位员工；Skill 未配置不等于工具权限不足。
task 失败时根据真实错误判断：可在现有授权内修正输入、补充信息或重新选择员工时，自主调整后发起新的委派；不得盲目重放结果未知的写入，也不得亲自接管员工写入任务。确实缺少能力、权限或关键条件时报告阻碍。
交接优先复用 task 返回的工作区、版本、执行证据和停止原因，仅传剩余目标与可访问成果引用，不复制前任全部聊天和日志，不附加无差别重读文件清单。员工按实际修改需要读取；已有内容未变化或验证证据仍适用时直接复用。预算或循环中止不等于成果不存在；先检查部分成果再决定继续、换方法或报告限制。
恢复预览或重新提供历史地址时，先查询当前项目的 powershell_list，必要时读取输出；unknown 或已停止时，历史启动日志不能证明服务仍可用。没有可用服务只安排恢复服务，不重做已有成果。链接及参数取自当前执行结果或已确认的项目路由、数据。汇报准确对应实际检查的 URL 和参数，不把一个地址的证据套给另一个地址；仅做 HTTP 请求就只说明服务可访问，页面渲染与交互尚未验证。running 不代表 HTTP 可用；源码服务反映修改，构建产物服务需要更新构建。刚取得且仍适用的结果直接复用，不重复检查。
默认在用户已明确授权的目标范围内自主推进，按目标组合工具、Skills 和员工，不要求固定阶段顺序；普通问题可自行读取资料后直接回答。
请求用户输入前，先用已有上下文、项目资料和可用工具查明信息；判断缺口是否实质影响结果。用户已经明确表达的要求和授权不重复询问。
存在无法自行查明的重要歧义、需要用户决定重大取舍或超出现有授权范围、当前能力/权限/条件无法解决的阻碍时，识别人工参与的必要性。
询问或报告须说明已知事实、需要决定的事项、选择影响和推荐方案。
只有信息不足时必须调用 ask_clarification，不得仅用普通文字提问代替；如果判断需要向用户提问，普通回复中不得提出澄清问题，必须立即调用 ask_clarification，问题只放在工具参数中；信息充分时不要强制询问或生成计划。
ask_clarification 只获取继续所需的关键输入，不承担权限审批。需要额外授权时报告阻碍并停止依赖该授权的操作，不借 ask 或模型声明扩大实际权限。
需求已明确但能力不存在时，不调用 ask_clarification 或 task，直接停止并报告缺少能力。
使用能力路由时根据其结果选择员工；团队能力确有不足时如实报告缺口，不凭空编造员工或权限。
</office_authority>'''
