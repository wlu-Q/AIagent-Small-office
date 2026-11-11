"""Group-scoped stop boundary over native Runs and the existing employee FIFO.

No agent loop: new messages remain usable; old queued work needs an explicit
continue command. Interrupted direct work is not silently replayed.
"""
from __future__ import annotations

import asyncio
import logging

from deerflow.office.native_work import pending_ask, work_identity
from deerflow.runtime.runs.manager import CancelOutcome

logger = logging.getLogger(__name__)


class GroupExecution:
    def __init__(self, pipeline, *, stop_timeout=5):
        self.pipeline = pipeline
        self.launch_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.paused = False
        self.epoch = 0
        self.stopping_ids: set[str] = set()
        self.error = ''
        self.stop_timeout = stop_timeout
        self.settlers: set[asyncio.Task] = set()

    async def _settle(self, tasks):
        try:
            await asyncio.wait(tasks)
            await self.state()
        except Exception:
            logger.exception('Failed to refresh group stop completion')

    async def shutdown(self):
        tasks = list(self.settlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def guard(self, start_run, queue_id, *, before_start=None):
        async def start(body, thread_id, request):
            async with self.launch_lock:
                if await self.pipeline.employee_run_gate.is_held(queue_id):
                    raise ValueError('本次群聊任务已暂停，尚未启动；需要执行时请重新发送')
                if before_start is not None:
                    await before_start()
                return await start_run(body, thread_id, request)
        return start

    async def records(self):
        p = self.pipeline
        records = []
        for employee in await p.employee_service.list_employees():
            records.extend(await p.run_view.run_manager.list_by_thread(
                p.employee_service.thread_id(employee['id'], 'group'), user_id=p.user_id, limit=None,
            ))
        return [r for r in records if r.user_id == p.user_id
                and (r.metadata or {}).get('office_conversation_kind') == 'group'
                and r.thread_id == p.employee_service.thread_id(r.assistant_id, 'group')]

    @staticmethod
    def active(record):
        return record.status in {'pending', 'running'} or getattr(record, 'finalizing', False) or getattr(record, 'completion_pending', False)

    async def state(self):
        records = await self.records()
        active = [r for r in records if self.active(r)]
        stopping = bool(self.stopping_ids.intersection(r.run_id for r in active))
        if not stopping and self.stopping_ids:
            self.stopping_ids.clear()
            await self.pipeline.employee_run_gate.finish_group_pause()
        held = [q.queue_id for q in await self.pipeline.employee_run_gate.list_pending()
                if await self.pipeline.employee_run_gate.is_held(q.queue_id)]
        binding = self.pipeline.project_service.get_binding() if self.pipeline.project_service else None
        project_id = binding.project_id if binding else None
        targets = []
        superseded = {(r.metadata or {}).get('office_resume_source_run_id') for r in records if r.status in {'pending', 'running', 'success'}}
        for record in records:
            work = work_identity(record)
            record_project = work.project_id if work else (record.metadata or {}).get('office_project_id')
            if record_project != project_id or record.run_id in superseded:
                continue
            if self.active(record) or await pending_ask(self.pipeline.run_launcher.checkpointer, record):
                targets.append({'employeeId': record.assistant_id, 'runId': record.run_id,
                                'projectId': record_project, 'workId': work.work_id if work else None})
        return {'activeCount': len(active), 'paused': self.paused or bool(held),
                'stopping': stopping, 'heldQueueIds': held, 'error': self.error, 'runTargets': targets}

    async def pause(self):
        async with self.control_lock:
            try:
                return await self._pause_locked()
            except BaseException:
                self.error = '群聊暂停未完成，请重试；已暂停的排队任务不会自动执行。'
                await self.pipeline.employee_run_gate.finish_group_pause()
                raise

    async def _pause_locked(self):
        p = self.pipeline
        self.epoch += 1
        await p.employee_run_gate.pause_group()  # Close FIFO admission first.
        self.paused = True
        self.error = ''
        # A launch that already crossed the boundary is included in records;
        # later launches fail the held-lease check, including resume requests.
        async with self.launch_lock:
            records = await self.records()
            for record in records:
                if await pending_ask(p.run_launcher.checkpointer, record):
                    await p.run_view.run_manager.update_metadata(record.run_id, {'office_control': 'paused'}, user_id=p.user_id)
            active = [r for r in records if self.active(r)]
            self.stopping_ids.update(r.run_id for r in active)
            for record in active:
                try:
                    await p.run_view.run_manager.update_metadata(record.run_id, {'office_control': 'paused'}, user_id=p.user_id)
                    await p.employee_run_gate.cancel_parent(record.run_id)
                    outcome = await p.run_view.run_manager.cancel(record.run_id, action='interrupt', user_id=p.user_id)
                    if outcome in {CancelOutcome.unknown, CancelOutcome.not_active_locally}:
                        raise RuntimeError('Run is not cancellable locally')
                except Exception:
                    # Continue stopping other employees, but never report full
                    # success or leave the UI spinning on an unsignalled Run.
                    self.stopping_ids.discard(record.run_id)
                    self.error = '部分群聊任务未能停止，请重试暂停并检查 Run 状态。'
                    logger.exception('Failed to stop group Run %s', record.run_id)
        tasks = [r.task for r in active if r.task is not None and not r.task.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.stop_timeout)
            if pending:
                # Complete the stop even if the user closes the window. This
                # observes native tasks; it does not execute or schedule Agents.
                settler = asyncio.create_task(self._settle(pending))
                self.settlers.add(settler)
                settler.add_done_callback(self.settlers.discard)
        if not self.stopping_ids:
            await p.employee_run_gate.finish_group_pause()
        return await self.state()


    async def resume(self, *, start_run=None, request=None):
        async with self.control_lock:
            if (await self.state())['stopping']:
                raise ValueError('群聊任务仍在停止，请稍后继续')
            if self.error:
                raise ValueError(self.error)
            if start_run is not None:
                await self.pipeline.queue_paused_continuations(start_run=start_run, request=request)
            for record in await self.records():
                if (record.metadata or {}).get('office_control') == 'paused' and (
                        await pending_ask(self.pipeline.run_launcher.checkpointer, record)
                        or not self.pipeline._employee(record.assistant_id).get('canDelegate')):
                    await self.pipeline.run_view.run_manager.update_metadata(record.run_id,
                        {'office_control': None}, user_id=self.pipeline.user_id)
            await self.pipeline.employee_run_gate.resume_group()
            self.paused = False
            return await self.state()
