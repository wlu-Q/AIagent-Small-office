"""Supervise one POSIX command group until completion or owner pipe closure."""

import json
import os
import select
import signal
import subprocess
import sys


def group_exists(group_id):
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    return True


def supervise(args):
    process = None
    stopping = False
    completed = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if select.select([0], [], [], 0)[0] and not os.read(0, 1):
            return 1
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, start_new_session=True)
        while not stopping:
            returncode = process.poll()
            if returncode is not None and not group_exists(process.pid):
                completed = True
                return returncode
            if select.select([0], [], [], 0.05)[0] and not os.read(0, 1):
                break
    finally:
        if process is not None and not completed:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
    return process.returncode if process is not None else 1


if __name__ == '__main__':
    result = supervise(json.loads(sys.argv[1]))
    if result < 0:
        if -result not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-result, signal.SIG_DFL)
        os.kill(os.getpid(), -result)
    sys.exit(result)
