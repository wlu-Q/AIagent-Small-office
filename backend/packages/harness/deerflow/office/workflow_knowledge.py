"""Read confirmed facts and evidence from legacy history without advancing it."""

from deerflow.office.project_knowledge import ProjectKnowledge


def workflow_knowledge_records(workflow):
    for fact in workflow.knowledge_facts:
        verified = fact.get("source_type") == "tool_verification"
        yield ProjectKnowledge(
            memory_id=f"{workflow.workflow_id}:{fact['revision']}:{fact['topic']}:{fact['source_run_id']}",
            user_id=workflow.user_id,
            project_id=workflow.project_id,
            category=fact["category"],
            topic=fact["topic"],
            statement=fact["statement"],
            constraints=tuple(fact["constraints"]),
            status="verified" if verified else "confirmed",
            source_workflow_id=workflow.workflow_id,
            source_run_id=fact["source_run_id"],
            source_type="tool_verification" if verified else "user_confirmation",
            updated_at=fact["confirmed_at"],
            source_message_id=fact.get("source_message_id"),
            native_task_id=fact.get("native_task_id"),
            evidence=tuple(fact.get("evidence", ())),
        )


class WorkflowKnowledgeSource:
    def __init__(self, workflows):
        self.workflows = workflows
        self.user_id = workflows.user_id

    def facts_for(self, project_id):
        for workflow in self.workflows.list(project_id=project_id, include_terminal=True):
            yield from workflow_knowledge_records(workflow)
