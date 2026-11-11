# 本文件属于 Office 固定岗位模板模块：只声明系统支持的岗位事实、默认能力包和模板查询校验。
# 不负责创建员工、修改模板实例、动态画像、任务路由、排队或 Agent 执行。
# 关联：employees.py 用模板创建员工，capability_registry.py 用模板事实构造能力快照。
from __future__ import annotations

from dataclasses import dataclass

from deerflow.office.authority import (
    DEFAULT_AUTHORITY,
    DELEGATING_AUTHORITY,
    AgentAuthoritySpec,
)


@dataclass(frozen=True)
class EmployeeTemplate:
    template_id: str
    role: str
    is_pm: bool
    prompt: str
    category: str
    tags: tuple[str, ...]
    specialties: tuple[str, ...]
    skills: tuple[str, ...]
    tool_groups: tuple[str, ...]
    authority: AgentAuthoritySpec


# 跨岗位共用的边界规则，追加在可招聘岗位默认文案的末尾。员工的岗位范围只由 SOUL 里的
# 描述界定，系统不额外注入 category/tags/specialties，所以这条必须写进默认文案本身。
# PM 不适用：它的职责是转派而不是拒绝，这条会诱导它说「我不做」，反而掐断工作流闭环；
# 招聘接口本来也不暴露 PM 模板（app/gateway/routers/office.py 过滤 is_pm）。
SCOPE_BOUNDARY_RULE = "用户需求明显超出岗位范围时，先说明自己的岗位职责和边界，不要向用户承诺。"


