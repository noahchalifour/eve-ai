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
    timeout = timeout or settings.timeout_seconds
    memory_mb = memory_mb or settings.memory_mb
    max_output_bytes = max_output_bytes or settings.max_output_bytes

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
                sys.executable, "-I", "-m", "eve_sandbox.runner",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
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
            stdout, _ = await asyncio.wait_for(
                proc.communicate(job.encode()), timeout=timeout + 1
            )
        except asyncio.TimeoutError:
            _kill_group(proc)
            await proc.wait()
            return {"error": f"the tool exceeded its {timeout}s time limit"}

    if len(stdout) > max_output_bytes:
        return {
            "error": f"the tool returned more than {max_output_bytes} bytes"
        }
    if not stdout:
        # Killed by RLIMIT_CPU or RLIMIT_AS before it could write.
        return {"error": "the tool was stopped by a resource limit"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": "the tool returned something that is not JSON"}


def _package_root() -> str:
    """The directory containing the eve_sandbox package, so `-I` mode can
    still import the runner."""
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
