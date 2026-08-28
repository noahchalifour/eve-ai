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
        # exactly the hang this fixes. _read_capped/_read_stderr_capped
        # compensate by capping how much they will ever buffer, and _reap
        # below lets any still-in-flight reader finish draining rather than
        # abandoning the stream mid-read.
        stdout_task = asyncio.ensure_future(_read_capped(proc.stdout, max_output_bytes))
        # Not _read_capped: stdout-over-cap immediately triggers _kill_group
        # below, which unblocks any pending write as a side effect of ending
        # the child, so stopping the read right at the cap is fine there.
        # stderr-over-cap triggers no kill - it is purely diagnostic - so if
        # this stopped reading at the cap too, a tool that wrote more than
        # _STDERR_CAP_BYTES to stderr (only reachable with the AST checker
        # bypassed) would leave the rest sitting unread in the pipe. Once the
        # kernel's pipe buffer filled, the child would block forever inside
        # its stderr write() call and never reach the return statement that
        # would have produced a correct result - a result the wall-clock
        # timeout below would then misreport to the caller as "time limit".
        stderr_task = asyncio.ensure_future(
            _read_stderr_capped(proc.stderr, _STDERR_CAP_BYTES)
        )

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


async def _read_stderr_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    """Like `_read_capped`, but never stops reading once the cap is crossed:
    it keeps consuming (and discarding, so memory stays bounded regardless of
    how much more arrives) everything else until the stream hits EOF, rather
    than returning as soon as `cap` bytes have been seen.

    stdout uses `_read_capped` instead, deliberately: stdout-over-cap
    immediately triggers `_kill_group` in `run_tool`, which unblocks any
    pending write as a side effect of ending the child, so there is nothing
    left to drain. stderr-over-cap triggers no kill - it exists purely to
    produce a diagnostic snippet - so if it stopped reading at the cap the
    same way, a tool that wrote more than `cap` bytes to stderr (only
    reachable with the AST checker bypassed, e.g. `sys.stderr.write('e' *
    500000)`) would leave the remainder sitting unread in the OS pipe buffer.
    Once that buffer filled, the child would block forever inside its
    stderr write() call - never reaching the return statement that would
    have produced a correct result - and `run_tool`'s wall-clock timeout
    would eventually fire and misreport that correct-but-unreached result as
    a timeout instead. Draining stderr to EOF here (concurrently with
    `run_tool` awaiting `stdout_task`, since this coroutine is scheduled as
    its own task) is what actually unblocks that write.
    """
    chunks: list[bytes] = []
    total = 0
    over = False
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        if not over:
            chunks.append(chunk)
            total += len(chunk)
            over = total > cap
    data = b"".join(chunks)
    if over:
        data = data[: cap + 1]
    return data, over


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

    # Mirrors what asyncio.subprocess.Process.communicate()'s own
    # _read_stream does after each stream hits EOF: explicitly close each
    # pipe's transport once its reader is done. Without this, nothing here
    # ever calls it, and (observed directly: this was missing in an earlier
    # version of this fix and produced exactly this symptom) a transport can
    # still be "connected" when the test event loop closes, so its __del__
    # later fires a PytestUnraisableExceptionWarning ("Event loop is closed")
    # instead of shutting down cleanly.
    #
    # Done BEFORE `proc.wait()` below, not after in a `finally` (that was an
    # earlier version of this fix and, on macOS specifically, it could stall
    # `proc.wait()` for the full grace period even though the child had
    # already been SIGKILLed - closing the pipe transports first is what
    # actually lets `proc.wait()` return promptly there). stdout_task is
    # always already done by the time `_reap` is called (every call site
    # awaits or cancels it first), so closing fd 1 here never truncates a
    # read still in progress; stderr_task may still be mid-drain (see
    # `_read_stderr_capped`), and closing fd 2 here simply delivers it an
    # EOF, which is the outcome it would have reached on its own once the
    # child actually exits.
    for fd in (1, 2):
        pipe = proc._transport.get_pipe_transport(fd)
        if pipe is not None:
            pipe.close()

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
