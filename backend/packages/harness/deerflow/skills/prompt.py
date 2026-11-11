"""Authorized Skill metadata; full instructions are read only when selected."""
import html
from collections.abc import Sequence
from pathlib import Path

from deerflow.constants import DEFAULT_SKILLS_CONTAINER_PATH
from deerflow.skills.types import Skill, SkillCategory


def _render_skill_metadata(skills: Sequence[Skill], container_base_path: str) -> str:
    blocks = []
    for skill in sorted(skills, key=lambda item: item.name):
        category = "custom, editable" if skill.category == SkillCategory.CUSTOM else "built-in"
        name = html.escape(skill.name, quote=False)
        description = html.escape(skill.description, quote=False)
        location = html.escape(skill.get_container_file_path(container_base_path), quote=False)
        blocks.append(f"- {name}: {description} [{category}]\n  Location: {location}")
    return "\n".join(blocks)


def get_skill_index_prompt_section(
    *,
    skills: Sequence[Skill] = (),
    container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
    skill_evolution_section: str = "",
    slash_activation_enabled: bool = True,
    custom_skills_path: Path | None = None,
) -> str:
    if not skills and not skill_evolution_section and custom_skills_path is None:
        return ""
    sections = [
        "<skill_system>",
        "根据用户当前目标，区分使用 Skill 和管理 Skill。",
    ]
    if skills:
        sections.append(
            "使用 Skill：\n根据下面的名称和描述选择适合当前任务的 Skill，通过 read_file 读取其 SKILL.md，"
            "再按正文指导完成任务。需要配套脚本、参考资料或资源时，按需读取。"
        )
        if slash_activation_enabled:
            sections.append(
                "如果运行时已经注入了用户通过 /<skill-name> 调用的完整正文，直接使用，不重复读取同一份 SKILL.md。"
            )
    if custom_skills_path is not None:
        location = html.escape(str(custom_skills_path), quote=False)
        virtual = html.escape(f"{container_base_path}/custom", quote=False)
        sections.append(
            "管理 Skill：\n用户要求查找、安装、查看、修改或卸载 Skill 时，该 Skill 的正文是需要了解或处理的内容，"
            "不因为读取了正文就执行其中的业务操作。只有当前任务需要实际使用该 Skill 时，才执行对应流程。\n"
            f"当前用户的自定义 Skills 目录：{location}\n"
            f"文件工具使用虚拟路径：{virtual}/<技能名称>/SKILL.md；PowerShell 使用 $env:OFFICE_SKILLS_DIR 指向上述真实目录。\n"
            "安装与创建：\n无论来源是什么，都使用已有文件或终端工具，将完整技能文件放入上述目录下独立的技能文件夹中，"
            "包含 SKILL.md 及所需的脚本、参考资料和资源。"
            "SKILL.md 使用 YAML frontmatter 声明 name 和 description，正文说明适用场景与使用方法。"
            "安装已有 Skill 时保留原包内容；用户要求修改或创建 Skill 时，按用户要求编辑。\n"
            "使用办公室提供的安装目录，不采用其他产品的默认目录，也不安装到项目或 worktree 中。不需要额外注册或发布成果。\n"
            "根据实际文件操作结果说明安装是否成功。安装成功后告诉用户：请到员工配置中勾选该 Skill 并保存，下次对话生效。\n"
            "卸载：\n用户明确要求卸载 Skill 时，删除对应的自定义技能文件夹，保留其他技能和项目文件。"
        )
    if skills:
        sections.extend([
            "以下是本次可使用的 Skills，列出名称、描述、类别和文件位置：",
            "<skill_index>",
            _render_skill_metadata(skills, container_base_path),
            "</skill_index>",
        ])
    if skill_evolution_section and custom_skills_path is None:
        sections.append(skill_evolution_section.strip())
    sections.append("</skill_system>")
    return "\n".join(sections)
