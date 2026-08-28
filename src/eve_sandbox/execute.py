"""One subprocess per call. No reuse, no warm pool.

No pool because process startup is milliseconds against a VOICE model call,
and a reused interpreter is state shared between two tools - the one thing a
sandbox tool does not get.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import tempfile

from eve_sandbox.settings import get_sandbox_settings

logger = logging.getLogger(__name__)

# Diagnostic-only, not part of the tool's declared output: capped small and
# fixed rather than sharing max_output_bytes, since it only exists to make a
# startup/import failure (Critical 1's ModuleNotFoundError, for example)
# distinguishable from a genuine resource-limit kill.
_STDERR_CAP_BYTES = 4096
_READ_CHUNK = 65536
# Grace window to reap the child after it has been sent SIGKILL. SIGKILL
# cannot be caught or blocked, so this should always finish in well under a
# second; it exists only so a kernel-level surprise cannot turn into an
# indefinite hang of the caller.
_REAP_GRACE_SECONDS = 5.0


async def run_tool(
    source: str,
    source_sha256: str,
    arguments: dict,
    *,
    timeout: int | None = None,
    memory_mb: int | None = None,
    max_output_bytes: int | None = None,
) -> dict:
    settings = get_sandbox_settings()
    timeout = timeout if timeout is not None else settings.timeout_seconds
    memory_mb = memory_mb if memory_mb is not None else settings.memory_mb
    max_output_bytes = (
        max_output_bytes if max_output_bytes is not None else settings.max_output_bytes
    )

    actual = hashlib.sha256(source.encode()).hexdigest()
    if actual != source_sha256:
        # The database and the caller disagree about approved bytes. A
        # tampering signal, not a bug to retry.
        logger.error("source hash mismatch: refusing to execute")
        return {"error": "source hash mismatch; refusing to execute"}

    job = json.dumps(
        {
            "source": source,
            "arguments": arguments,
            "memory_mb": memory_mb,
            "cpu_seconds": timeout,
        }
    )

    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = await asyncio.create_subprocess_exec(
                # -P: excludes the script's/cwd's directory from sys.path.
                # -s: excludes user site-packages. Together these preserve
                # the "no ambient path pollution" intent that -I (isolated
                # mode) used to provide, without -I's side effect of also
                # implying -E, which makes Python ignore PYTHONPATH - the one
                # env var this child legitimately needs, to find its own
                # eve_sandbox package. See runner.py's docstring.
                sys.executable, "-P", "-s", "-m", "eve_sandbox.runner",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                # Empty but for the import path the child needs to find
                # itself. No EVE_*, no PATH-derived credentials, nothing.
                env={"PYTHONPATH": _package_root()},
                start_new_session=True,
            )
        except Exception as exc:
            logger.warning("could not start the sandbox child", exc_info=True)
            return {"error": f"could not start the sandbox ({exc.__class__.__name__})"}

        try:
            proc.stdin.write(job.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # The child died (or refused to start) before we finished
            # writing. Whatever's on stdout/stderr below explains why.
            pass
        finally:
            proc.stdin.close()

        # Deliberately not communicate(): its docs warn it is the only safe
        # way to read a subprocess pipe precisely because a bare
        # `await stream.read()` can leave a stream "paused" once its
        # internal buffer passes StreamReader's flow-control limit, which
        # stalls the OS-level pipe and can block the child in write() -
        # exactly the hang this fixes. _read_capped compensates by capping
        # how much it will ever buffer and, on the kill paths below, by
        # continuing to drain whatever is left after the SIGKILL rather
        # than abandoning the stream mid-read.
        stdout_task = asyncio.ensure_future(_read_capped(proc.stdout, max_output_bytes))
        stderr_task = asyncio.ensure_future(_read_capped(proc.stderr, _STDERR_CAP_BYTES))

        try:
            stdout, stdout_over = await asyncio.wait_for(
                stdout_task, timeout=timeout + 1
            )
        except asyncio.TimeoutError:
            _kill_group(proc)
            await _reap(proc, stdout_task, stderr_task)
            return {"error": f"the tool exceeded its {timeout}s time limit"}

        if stdout_over:
            _kill_group(proc)
            await _reap(proc, stdout_task, stderr_task)
            return {
                "error": f"the tool returned more than {max_output_bytes} bytes"
            }

        # stdout reached EOF inside the cap, so the child is at or near
        # exit already; no kill needed, just reap it and collect stderr.
        stderr = await _reap(proc, stdout_task, stderr_task)

    if not stdout:
        if stderr:
            snippet = stderr[:_STDERR_CAP_BYTES].decode("utf-8", "replace")
            return {"error": f"the tool produced no output (stderr: {snippet!r})"}
        # Killed by RLIMIT_CPU or RLIMIT_AS before it could write anything -
        # not even to stderr. That silence is the signature: a real
        # startup/import failure (Critical 1's ModuleNotFoundError, for
        # example) always leaves a stderr trace, caught in the branch above.
        return {"error": "the tool was stopped by a resource limit"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": "the tool returned something that is not JSON"}


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    """Read from `stream` until EOF or until more than `cap` bytes have
    arrived, whichever comes first. Bounds memory against a streaming
    writer (unlike checking len() only after communicate() has already
    buffered everything) and returns at most `cap + 1` bytes, just enough to
    tell "exactly at the limit" from "over it" without buffering an
    attacker-controlled amount of output."""
    chunks: list[bytes] = []
    total = 0
    while total <= cap:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    data = b"".join(chunks)
    return data, len(data) > cap


async def _reap(
    proc: asyncio.subprocess.Process,
    stdout_task: asyncio.Task,
    stderr_task: asyncio.Task,
) -> bytes:
    """Wait for the child to actually exit, letting any still-in-flight
    reader task finish rather than cancelling it out from under the stream:
    a stream stalled by flow control (see the module docstring above) needs
    to be drained, not abandoned mid-read, or `proc.wait()` can end up
    waiting on a child that's already dead but whose exit the transport
    hasn't registered yet. Bounded by a grace period so this can never be
    the thing that hangs the caller, even if a kernel-level surprise means
    SIGKILL didn't immediately finish the child off.
    """

    async def _safe(task: asyncio.Task) -> bytes:
        try:
            data, _ = await task
            return data
        except (asyncio.CancelledError, Exception):
            return b""

    try:
        try:
            _, stderr, _ = await asyncio.wait_for(
                asyncio.gather(_safe(stdout_task), _safe(stderr_task), proc.wait()),
                timeout=_REAP_GRACE_SECONDS,
            )
            return stderr
        except asyncio.TimeoutError:
            logger.error(
                "sandbox child pid=%s did not exit within %ss of being killed",
                proc.pid, _REAP_GRACE_SECONDS,
            )
            return b""
    finally:
        # Mirrors what asyncio.subprocess.Process.communicate()'s own
        # _read_stream does after each stream hits EOF: explicitly close
        # each pipe's transport once its reader is done. Without this, nothing
        # here ever calls it, and (observed directly: this was missing in an
        # earlier version of this fix and produced exactly this symptom) a
        # transport can still be "connected" when the test event loop closes,
        # so its __del__ later fires a PytestUnraisableExceptionWarning
        # ("Event loop is closed") instead of shutting down cleanly.
        for fd in (1, 2):
            pipe = proc._transport.get_pipe_transport(fd)
            if pipe is not None:
                pipe.close()


def _package_root() -> str:
    """The directory containing the eve_sandbox package, so the child (run
    with -P -s, see run_tool above) can still import the runner via
    PYTHONPATH."""
    import eve_sandbox

    return os.path.dirname(os.path.dirname(os.path.abspath(eve_sandbox.__file__)))


def _kill_group(proc) -> None:
    """start_new_session puts the child in its own process group, so a tool
    that spawned anything is killed with it."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
