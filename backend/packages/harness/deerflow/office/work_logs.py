"""Employee daily logs projected from finalized Office runs and delegation receipts."""
import asyncio
import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from deerflow.office.delegation_memory import delegation_observations
from deerflow.office.execution_facts import ExecutionReceiptStore
from deerflow.utils.messages import public_ai_text


_HEADER_KEYS = ('office_conversation_kind', 'office_work_id', 'office_project_id', 'office_runtime_finished_at',
                'office_resume_source_run_id', 'office_control')


def _day(timestamp):
    return datetime.fromisoformat(timestamp).astimezone().date()


def _receipt_time(row):
    return datetime.fromtimestamp(row['updated_at'] / 1000, UTC)


def _step(text, status=None):
    return {'text': text, 'error': status == 'error'}


class EmployeeWorkLogs:
    def __init__(self, employees, runs, events, employee_id):
        employee = employees.get_employee_summary(employee_id)
        self.employee_id = employee['id']
        self.user_id = employees.user_id
        self.paths = employees.paths
        self.runs = runs
        self.events = events
        kinds = ('group',) if employee['templateId'] == 'pm' else ('group', 'private')
        self.threads = {employees.thread_id(self.employee_id, kind): kind for kind in kinds}

    async def _finished_runs(self):
        headers = await self.runs.list_assistant_run_headers(tuple(self.threads), self.employee_id,
                                                            metadata_keys=_HEADER_KEYS, user_id=self.user_id)
        groups = defaultdict(list)
        for row in headers:
            if self.threads[row['thread_id']] == row['office_conversation_kind']:
                work = row['office_work_id'] or f"run:{row['run_id']}"
                groups[row['thread_id'], row['office_project_id'], work].append(row)
        finished = []
        for attempts in groups.values():
            by_id = {row['run_id']: row for row in attempts}
            superseded = {row['office_resume_source_run_id'] for row in attempts}
            leaves = [row for row in attempts if row['run_id'] not in superseded]
            if not leaves:
                continue
            last = max(leaves, key=lambda row: (row['created_at'], row['run_id']))
            cancelled = last['status'] == 'interrupted' and last['office_control'] == 'cancelled'
            if not cancelled and last['status'] not in ('success', 'error', 'timeout'):
                continue
            end = last['office_runtime_finished_at']
            if not end and not last['events_finalized']:
                continue
            recorded = last['updated_at'] if cancelled or not end else end
            chain = [last]
            while source := by_id.pop(chain[-1]['office_resume_source_run_id'], None):
                chain.append(source)
            finished.append((recorded, chain[::-1]))
        return finished

    async def _receipts(self, day=None):
        return await asyncio.to_thread(ExecutionReceiptStore.work_log_receipts, self.paths, self.user_id,
                                       self.employee_id, day)

    async def days(self, before, limit):
        runs, receipts = await asyncio.gather(self._finished_runs(), self._receipts())
        counts = Counter(_day(recorded) for recorded, _ in runs)
        counts.update({date.fromisoformat(row['day']): row['count'] for row in receipts})
        dates = sorted((day for day in counts if before is None or day < before), reverse=True)
        return {'days': [{'date': day.isoformat(), 'count': counts[day]} for day in dates[:limit]],
                'nextBefore': dates[limit - 1].isoformat() if len(dates) > limit else None}

    async def _events(self, thread_id, run_id, types, task_id=None):
        after = 0
        while True:
            page = await self.events.list_events(thread_id, run_id, event_types=types, task_id=task_id,
                                                user_id=self.user_id, after_seq=after, limit=200)
            for event in page:
                yield event
            if len(page) < 200:
                return
            after = page[-1]['seq']

    async def _direct(self, recorded, chain):
        first, last = chain[0], chain[-1]
        thread = last['thread_id']
        records = await self.runs.get_many_by_thread(thread, {first['run_id'], last['run_id']}, user_id=self.user_id)
        if not all(row['run_id'] in records for row in (first, last)):
            return None
        result = records[last['run_id']]
        title = None if first['office_resume_source_run_id'] else records[first['run_id']]['metadata'].get('office_task_summary')
        steps, started = [], None
        for row in chain:
            async for event in self._events(thread, row['run_id'], ['run.start', 'llm.ai.response', 'llm.tool.result']):
                if event['event_type'] == 'run.start':
                    if row is first:
                        started = event['created_at']
                elif event['metadata'].get('caller') == 'lead_agent':
                    body = event['content']
                    if not isinstance(body, dict):
                        continue
                    if event['event_type'] == 'llm.tool.result' and body.get('name'):
                        steps.append(_step(body['name'], body.get('status')))
                    elif body.get('tool_calls') and (text := public_ai_text(body)):
                        steps.append(_step(text))
        return {'id': first['run_id'], 'source': self.threads[thread], 'task': title,
                'startedAt': started, 'endedAt': last['office_runtime_finished_at'], 'recordedAt': recorded,
                'steps': steps, 'result': result.get('last_ai_message'),
                'issue': result.get('error'), 'outcome': last['status']}

    async def _delegated(self, row):
        result = json.loads(row['result_ref_json']) if row['result_ref_json'] else {}
        title = row['task']
        exception = result.get('stop_reason')
        started = ended = None
        steps = []
        async for event in self._events(row['thread_id'], row['parent_run_id'],
                                        ['subagent.start', 'subagent.step', 'subagent.end'], row['native_task_id']):
            if event['metadata'].get('employee_id') != self.employee_id:
                continue
            body = event['content']
            if event['event_type'] == 'subagent.start':
                started = event['created_at']
                if not title:
                    title = body.get('description')
            elif event['event_type'] == 'subagent.end':
                ended = event['created_at']
                if body.get('error'):
                    exception = body['error']
            elif body.get('kind') == 'tool' and body.get('tool_name'):
                steps.append(_step(body['tool_name'], body.get('status')))
            elif body.get('kind') == 'ai' and body.get('tool_calls') and body.get('public_text'):
                steps.append(_step(body['public_text']))
        if not steps:
            steps = [_step(item['tool'], item['status']) for item in
                     delegation_observations(json.loads(row['evidence_json'])) if item['tool']]
        report = result.get('report')
        return {'id': row['receipt_key'], 'source': 'delegation', 'task': title,
                'startedAt': started, 'endedAt': ended, 'recordedAt': _receipt_time(row).isoformat(),
                'steps': steps, 'result': report if report != exception else None, 'issue': exception, 'outcome': row['status']}

    async def day(self, day):
        runs, receipts = await asyncio.gather(self._finished_runs(), self._receipts(day))
        tasks = []
        for recorded, chain in runs:
            if _day(recorded) == day and (entry := await self._direct(recorded, chain)):
                tasks.append(entry)
        for row in receipts:
            tasks.append(await self._delegated(row))
        tasks.sort(key=lambda item: (datetime.fromisoformat(item['recordedAt']), item['id']))
        return {'date': day.isoformat(), 'tasks': tasks}