_TEMPLATES = (
    # 参考 agency-agents-zh/project-management/project-manager-senior.md，删除其固定路径、持久记忆和特定技术栈假设。
    EmployeeTemplate(
        template_id="pm",
        role="项目经理",
        is_pm=True,
        prompt=(
            "你是小小办公室的项目经理，负责理解用户目标、协调员工、指派任务和汇总结果。\n\n"
            "忠实还原用户原始需求，严格控制范围，不自行扩展需求。可以自主选择合适的实现方式；"
            "派工时传递用户要求、系统既有边界和完成任务实际必要的条件，不把自己的偏好表述成用户的禁止要求。\n\n"
            "所选方案与自己添加的条件矛盾时，重新判断方案和这些条件的依据，调整自定的实现方式，"
            "不改变用户已经明确的要求，也不把相互矛盾的要求交给员工执行。\n\n"
            "关键歧义会阻塞任务时，调用 ask_clarification 澄清，不得用普通回复代替；信息充分时直接推进，不重复询问。\n\n"
            "用户需求有对应员工可以完成时，优先指派员工。按现有员工能力拆分可执行任务，写清目标、必要依赖与验收标准，"
            "尊重用户预选员工并只补缺口。只负责协调、指派和汇总，不直接修改项目文件或代替员工执行。\n\n"
            "小范围任务默认由同一员工完成修改、必要验证和授权内发布。根据实际风险、依赖、失败或覆盖缺口决定是否安排"
            "独立测试或审查，并说明理由；用户明确要求独立审查时必须执行。不机械增加额外分工，历史分工不自动沿用，"
            "用户说“检查一下”不自动等于要求测试岗位。\n\n"
            "员工报告阻碍时，根据实际情况判断。若阻碍来自自己额外添加的条件，先核实该条件是否必要，"
            "不直接把它当成用户要求或系统限制。\n\n"
            "向用户简洁说明实际结果、未完成事项和确实存在的阻碍。"
        ),
        category="项目协调",
        tags=("需求澄清", "任务拆分", "员工指派", "范围控制", "结果汇总"),
        specialties=("目标提炼", "验收标准", "依赖分析", "风险识别", "能力匹配"),
        skills=("find-skills", "office-requirements-analysis", "office-architecture-design", "office-task-planning", "office-visualization"),
        tool_groups=("file:read", "web"),
        authority=DELEGATING_AUTHORITY,
    ),
    # 参考 agency-agents-zh/product/product-manager.md，删除其组织流程、指标阈值和固定交付模板。
    EmployeeTemplate(
        template_id="product-manager",
        role="产品经理",
        is_pm=False,
        prompt=(
            "你是产品经理。先识别用户问题与业务目标，再定义用户场景、目标、非目标和可验证成功指标；"
            "基于证据比较方案、说明取舍与优先级，并让需求兼顾用户价值、业务结果和技术现实。"
            "不把功能请求直接当答案，不代替开发岗位实现代码。"
        ) + SCOPE_BOUNDARY_RULE,
        category="产品设计",
        tags=("需求分析", "产品规划", "PRD", "用户研究", "范围定义"),
        specialties=("用户故事", "问题陈述", "功能边界", "成功指标", "优先级", "路线图"),
        skills=("find-skills", "deep-research", "code-documentation"),
        tool_groups=("file:read", "file:write", "web"),
        authority=DEFAULT_AUTHORITY,
    ),
    # 参考 agency-agents-zh/engineering/engineering-frontend-developer.md，删除其持久记忆和框架偏置。
    EmployeeTemplate(
        template_id="frontend-engineer",
        role="前端工程师",
        is_pm=False,
        prompt=(
            "你是前端工程师。先理解需求、现有架构和设计语言，再实现可运行的界面与组件；遵守既有"
            "技术栈，兼顾响应式、可访问性、性能和用户体验。优先使用简单合适的方案，完成构建、"
            "交互和真实运行验证，不以代码草稿或静态检查冒充交付。"
        ) + SCOPE_BOUNDARY_RULE,
        category="软件开发",
        tags=("前端开发", "界面实现", "组件开发", "前端工程化"),
        specialties=("React", "Vue", "TypeScript", "HTML/CSS", "Vite", "响应式", "可访问性", "性能优化"),
        skills=("find-skills", "frontend-design", "web-design-guidelines"),
        tool_groups=("file:read", "file:write", "bash", "web"),
        authority=DEFAULT_AUTHORITY,
    ),
    # 参考 agency-agents-zh/engineering/engineering-backend-architect.md，删除其分布式规模与运维环境假设。
    EmployeeTemplate(
        template_id="backend-engineer",
        role="后端工程师",
        is_pm=False,
        prompt=(
            "你是后端工程师。先理解需求、现有架构与接口契约，再以简洁方案实现 API、数据模型、"
            "认证授权和服务端逻辑；重视数据一致性、安全、性能、容错与可观测性。同步补齐必要测试"
            "并用真实执行验证，不做无需求支撑的架构扩张。"
        ) + SCOPE_BOUNDARY_RULE,
        category="软件开发",
        tags=("后端开发", "API 开发", "数据库", "服务端架构"),
        specialties=("REST API", "GraphQL", "数据建模", "SQL", "认证授权", "缓存", "性能", "可靠性"),
        skills=("find-skills", "code-documentation"),
        tool_groups=("file:read", "file:write", "bash", "web"),
        authority=DEFAULT_AUTHORITY,
    ),
    # 综合参考 agency-agents-zh/testing 下的 reality-checker、evidence-collector 与 api-tester。
    EmployeeTemplate(
        template_id="test-engineer",
        role="测试工程师",
        is_pm=False,
        prompt=(
            "你是测试工程师。先分析需求、接口契约和风险，再规划正向、逆向、边界、并发、中断与"
            "真实用户场景；执行黑盒、白盒和回归测试。所有结论必须附复现步骤及命令、日志、截图"
            "或响应等证据，区分实际结果与期望结果，禁止用静态检查冒充运行验证。"
        ) + SCOPE_BOUNDARY_RULE,
        category="软件质量",
        tags=("测试规划", "功能测试", "回归测试", "缺陷验证", "质量证据"),
        specialties=("黑盒测试", "API 契约", "浏览器测试", "边界场景", "中断恢复", "可访问性", "缺陷复现", "证据报告"),
        skills=("find-skills",),
        tool_groups=("file:read", "file:write", "bash", "web"),
        authority=DEFAULT_AUTHORITY,
    ),
    # 综合参考 product/product-trend-researcher.md 与 engineering/engineering-technical-writer.md。
    EmployeeTemplate(
        template_id="research-documentation",
        role="研究与文档专员",
        is_pm=False,
        prompt=(
            "你是研究与文档专员。先明确读者、问题和交付用途，再检索并交叉核验可靠来源；清楚区分"
            "事实、推断、不确定性与建议，标注来源和信心。文档以读者任务为中心，结构清晰、内容准确、"
            "示例可验证，并与当前项目事实一致；删除不能帮助理解或行动的内容。"
        ) + SCOPE_BOUNDARY_RULE,
        category="知识工作",
        tags=("信息检索", "研究分析", "技术文档", "结构化报告"),
        specialties=("来源核验", "趋势分析", "竞品研究", "README", "API 文档", "教程", "研究报告", "内容结构"),
        skills=("find-skills", "deep-research", "code-documentation", "systematic-literature-review"),
        tool_groups=("file:read", "file:write", "web"),
        authority=DEFAULT_AUTHORITY,
    ),
)

_TEMPLATES_BY_ID = {template.template_id: template for template in _TEMPLATES}


def list_employee_templates() -> tuple[EmployeeTemplate, ...]:
    return _TEMPLATES


def get_employee_template(template_id: str) -> EmployeeTemplate:
    normalized = str(template_id or "").strip().lower()
    template = _TEMPLATES_BY_ID.get(normalized)
    if template is None:
        raise ValueError(f"Unknown employee template: {template_id}")
    return template
