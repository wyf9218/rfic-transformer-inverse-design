"""Reserve a sleeping batch-continuation parent without signalling its children."""
import os
import signal
import time
import native_birth
import quiescent_handoff as q


def require(value, message):
    if not value:
        raise RuntimeError(message)


def same(a, b):
    return all(a[k] == b[k] for k in ('pid', 'start_ticks', 'uid', 'argv'))


def validate(view, identity, token, *, before):
    require(same(view['process'], identity), 'WAITING_PARENT_IDENTITY_CHANGED')
    require(view['token'] == token, 'WAITING_RELEASE_OR_EVENT_CHANGED')
    require(view['waiting'], 'NOT_AT_SUPPORTED_PARENT_WAIT')
    require(view['fds_safe'], 'PARENT_HAS_UNEXPECTED_FD_OR_PRODUCTION_LOCK')
    require(view['children_recognized'], 'UNRECOGNIZED_DIRECT_CHILD')
    if before:
        require(view['owner_alive'], 'OWNER_ALREADY_CLOSED_RETRY_QUIESCENT_PATH')
        require(view['wchan'] == 'hrtimer_nanosleep', 'PARENT_NOT_SLEEPING_IN_WAIT')
    require(view['process']['state'] != 'Z', 'PARENT_DEAD')


def reserve(identity, snapshot):
    before = snapshot()
    token = before['token']
    validate(before, identity, token, before=True)
    fd = os.pidfd_open(identity['pid'])
    stopped = False
    try:
        q.state(identity)
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        stopped = True
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            threads = q.thread_states(identity)
            if threads and all(v in ('T', 't') for v in threads.values()):
                break
            time.sleep(.01)
        else:
            raise RuntimeError('PARENT_NOT_ALL_THREADS_STOPPED')
        after = snapshot()
        validate(after, identity, token, before=False)
        return fd, dict(before=before, after=after, all_threads_stopped=threads,
                        child_signals=0, native_signals=0)
    except BaseException:
        if stopped:
            signal.pidfd_send_signal(fd, signal.SIGCONT)
        os.close(fd)
        raise
