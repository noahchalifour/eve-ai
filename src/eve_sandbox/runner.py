"""The child process. Reads one job on stdin, writes one JSON line on stdout.

Limits are set here, by the child on itself, rather than through
subprocess(preexec_fn=...): asyncio.create_subprocess_exec does not support
preexec_fn, and a child that limits itself needs no unsafe hook.

Run as `python -P -s -m eve_sandbox.runner`: -P excludes the script's/cwd's
directory from sys.path, -s excludes user site-packages. NOT -I (isolated
mode): -I implies -E, which makes Python ignore PYTHONPATH entirely, and
PYTHONPATH is the one env var this child legitimately needs, to find its own
eve_sandbox package (execute.py sets it explicitly since the parent passes
no other environment). -I only "worked" in a dev checkout because the
editable `uv sync` install drops a .pth file into
.venv/lib/python3.12/site-packages that puts src/ on sys.path independent of
PYTHONPATH; the built image (`uv sync --frozen --no-dev --no-install-project`
in Dockerfile.eve-sandbox) has no such .pth, so -I there is a
ModuleNotFoundError on every call.
"""

from __future__ import annotations

import contextlib
import io
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
    # platform limitation. That known case is caught and skipped rather than
    # crashing the child, since the deployed pod is Linux, where this limit
    # does apply and is enforced. The except is scoped to Darwin only: on
    # any other platform a setrlimit(RLIMIT_AS) failure is a real
    # regression (a restrictive seccomp profile, a bad argument, a future
    # container runtime quirk) and must fail closed exactly like an
    # RLIMIT_CPU or RLIMIT_CORE failure does below - re-raised, not
    # swallowed, so the 256 MiB guarantee can't silently stop applying.
    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024)
        )
    except (ValueError, OSError):
        if sys.platform != "darwin":
            raise
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    # No core dumps: a dump of this process is the one artefact that could
    # persist tool data outside the call.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> None:
    job = json.loads(sys.stdin.read())
    _limit(int(job["memory_mb"]), int(job["cpu_seconds"]))

    namespace: dict = {}
    try:
        # A tool's own `print()` call is not a denied name the AST checker
        # rejects, but sys.stdout is also the pipe execute.py reads the JSON
        # payload back from below - anything the tool writes there would
        # otherwise corrupt that protocol. Redirected for the duration of
        # exec/run only, and restored (by the context manager, even on
        # exception) before the payload is written.
        with contextlib.redirect_stdout(io.StringIO()):
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
    except BaseException as exc:  # SystemExit, KeyboardInterrupt, etc. raised
        # by the tool's own code. Not how an RLIMIT_CPU/RLIMIT_AS kill shows
        # up: those deliver SIGXCPU/SIGKILL straight to the interpreter and
        # terminate it without unwinding through any except clause here -
        # execute.py's "no stdout" branch is what actually detects that.
        payload = json.dumps({"error": f"the tool was stopped ({exc.__class__.__name__})"})

    sys.stdout.write(payload)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
