"""
Windows-specific functionality for stdio client operations.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO, TextIO, cast

from anyio import to_thread
from anyio.streams.file import FileReadStream, FileWriteStream


def get_windows_executable_command(command: str) -> str:
    """
    Get the correct executable command normalized for Windows.

    On Windows, commands might exist with specific extensions (.exe, .cmd, etc.)
    that need to be located for proper execution.

    Args:
        command: Base command (e.g., 'uvx', 'npx')

    Returns:
        str: Windows-appropriate command path
    """
    try:
        # First check if command exists in PATH as-is
        if command_path := shutil.which(command):
            return command_path

        # Check for Windows-specific extensions
        for ext in [".cmd", ".bat", ".exe", ".ps1"]:
            ext_version = f"{command}{ext}"
            if ext_path := shutil.which(ext_version):
                return ext_path

        # For regular commands or if we couldn't find special versions
        return command
    except OSError:
        # Handle file system errors during path resolution
        # (permissions, broken symlinks, etc.)
        return command


class FallbackProcess:
    """
    A fallback process wrapper for Windows to handle async I/O
    when using subprocess.Popen, which provides sync-only FileIO objects.

    This wraps stdin and stdout into async-compatible
    streams (FileReadStream, FileWriteStream),
    so that MCP clients expecting async streams can work properly.
    """

    def __init__(self, popen_obj: subprocess.Popen[bytes]):
        self.popen: subprocess.Popen[bytes] = popen_obj
        self.stdin_raw = popen_obj.stdin  # type: ignore[assignment]
        self.stdout_raw = popen_obj.stdout  # type: ignore[assignment]
        self.stderr = popen_obj.stderr  # type: ignore[assignment]

        self.stdin = FileWriteStream(cast(BinaryIO, self.stdin_raw)) if self.stdin_raw else None
        self.stdout = FileReadStream(cast(BinaryIO, self.stdout_raw)) if self.stdout_raw else None

    async def __aenter__(self):
        """Support async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: BaseException | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Terminate and wait on process exit inside a thread."""
        self.popen.terminate()
        await to_thread.run_sync(self.popen.wait)

        # Close the file handles to prevent ResourceWarning
        if self.stdin:
            await self.stdin.aclose()
        if self.stdout:
            await self.stdout.aclose()
        if self.stdin_raw:
            self.stdin_raw.close()
        if self.stdout_raw:
            self.stdout_raw.close()
        if self.stderr:
            self.stderr.close()

    async def wait(self):
        """Async wait for process completion."""
        return await to_thread.run_sync(self.popen.wait)

    def terminate(self):
        """Terminate the subprocess immediately."""
        return self.popen.terminate()

    def kill(self) -> None:
        """Kill the subprocess immediately (alias for terminate)."""
        self.terminate()


# ------------------------
# Updated function
# ------------------------


async def create_windows_process(
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    errlog: TextIO | None = sys.stderr,
    cwd: Path | str | None = None,
) -> FallbackProcess:
    """
    Creates a subprocess in a Windows-compatible way.

    On Windows, asyncio.create_subprocess_exec has incomplete support
    (NotImplementedError when trying to open subprocesses).
    Therefore, we fallback to subprocess.Popen and wrap it for async usage.

    Args:
        command (str): The executable to run
        args (list[str]): List of command line arguments
        env (dict[str, str] | None): Environment variables
        errlog (TextIO | None): Where to send stderr output (defaults to sys.stderr)
        cwd (Path | str | None): Working directory for the subprocess

    Returns:
        FallbackProcess: Async-compatible subprocess with stdin and stdout streams
    """
    try:
        # Try launching with creationflags to avoid opening a new console window
        popen_obj = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errlog,
            env=env,
            cwd=cwd,
            bufsize=0,  # Unbuffered output
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return FallbackProcess(popen_obj)

    except Exception:
        # If creationflags failed, fallback without them
        popen_obj = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errlog,
            env=env,
            cwd=cwd,
            bufsize=0,
        )

        return FallbackProcess(popen_obj)
