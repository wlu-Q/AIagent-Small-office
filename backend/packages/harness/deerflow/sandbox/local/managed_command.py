"""Windows process-tree ownership shared by legacy commands and terminal sessions.

The bootstrap joins the kill-on-close job BEFORE starting user code. Closing the
Gateway (even abruptly) kills the job; descendants cannot outlive its owner.
Non-Office LocalSandbox command semantics are unchanged.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
import threading
from ctypes import wintypes

from deerflow.sandbox.env_policy import build_sandbox_env
from deerflow.sandbox.local.local_sandbox import DEFAULT_COMMAND_TIMEOUT_SECONDS, LocalSandbox, _validate_extra_env
from deerflow.sandbox.output import BoundedPipeCapture


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                ('active_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]


class _Limits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', ctypes.c_uint64 * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [('times', ctypes.c_int64 * 4), ('faults', wintypes.DWORD),
                ('total', wintypes.DWORD), ('active', wintypes.DWORD), ('terminated', wintypes.DWORD)]


_BOOTSTRAP = '''import ctypes, json, subprocess, sys
from ctypes import wintypes
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
k.OpenJobObjectW.restype = wintypes.HANDLE
k.GetCurrentProcess.restype = wintypes.HANDLE
k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
k.CloseHandle.argtypes = [wintypes.HANDLE]
job = k.OpenJobObjectW(1, False, sys.argv[1])
if not job or not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
    raise ctypes.WinError(ctypes.get_last_error())
k.CloseHandle(job)
sys.exit(subprocess.call(json.loads(sys.argv[2]), stdin=subprocess.DEVNULL))
'''


class ManagedProcess:
    """An owned Windows job; user code starts only after joining it."""

    def __init__(self, args, *, cwd, env, output_limit=256 * 1024, secret_values=(), tail_bytes=0):
        if os.name != 'nt':
            raise RuntimeError('Office terminal requires Windows Job Objects')
        self.kernel = kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.name = 'Local\\OfficeCommand-' + uuid.uuid4().hex
        self.job = kernel.CreateJobObjectW(None, self.name)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        self.process = None
        self.readers, self.captures = [], []
        try:
            limits = _Limits()
            limits.basic.flags = 0x2000
            if not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-c', _BOOTSTRAP, self.name, json.dumps(args)],
                cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW)
            for stream in (self.process.stdout, self.process.stderr):
                capture = BoundedPipeCapture(limit_bytes=output_limit, secret_values=secret_values, tail_bytes=tail_bytes)
                reader = threading.Thread(target=LocalSandbox._drain_pipe, args=(os.dup(stream.fileno()), capture), daemon=True)
                self.captures.append(capture)
                self.readers.append(reader)
                reader.start()
                stream.close()
        except BaseException:
            self.close()
            raise

    def done(self):
        account = _Accounting()
        if not self.kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(account), ctypes.sizeof(account), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return self.process.poll() is not None and account.active == 0

    def close(self):
        if not self.job:
            return
        # Kill-on-close also applies if explicit termination fails. Never kill an
        # arbitrary recovered PID: this live process handle/job is the authority.
        try:
            if not self.kernel.TerminateJobObject(self.job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.kill()  # bootstrap may not have joined yet
                self.process.wait(timeout=10)
            deadline = time.monotonic() + 10
            while self.process is not None and not self.done():
                if time.monotonic() >= deadline:
                    raise RuntimeError('Managed descendants have not exited')
                time.sleep(0.02)
        finally:
            self.kernel.CloseHandle(self.job)
            self.job = None
        for reader in self.readers:
            reader.join(timeout=5)
            if reader.is_alive():
                raise RuntimeError('Managed output reader did not stop')


def execute_managed_command(sandbox, command, *, env=None, timeout=None, abort_event=None):
    """Legacy synchronous caller; same job/pipe owner as background sessions."""
    _validate_extra_env(env)
    shell = sandbox._get_shell()
    command = sandbox._resolve_paths_in_command(command)
    args = [shell, '-NoProfile', '-Command', command] if sandbox._is_powershell(shell) else [shell, '/c' if sandbox._is_cmd_shell(shell) else '-c', command]
    environment = build_sandbox_env(env)
    if sandbox._is_msys_shell(shell):
        environment.update(MSYS_NO_PATHCONV='1', MSYS2_ARG_CONV_EXCL='*')
    owned = ManagedProcess(args, cwd=None, env=environment, output_limit=10 * 1024 * 1024, secret_values=(env or {}).values())
    reason = ''
    try:
        deadline = time.monotonic() + (timeout if timeout is not None else DEFAULT_COMMAND_TIMEOUT_SECONDS)
        while not owned.done():
            if abort_event is not None and abort_event.is_set():
                reason = 'Command cancelled'
                break
            if time.monotonic() >= deadline:
                reason = 'Command timed out'
                break
            time.sleep(0.02)
    finally:
        owned.close()
    output = '\n'.join(c.read() for c in owned.captures).strip()
    if reason:
        output += f'\n{reason}; all managed processes stopped.'
    elif owned.process.returncode:
        output += f'\nExit Code: {owned.process.returncode}'
    return output
