"""Strict engineering tool registry, policy engine, command runners and audit service."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.models.engineering_workspace import ToolExecution
from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.schemas.engineering_workspace import (
    CommandInput,
    DeleteFileInput,
    FileCreateInput,
    FilePatchInput,
    FilePathInput,
    FileRenameInput,
    GitInput,
    SearchContentInput,
    SearchFilesInput,
    StructuredToolResult,
)
from app.services.engineering_workspace_service import (
    FileOperationResult,
    FileSystemTool,
    SearchTool,
    WorkspaceError,
    WorkspacePathResolver,
)
from app.services.engineering_workspace_storage import (
    LocalDurableWorkspaceStorage,
    WorkspaceStorageBackend,
    WorkspaceStorageError,
    build_workspace_storage,
    capture_workspace,
    reconcile_workspace,
)
from app.services.runtime_environment import is_production


class ToolExecutionError(Exception):
    def __init__(
        self,
        code: str,
        *,
        status: str = "FAILED",
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        result: FileOperationResult | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.status = status
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.result = result


class DirectoryCreateInput(FilePathInput):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel, "ToolRuntime"], Awaitable[FileOperationResult]]


@dataclass
class ToolRuntime:
    execution_id: str
    workspace_id: str
    owner_id: str
    root_path: str
    repository: EngineeringWorkspaceRepository


@dataclass(frozen=True)
class PolicyDecision:
    classification: str
    permitted: bool
    approval_required: bool = False
    network_allowed: bool = False
    reason: str | None = None


class ToolPolicyEngine:
    """Deterministic allowlist. Unknown command shapes fail closed."""

    FORBIDDEN_EXECUTABLES = {
        "bash",
        "sh",
        "zsh",
        "fish",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "sudo",
        "su",
        "runas",
        "reg",
        "reg.exe",
        "sc",
        "sc.exe",
        "net",
        "net.exe",
        "taskkill",
        "kill",
        "rm",
        "rmdir",
        "del",
        "format",
        "diskpart",
        "shutdown",
        "reboot",
        "mount",
        "umount",
        "curl",
        "wget",
    }
    READ_ONLY = {"pwd", "ls", "rg"}
    PYTHON_NAMES = {"python", "python3", "python.exe", "py"}
    NODE_NAMES = {"node", "node.exe"}
    NPM_NAMES = {"npm", "npm.cmd"}
    TEST_SCRIPTS = {"test", "lint", "build", "typecheck", "type-check", "format", "check"}

    @staticmethod
    def _unsafe_path_argument(value: str) -> bool:
        candidate = value.split("=", 1)[-1] if value.startswith("-") and "=" in value else value
        path = Path(candidate.replace("\\", "/"))
        windows = PureWindowsPath(candidate)
        return path.is_absolute() or windows.is_absolute() or bool(windows.drive) or ".." in path.parts

    def classify(self, command: CommandInput) -> PolicyDecision:
        raw_executable = command.argv[0]
        if Path(raw_executable).name != raw_executable or PureWindowsPath(raw_executable).name != raw_executable:
            return PolicyDecision("FORBIDDEN", False, reason="EXECUTABLE_PATH_REJECTED")
        executable = raw_executable.casefold()
        args = command.argv[1:]
        if any(self._unsafe_path_argument(argument) for argument in args):
            return PolicyDecision("FORBIDDEN", False, reason="COMMAND_PATH_REJECTED")
        if executable in self.FORBIDDEN_EXECUTABLES:
            return PolicyDecision("FORBIDDEN", False, reason="FORBIDDEN_EXECUTABLE")
        if any(token in {"&&", "||", ";", "|", ">", ">>", "<"} for token in command.argv):
            return PolicyDecision("FORBIDDEN", False, reason="SHELL_OPERATOR_REJECTED")
        if executable in self.READ_ONLY:
            if executable == "pwd" and args:
                return PolicyDecision("FORBIDDEN", False, reason="PWD_ARGUMENT_REJECTED")
            if executable == "ls" and (len(args) > 1 or (args and args[0].startswith("-"))):
                return PolicyDecision("FORBIDDEN", False, reason="LS_ARGUMENT_REJECTED")
            if executable == "rg" and any(
                arg in {"--pre", "--pre-glob", "--search-zip", "-L", "--follow"} for arg in args
            ):
                return PolicyDecision("FORBIDDEN", False, reason="RG_EXECUTION_OR_SYMLINK_OPTION_REJECTED")
            return PolicyDecision("READ_ONLY", True)
        if executable == "git" or executable == "git.exe":
            return self._git(args, command)
        if executable in self.PYTHON_NAMES:
            return self._python(args, command)
        if executable in self.NODE_NAMES:
            return self._node(args)
        if executable in self.NPM_NAMES:
            return self._npm(args, command)
        if executable in {"pytest", "pytest.exe", "ruff", "ruff.exe", "mypy", "mypy.exe"}:
            return PolicyDecision("PROJECT_VERIFICATION", True)
        return PolicyDecision("FORBIDDEN", False, reason="EXECUTABLE_NOT_ALLOWLISTED")

    def _git(self, args: list[str], command: CommandInput) -> PolicyDecision:
        if not args:
            return PolicyDecision("FORBIDDEN", False, reason="GIT_SUBCOMMAND_REQUIRED")
        if any(arg == "-C" or arg.startswith("--git-dir") or arg.startswith("--work-tree") for arg in args):
            return PolicyDecision("FORBIDDEN", False, reason="GIT_PATH_OVERRIDE_REJECTED")
        subcommand = args[0].casefold()
        if subcommand in {"status", "diff", "branch", "rev-parse", "log", "show"}:
            return PolicyDecision("READ_ONLY", True)
        if subcommand in {"init", "add", "switch", "checkout"}:
            if subcommand == "checkout" and not any(arg in {"-b", "--orphan"} for arg in args[1:]):
                return PolicyDecision("FORBIDDEN", False, reason="GIT_CHECKOUT_SCOPE_REJECTED")
            return PolicyDecision("WORKSPACE_WRITE", True)
        if subcommand == "commit":
            return PolicyDecision(
                "LOCAL_COMMIT",
                command.approval_granted,
                True,
                reason=None if command.approval_granted else "EXPLICIT_APPROVAL_REQUIRED",
            )
        return PolicyDecision("FORBIDDEN", False, reason="GIT_SUBCOMMAND_REJECTED")

    def _python(self, args: list[str], command: CommandInput) -> PolicyDecision:
        if not args or args in (["--version"], ["-V"]):
            return PolicyDecision("READ_ONLY", True)
        if args[0] in {"-c", "-"}:
            return PolicyDecision("FORBIDDEN", False, reason="INLINE_CODE_REJECTED")
        if args[0] == "-m":
            if len(args) < 2:
                return PolicyDecision("FORBIDDEN", False, reason="PYTHON_MODULE_REQUIRED")
            module = args[1].casefold()
            if module in {"pytest", "unittest", "compileall", "ruff", "mypy"}:
                return PolicyDecision("PROJECT_VERIFICATION", True)
            if module == "pip" and len(args) >= 3 and args[2].casefold() in {"install", "download", "wheel"}:
                approved = command.approval_granted and command.allow_network
                return PolicyDecision(
                    "DEPENDENCY_INSTALL", approved, True, True, None if approved else "NETWORK_APPROVAL_REQUIRED"
                )
            return PolicyDecision("FORBIDDEN", False, reason="PYTHON_MODULE_REJECTED")
        if args[0].startswith("-") or Path(args[0]).is_absolute() or ".." in Path(args[0]).parts:
            return PolicyDecision("FORBIDDEN", False, reason="PYTHON_SCRIPT_PATH_REJECTED")
        return PolicyDecision("PROJECT_EXECUTION", True)

    def _node(self, args: list[str]) -> PolicyDecision:
        if not args or args in (["--version"], ["-v"]):
            return PolicyDecision("READ_ONLY", True)
        if args[0] in {"-e", "--eval", "-p", "--print"} or Path(args[0]).is_absolute() or ".." in Path(args[0]).parts:
            return PolicyDecision("FORBIDDEN", False, reason="NODE_ARGUMENT_REJECTED")
        return PolicyDecision("PROJECT_EXECUTION", True)

    def _npm(self, args: list[str], command: CommandInput) -> PolicyDecision:
        if not args or args in (["--version"], ["-v"]):
            return PolicyDecision("READ_ONLY", True)
        subcommand = args[0].casefold()
        if any(arg == "--prefix" or arg.startswith("--prefix=") for arg in args):
            return PolicyDecision("FORBIDDEN", False, reason="NPM_PATH_OVERRIDE_REJECTED")
        if subcommand in {"test", "run"}:
            script = "test" if subcommand == "test" else (args[1].casefold() if len(args) > 1 else "")
            if script in self.TEST_SCRIPTS:
                return PolicyDecision("PROJECT_VERIFICATION", True)
        if subcommand in {"install", "ci"}:
            approved = command.approval_granted and command.allow_network
            return PolicyDecision(
                "DEPENDENCY_INSTALL", approved, True, True, None if approved else "NETWORK_APPROVAL_REQUIRED"
            )
        return PolicyDecision("FORBIDDEN", False, reason="NPM_COMMAND_REJECTED")


class GitTool:
    """Builds only the bounded local Git operations exposed by Engineering mode."""

    @staticmethod
    def command(request: GitInput) -> CommandInput:
        if any(
            Path(item).is_absolute()
            or PureWindowsPath(item).is_absolute()
            or PureWindowsPath(item).drive
            or ".." in Path(item.replace("\\", "/")).parts
            for item in request.files
        ):
            raise ToolExecutionError("GIT_FILE_PATH_REJECTED")
        if request.action == "init":
            argv = ["git", "init"]
        elif request.action == "status":
            argv = ["git", "status", "--short"]
        elif request.action == "diff":
            argv = ["git", "diff", "--"] + request.files
        elif request.action == "current_branch":
            argv = ["git", "branch", "--show-current"]
        elif request.action == "create_branch":
            if (
                not request.branch
                or not re.fullmatch(r"[A-Za-z0-9._/-]{1,160}", request.branch)
                or ".." in request.branch
            ):
                raise ToolExecutionError("GIT_BRANCH_INVALID")
            argv = ["git", "switch", "-c", request.branch]
        elif request.action == "stage":
            if not request.files:
                raise ToolExecutionError("GIT_FILES_REQUIRED")
            argv = ["git", "add", "--"] + request.files
        else:
            if not request.message:
                raise ToolExecutionError("GIT_COMMIT_MESSAGE_REQUIRED")
            argv = ["git", "commit", "-m", request.message]
        return CommandInput(argv=argv, approval_granted=request.approval_granted)


class SecretRedactor:
    PATTERNS = (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
        re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+"),
    )

    def __init__(self):
        self.secret_values = [
            value
            for key, value in os.environ.items()
            if value
            and len(value) >= 8
            and (
                any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                or key.upper() == "DATABASE_URL"
                or key.upper().endswith("_DATABASE_URL")
            )
        ]

    def redact(self, value: str) -> str:
        result = value
        for secret in self.secret_values:
            result = result.replace(secret, "[REDACTED]")
        for pattern in self.PATTERNS:
            result = pattern.sub(r"\1[REDACTED]", result)
        return result


class CommandRunner:
    async def run(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        raise NotImplementedError

    async def cancel(self, execution_id: str) -> bool:
        raise NotImplementedError


class UnavailableCommandRunner(CommandRunner):
    """Keeps the API healthy while failing every execution closed."""

    def __init__(self, reason: str):
        self.reason = reason

    async def run(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        raise ToolExecutionError(self.reason)

    async def cancel(self, execution_id: str) -> bool:
        return False


class LocalDevelopmentCommandRunner(CommandRunner):
    """Owner-only development fallback; never a production multi-tenant sandbox."""

    def __init__(self, *, allow_production: bool = False):
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._lock = asyncio.Lock()
        self._execution_slots = asyncio.Semaphore(
            max(1, int(os.getenv("NIE_ENGINEERING_MAX_CONCURRENT_EXECUTIONS", "2")))
        )
        self._redactor = SecretRedactor()
        self._allow_production = allow_production

    @staticmethod
    async def _read_bounded(stream: asyncio.StreamReader, maximum: int) -> tuple[bytes, bool]:
        output = bytearray()
        truncated = False
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            remaining = maximum - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        return bytes(output), truncated

    def _environment(self, root: Path) -> dict[str, str]:
        temp = root / ".nie-tmp"
        temp.mkdir(exist_ok=True)
        allowed = {}
        for name in ("PATH", "PATHEXT", "SystemRoot", "WINDIR", "COMSPEC"):
            value = os.environ.get(name)
            if value:
                allowed[name] = value
        allowed.update(
            {
                "HOME": str(root),
                "USERPROFILE": str(root),
                "TMP": str(temp),
                "TEMP": str(temp),
                "PYTHONDONTWRITEBYTECODE": "1",
                "CI": "1",
            }
        )
        return allowed

    @staticmethod
    def _validate_existing_path_arguments(root: Path, cwd: Path, arguments: list[str]) -> None:
        """Reject an existing symlink/path argument that resolves outside the workspace."""
        for argument in arguments:
            if not argument or argument.startswith("-"):
                continue
            candidate = cwd / argument
            if not candidate.exists() and not candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if resolved != root and root not in resolved.parents:
                raise ToolExecutionError("COMMAND_PATH_ESCAPE_REJECTED")

    async def run(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        async with self._execution_slots:
            return await self._run_confined(execution_id, root_path, command, policy)

    async def _run_confined(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        if is_production() and not self._allow_production:
            raise ToolExecutionError("LOCAL_RUNNER_DISABLED_IN_PRODUCTION")
        root = Path(root_path).resolve()
        cwd = (root / command.cwd).resolve()
        if cwd != root and root not in cwd.parents:
            raise ToolExecutionError("COMMAND_CWD_ESCAPE_REJECTED")
        if not cwd.is_dir():
            raise ToolExecutionError("COMMAND_CWD_NOT_FOUND")
        executable = Path(command.argv[0]).name.casefold()
        self._validate_existing_path_arguments(root, cwd, command.argv[1:])
        if executable == "pwd":
            result = FileOperationResult({"classification": policy.classification})
            result.stdout = "/workspace\n"
            result.stderr = ""
            result.exit_code = 0
            return result
        if executable == "ls":
            list_target = (cwd / command.argv[1]).resolve() if len(command.argv) == 2 else cwd
            if list_target != root and root not in list_target.parents:
                raise ToolExecutionError("COMMAND_PATH_ESCAPE_REJECTED")
            if not list_target.is_dir():
                raise ToolExecutionError("COMMAND_PATH_NOT_FOUND")
            entries = "\n".join(sorted(item.name for item in list_target.iterdir()))
            result = FileOperationResult({"classification": policy.classification})
            result.stdout = (entries + "\n") if entries else ""
            result.stderr = ""
            result.exit_code = 0
            return result
        argv = list(command.argv)
        if executable in ToolPolicyEngine.PYTHON_NAMES:
            argv[0] = sys.executable
        else:
            resolved = shutil.which(argv[0], path=os.environ.get("PATH"))
            if not resolved:
                raise ToolExecutionError("COMMAND_NOT_AVAILABLE")
            argv[0] = resolved
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=self._environment(root),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ToolExecutionError("COMMAND_START_FAILED") from exc
        async with self._lock:
            self._processes[execution_id] = process
            cancellation_pending = execution_id in self._cancelled
        if cancellation_pending:
            process.terminate()
        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise ToolExecutionError("COMMAND_OUTPUT_CAPTURE_UNAVAILABLE")
        stdout_task = asyncio.create_task(self._read_bounded(process.stdout, command.max_output_bytes))
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr, command.max_output_bytes))
        try:
            await asyncio.wait_for(process.wait(), timeout=command.timeout_seconds)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
            raise ToolExecutionError(
                "COMMAND_TIMEOUT",
                status="TIMED_OUT",
                exit_code=process.returncode,
                stdout=self._decode(stdout, stdout_truncated),
                stderr=self._decode(stderr, stderr_truncated),
            ) from exc
        finally:
            async with self._lock:
                self._processes.pop(execution_id, None)
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        async with self._lock:
            was_cancelled = execution_id in self._cancelled
            self._cancelled.discard(execution_id)
        if was_cancelled:
            raise ToolExecutionError(
                "COMMAND_CANCELLED",
                status="CANCELLED",
                exit_code=process.returncode,
                stdout=self._decode(stdout, stdout_truncated),
                stderr=self._decode(stderr, stderr_truncated),
            )
        result = FileOperationResult(
            {"classification": policy.classification, "output_truncated": stdout_truncated or stderr_truncated},
        )
        result.stdout = self._decode(stdout, stdout_truncated)
        result.stderr = self._decode(stderr, stderr_truncated)
        result.exit_code = process.returncode
        if process.returncode != 0:
            raise ToolExecutionError(
                "COMMAND_EXIT_NONZERO", exit_code=process.returncode, stdout=result.stdout, stderr=result.stderr
            )
        return result

    def _decode(self, content: bytes, truncated: bool) -> str:
        text = self._redactor.redact(content.decode("utf-8", errors="replace"))
        return text + ("\n...[OUTPUT TRUNCATED]" if truncated else "")

    async def cancel(self, execution_id: str) -> bool:
        async with self._lock:
            self._cancelled.add(execution_id)
            process = self._processes.get(execution_id)
        if process is None:
            return True
        if process.returncode is not None:
            return False
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        return True


class DockerCommandRunner(LocalDevelopmentCommandRunner):
    """Runs one command in a bounded, non-root, disposable workspace container."""

    def __init__(self):
        # The host-side process is only the tightly constructed `docker run`
        # invocation below. Production may never execute the requested command
        # directly through LocalDevelopmentCommandRunner.
        super().__init__(allow_production=True)

    async def run(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        docker = shutil.which("docker")
        if not docker:
            raise ToolExecutionError("DOCKER_RUNNER_UNAVAILABLE")
        root = Path(root_path).resolve()
        cwd = (root / command.cwd).resolve()
        if cwd != root and root not in cwd.parents:
            raise ToolExecutionError("COMMAND_CWD_ESCAPE_REJECTED")
        if not cwd.is_dir():
            raise ToolExecutionError("COMMAND_CWD_NOT_FOUND")
        relative_cwd = cwd.relative_to(root).as_posix()
        image = os.getenv("NIE_ENGINEERING_RUNNER_IMAGE", "napstertec-engineering-runner:local").strip()
        container_name = f"nie-{execution_id[-16:]}"
        docker_argv = [
            docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--user",
            "65532:65532",
            "--read-only",
            "--network",
            "bridge" if policy.network_allowed else "none",
            "--cpus",
            os.getenv("NIE_ENGINEERING_CPU_LIMIT", "1"),
            "--memory",
            os.getenv("NIE_ENGINEERING_MEMORY_LIMIT", "512m"),
            "--pids-limit",
            os.getenv("NIE_ENGINEERING_PROCESS_LIMIT", "128"),
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            "--env",
            "HOME=/tmp/home",
            "--env",
            "npm_config_cache=/tmp/npm-cache",
            "--env",
            "PIP_CACHE_DIR=/tmp/pip-cache",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "CI=1",
            "--mount",
            f"type=bind,source={root},target=/workspace",
            "--workdir",
            "/workspace" + (f"/{relative_cwd}" if relative_cwd else ""),
            image,
            *command.argv,
        ]
        wrapped = command.model_copy(update={"argv": docker_argv, "cwd": ""})
        return await self._run_docker_host_command(execution_id, root_path, wrapped, policy)

    async def _run_docker_host_command(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        return await super().run(execution_id, root_path, command, policy)


class EngineeringToolRegistry:
    def __init__(self):
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"DUPLICATE_TOOL: {definition.name}")
        self._definitions[definition.name] = definition

    def require(self, name: str) -> ToolDefinition:
        definition = self._definitions.get(name)
        if definition is None:
            raise ToolExecutionError("UNKNOWN_TOOL")
        return definition

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {"name": item.name, "description": item.description, "parameters": item.input_model.model_json_schema()}
            for item in self._definitions.values()
        ]


def _file_handlers() -> EngineeringToolRegistry:
    registry = EngineeringToolRegistry()

    async def list_files(data: FilePathInput, runtime: ToolRuntime) -> FileOperationResult:
        return FileSystemTool(runtime.root_path).list_files(data.path)

    async def read_file(data: FilePathInput, runtime: ToolRuntime) -> FileOperationResult:
        return FileSystemTool(runtime.root_path).read_file(data.path)

    async def metadata(data: FilePathInput, runtime: ToolRuntime) -> FileOperationResult:
        return FileSystemTool(runtime.root_path).metadata(data.path)

    async def create_file(data: FileCreateInput, runtime: ToolRuntime) -> FileOperationResult:
        return FileSystemTool(runtime.root_path).create_file(data.path, data.content, data.overwrite)

    async def patch_file(data: FilePatchInput, runtime: ToolRuntime) -> FileOperationResult:
        return FileSystemTool(runtime.root_path).apply_patch(data.path, data.replacements)

    async def mkdir(data: DirectoryCreateInput, runtime: ToolRuntime) -> FileOperationResult:
        return FileSystemTool(runtime.root_path).create_directory(data.path)

    async def rename(data: FileRenameInput, runtime: ToolRuntime) -> FileOperationResult:
        return FileSystemTool(runtime.root_path).rename(data.source, data.destination, data.overwrite)

    async def delete(data: DeleteFileInput, runtime: ToolRuntime) -> FileOperationResult:
        if not data.approval_granted:
            raise ToolExecutionError("EXPLICIT_APPROVAL_REQUIRED", status="APPROVAL_REQUIRED")
        return FileSystemTool(runtime.root_path).delete_file(data.path)

    async def search_files(data: SearchFilesInput, runtime: ToolRuntime) -> FileOperationResult:
        return SearchTool(runtime.root_path).filenames(data.query, data.path, data.max_results)

    async def search_content(data: SearchContentInput, runtime: ToolRuntime) -> FileOperationResult:
        return SearchTool(runtime.root_path).contents(data.query, data.path, data.max_results, data.case_sensitive)

    registry.register(
        ToolDefinition(
            "filesystem.list", "List bounded workspace-relative directory entries.", FilePathInput, list_files
        )
    )
    registry.register(
        ToolDefinition("filesystem.read", "Read one bounded UTF-8 workspace file.", FilePathInput, read_file)
    )
    registry.register(
        ToolDefinition("filesystem.metadata", "Inspect safe workspace-relative file metadata.", FilePathInput, metadata)
    )
    registry.register(
        ToolDefinition(
            "filesystem.create", "Create a file atomically; overwrite must be explicit.", FileCreateInput, create_file
        )
    )
    registry.register(
        ToolDefinition(
            "filesystem.patch",
            "Apply validated exact-context text replacements atomically.",
            FilePatchInput,
            patch_file,
        )
    )
    registry.register(ToolDefinition("filesystem.mkdir", "Create a workspace directory.", DirectoryCreateInput, mkdir))
    registry.register(
        ToolDefinition("filesystem.rename", "Rename a workspace file or directory.", FileRenameInput, rename)
    )
    registry.register(
        ToolDefinition(
            "filesystem.delete", "Soft-delete a workspace file after explicit approval.", DeleteFileInput, delete
        )
    )
    registry.register(
        ToolDefinition(
            "search.files", "Search workspace filenames without leaving the workspace.", SearchFilesInput, search_files
        )
    )
    registry.register(
        ToolDefinition("search.content", "Search bounded workspace file contents.", SearchContentInput, search_content)
    )
    return registry


engineering_tool_registry = _file_handlers()


def engineering_tool_schemas() -> list[dict[str, Any]]:
    return engineering_tool_registry.schemas() + [
        {
            "name": "command.run",
            "description": (
                "Run one policy-approved argv command in the isolated workspace. Shell strings are forbidden."
            ),
            "parameters": CommandInput.model_json_schema(),
        },
        {
            "name": "git.run",
            "description": "Perform bounded local-only Git operations. Remote operations are forbidden.",
            "parameters": GitInput.model_json_schema(),
        },
    ]


def build_command_runner(mode: str | None = None, *, storage: WorkspaceStorageBackend | None = None) -> CommandRunner:
    default_mode = "docker" if is_production() else "local"
    mode = (mode or os.getenv("NIE_ENGINEERING_RUNNER") or default_mode).strip().casefold()
    if mode == "docker":
        return DockerCommandRunner()
    if mode == "vercel_sandbox":
        from app.services.vercel_sandbox_runner import VercelSandboxRunner

        try:
            return VercelSandboxRunner(storage=storage)
        except ToolExecutionError as exc:
            return UnavailableCommandRunner(exc.code)
    if mode == "local" and not is_production():
        return LocalDevelopmentCommandRunner()
    return UnavailableCommandRunner("ENGINEERING_RUNNER_NOT_CONFIGURED")


engineering_command_runner = build_command_runner()


def engineering_runner_readiness() -> dict[str, object]:
    mode = os.getenv("NIE_ENGINEERING_RUNNER", "docker" if is_production() else "local").strip().casefold()
    enabled = os.getenv("NIE_ENGINEERING_MODE_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    if mode == "vercel_sandbox":
        from app.services.vercel_sandbox_runner import vercel_sandbox_readiness

        provider = vercel_sandbox_readiness()
        return {"enabled": enabled, "runner": mode, "ready": bool(provider.get("configured")), **provider}
    ready = mode == "local" and not is_production() or mode == "docker" and shutil.which("docker") is not None
    return {"enabled": enabled, "runner": mode, "ready": ready}


def validate_engineering_runner_configuration() -> None:
    readiness = engineering_runner_readiness()
    if readiness["enabled"] and not readiness["ready"]:
        reason = str(readiness.get("reason") or "ENGINEERING_RUNNER_NOT_READY")
        raise RuntimeError(reason)


class ToolExecutionService:
    _MUTATING_FILE_TOOLS = {
        "filesystem.create",
        "filesystem.patch",
        "filesystem.mkdir",
        "filesystem.rename",
        "filesystem.delete",
    }

    def __init__(
        self,
        repository: EngineeringWorkspaceRepository,
        runner: CommandRunner | None = None,
        storage: WorkspaceStorageBackend | None = None,
    ):
        self.repository = repository
        self.policy = ToolPolicyEngine()
        try:
            self.storage = storage or build_workspace_storage(repository)
        except WorkspaceStorageError as exc:
            raise ToolExecutionError(exc.code) from exc
        self.runner = runner or build_command_runner(storage=self.storage)

    async def _execute_file_tool(
        self,
        *,
        definition: ToolDefinition,
        validated: BaseModel,
        workspace: Any,
        owner_id: str,
        execution_id: str,
        tool_name: str,
    ) -> FileOperationResult:
        if not getattr(self.storage, "is_async", False):
            runtime = ToolRuntime(execution_id, workspace.workspace_id, owner_id, workspace.root_path, self.repository)
            return await definition.handler(validated, runtime)

        snapshot = await capture_workspace(self.storage, workspace)
        with tempfile.TemporaryDirectory(prefix="nie-engineering-operation-") as temp_root:
            resolver = WorkspacePathResolver(temp_root)
            for path, stored in snapshot.files.items():
                target, _ = resolver.resolve(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stored.content)
            runtime = ToolRuntime(execution_id, workspace.workspace_id, owner_id, temp_root, self.repository)
            result = await definition.handler(validated, runtime)
            if tool_name not in self._MUTATING_FILE_TOOLS:
                return result
            local_snapshot = LocalDurableWorkspaceStorage().capture(temp_root)
            synchronized = await reconcile_workspace(
                self.storage,
                workspace,
                execution_id,
                snapshot.revision,
                {path: item.content for path, item in local_snapshot.files.items()},
            )
            result.changed_files = [str(change["path"]) for change in synchronized.changes]
            result.changes = list(synchronized.changes)
            result.change = None
            result.changes_persisted = True
            result.provider_metadata.update(
                {
                    "storage_backend": "supabase",
                    "synchronization_status": "COMMITTED",
                    "workspace_revision_before": synchronized.revision_before,
                    "workspace_revision_after": synchronized.revision_after,
                }
            )
            return result

    @staticmethod
    def _sanitize(arguments: dict[str, Any]) -> dict[str, Any]:
        sanitized = json.loads(json.dumps(arguments, default=str))
        if "content" in sanitized:
            content = str(sanitized.pop("content"))
            sanitized["content_bytes"] = len(content.encode("utf-8"))
            sanitized["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        for key in list(sanitized):
            if any(marker in key.casefold() for marker in ("secret", "token", "password", "authorization", "cookie")):
                sanitized[key] = "[REDACTED]"
        return sanitized

    async def execute(
        self,
        *,
        workspace: Any,
        owner_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        conversation_id: str | None,
        correlation_id: str | None,
        on_started: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        user_approval_verified: bool = False,
    ) -> StructuredToolResult:
        if getattr(workspace, "owner_id", None) != owner_id:
            raise ToolExecutionError("WORKSPACE_OWNERSHIP_REJECTED")
        execution_id = f"tex_{uuid.uuid4().hex}"
        correlation = correlation_id or f"cor_{uuid.uuid4().hex}"
        arguments = dict(arguments)
        if "approval_granted" in arguments:
            arguments["approval_granted"] = bool(user_approval_verified and arguments["approval_granted"])
        approval_status = "GRANTED" if arguments.get("approval_granted") else "NOT_REQUIRED"
        execution = ToolExecution(
            execution_id=execution_id,
            workspace_id=workspace.workspace_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            sanitized_arguments=self._sanitize(arguments),
            approval_status=approval_status,
            status="RUNNING",
            correlation_id=correlation,
        )
        await self.repository.begin_execution(execution)
        await self.repository.add_event(execution_id, "tool.started", {"tool_name": tool_name})
        if on_started is not None:
            await on_started(
                {
                    "execution_id": execution_id,
                    "workspace_id": workspace.workspace_id,
                    "tool_name": tool_name,
                    "status": "RUNNING",
                    "correlation_id": correlation,
                }
            )
        started = time.monotonic()
        result = FileOperationResult({})
        status = "FAILED"
        error_type = None
        exit_code = None
        try:
            if tool_name in {"command.run", "git.run"}:
                validated = (
                    GitTool.command(GitInput.model_validate(arguments))
                    if tool_name == "git.run"
                    else CommandInput.model_validate(arguments)
                )
                configured_output_limit = max(
                    1_000,
                    int(os.getenv("NIE_ENGINEERING_MAX_OUTPUT_BYTES", "100000")),
                )
                validated = validated.model_copy(
                    update={"max_output_bytes": min(validated.max_output_bytes, configured_output_limit)}
                )
                policy = self.policy.classify(validated)
                execution.approval_status = (
                    "GRANTED"
                    if validated.approval_granted
                    else ("REQUIRED" if policy.approval_required else "NOT_REQUIRED")
                )
                if not policy.permitted:
                    if policy.approval_required:
                        raise ToolExecutionError(
                            policy.reason or "EXPLICIT_APPROVAL_REQUIRED", status="APPROVAL_REQUIRED"
                        )
                    raise ToolExecutionError(policy.reason or "COMMAND_POLICY_REJECTED")
                runner_target = workspace if getattr(self.storage, "is_async", False) else workspace.root_path
                result = await self.runner.run(execution_id, runner_target, validated, policy)
                exit_code = getattr(result, "exit_code", 0)
            else:
                definition = engineering_tool_registry.require(tool_name)
                validated = definition.input_model.model_validate(arguments)
                result = await self._execute_file_tool(
                    definition=definition,
                    validated=validated,
                    workspace=workspace,
                    owner_id=owner_id,
                    execution_id=execution_id,
                    tool_name=tool_name,
                )
            status = "SUCCEEDED"
        except ValidationError:
            error_type = "TOOL_ARGUMENT_VALIDATION_FAILED"
            result.stderr = "Tool arguments did not match the registered schema."
        except WorkspaceError as exc:
            error_type = exc.code.split(":", 1)[0]
        except ToolExecutionError as exc:
            status = exc.status
            error_type = exc.code
            exit_code = exc.exit_code
            if exc.result is not None:
                result = exc.result
            result.stdout = exc.stdout or result.stdout
            result.stderr = exc.stderr or result.stderr
        except asyncio.CancelledError:
            # Persist a terminal audit event even when the requesting browser
            # disconnects. Cancellation is forwarded to the isolated runner;
            # it must never be interpreted as successful execution.
            await asyncio.shield(self.runner.cancel(execution_id))
            status = "CANCELLED"
            error_type = "EXECUTION_REQUEST_CANCELLED"
        except Exception:
            error_type = "TOOL_EXECUTION_FAILED"
        if status == "APPROVAL_REQUIRED":
            execution.approval_status = "REQUIRED"
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        changed_files = result.changed_files
        await self.repository.finish_execution(
            execution,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            error_type=error_type,
            changed_files=changed_files,
            provider_metadata=result.provider_metadata,
        )
        changes = list(result.changes)
        if result.change and not changes:
            changes.append(result.change)
        for change in changes:
            if not result.changes_persisted:
                await self.repository.add_file_change(
                    execution_id=execution_id,
                    workspace_id=workspace.workspace_id,
                    relative_path=change["path"],
                    operation=change["operation"],
                    bytes_before=change["bytes_before"],
                    bytes_after=change["bytes_after"],
                    content_sha256=change.get("sha256"),
                )
            file_event = "file.created" if change["operation"] == "CREATED" else "file.updated"
            await self.repository.add_event(
                execution_id,
                file_event,
                {"path": change["path"], "operation": change["operation"]},
            )
        if stdout:
            await self.repository.add_event(execution_id, "tool.stdout", {"content": stdout})
        if stderr:
            await self.repository.add_event(execution_id, "tool.stderr", {"content": stderr})
        terminal_event = "tool.completed" if status == "SUCCEEDED" else "tool.failed"
        await self.repository.add_event(
            execution_id, terminal_event, {"status": status, "exit_code": exit_code, "error_type": error_type}
        )
        return StructuredToolResult(
            execution_id=execution_id,
            workspace_id=workspace.workspace_id,
            tool_name=tool_name,
            status=status,
            success=status == "SUCCEEDED",
            data=result.data,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            error_type=error_type,
            changed_files=changed_files,
            correlation_id=correlation,
            evidence_verified=status == "SUCCEEDED",
        )

    async def cancel(self, execution_id: str) -> bool:
        return await self.runner.cancel(execution_id)
