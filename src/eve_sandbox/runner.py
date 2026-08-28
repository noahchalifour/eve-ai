"""The child process. Reads one job on stdin, writes one JSON line on stdout.

Limits are set here, by the child on itself, rather than through
subprocess(preexec_fn=...): asyncio.create_subprocess_exec does not support
preexec_fn, and a child that limits itself needs no unsafe hook.

Run as `python -I -m eve_sandbox.runner`: -I is isolated mode, so no
PYTHONPATH, no user site-packages, and no current directory on sys.path.
"""

from __future__ import annotations

import json
import resource
import sys


def _limit(memory_mb: int, cpu_seconds: int) -> None:
    # Address space: catches an allocation blow-up. CPU: catches a busy loop
    # that the parent's wall clock would also catch, but sooner and from
    # inside, so the parent's kill is a backstop rather than the only bound.
    #
    # RLIMIT_AS is not settable on Darwin (macOS): the kernel rejects any
    # value, on every process, unconditionally - not a config error, a
    # platform limitation. Caught and skipped rather than crashing the
    # child, since the deployed pod is Linux, where this limit does apply
    # and is enforced; RLIMIT_CPU below is a real backstop on every
    # platform, including this one.
    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024)
        )
    except (ValueError, OSError):
        pass
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    # No core dumps: a dump of this process is the one artefact that could
    # persist tool data outside the call.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> None:
    job = json.loads(sys.stdin.read())
    _limit(int(job["memory_mb"]), int(job["cpu_seconds"]))

    namespace: dict = {}
    try:
        exec(compile(job["source"], "<tool>", "exec"), namespace)  # noqa: S102
        run = namespace.get("run")
        if run is None:
            raise RuntimeError("the source defines no 'run' function")
        result = run(job["arguments"])
        payload = json.dumps({"result": result})
    except MemoryError:
        payload = json.dumps({"error": "the tool exceeded its memory limit"})
    except Exception as exc:  # noqa: BLE001
        payload = json.dumps({"error": f"{exc.__class__.__name__}: {exc}"})
    except BaseException as exc:  # SystemExit, KeyboardInterrupt from rlimits
        payload = json.dumps({"error": f"the tool was stopped ({exc.__class__.__name__})"})

    sys.stdout.write(payload)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
