"""
NapsterTec AI - Workspace Writer Tool
Module: app/tools/workspace_writer.py
"""

import os
import shutil
import tempfile
import logging
from typing import Dict, Any, Optional

from pydantic import BaseModel, Field
from app.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class WorkspaceWriterInput(BaseModel):
    path: str = Field(..., description="Target file path relative to project root.")
    content: str = Field(default="", description="Content to write or append. Ignored if mode is 'delete'.")
    mode: str = Field(default="overwrite", description="'overwrite', 'append', or 'delete'")


class WorkspaceWriterOutput(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    access_mode: str = "WRITE_APPROVED"


class WorkspaceWriterTool(BaseTool):
    """
    Controlled workspace writer.

    Security invariants:
    - defaults to READ_ONLY when authority context is absent
    - requires mutation_allowed=True for writes
    - requires path to remain inside NAPSTERTEC_PROJECT_ROOT
    - validates mode
    - creates backups before overwriting or deleting existing files
    - uses atomic replacement for overwrite operations
    """

    name: str = "workspace_writer"
    description: str = (
        "Writes, appends, or deletes local project files. "
        "Requires explicit WRITE_APPROVED / mutation-authorized context."
    )
    input_schema = WorkspaceWriterInput
    output_schema = WorkspaceWriterOutput
    capabilities = ["write", "filesystem"]
    permissions = ["write", "local_repository_write"]

    ALLOWED_MODES = {"overwrite", "append", "delete"}

    def _get_project_root(self) -> str:
        root = os.environ.get("NAPSTERTEC_PROJECT_ROOT", os.getcwd())
        return os.path.realpath(os.path.abspath(root))

    def _resolve_safe_path(self, root: str, target: str) -> Optional[str]:
        """
        Resolve the requested path and ensure it remains inside project root.

        Uses os.path.commonpath instead of string-prefix matching, avoiding
        unsafe cases such as /project accidentally matching /project_backup.
        """

        if not target or not isinstance(target, str):
            return None

        candidate = os.path.realpath(
            os.path.abspath(
                os.path.join(root, target)
            )
        )

        try:
            if os.path.commonpath([root, candidate]) != root:
                return None
        except ValueError:
            # Can occur across different Windows drives.
            return None

        return candidate

    def _extract_authority(self, context: Any) -> Dict[str, Any]:
        """
        Normalize command authority information.
        Absence of authority ALWAYS defaults to read-only.
        """
        authority = {
            "read_only": True,
            "mutation_allowed": False,
            "local_repository_write": False,
            "access_mode": "READ_ONLY",
        }

        if context is None:
            return authority

        if isinstance(context, dict):
            read_only = context.get(
                "read_only",
                context.get("mission_read_only", True),
            )
            mutation_allowed = context.get("mutation_allowed", False)
            access_mode = context.get("access_mode")
            local_repo_allowed = context.get("local_repository_mutation_allowed", False)
            command_class = context.get("command_class", "")
            has_local_write = local_repo_allowed or command_class == "REPOSITORY_DEVELOPMENT"
        else:
            read_only = getattr(
                context,
                "read_only",
                getattr(context, "mission_read_only", True),
            )
            mutation_allowed = getattr(context, "mutation_allowed", False)
            access_mode = getattr(context, "access_mode", None)
            
            runtime_metadata = getattr(context, "runtime_metadata", {})
            command_context = runtime_metadata.get("command_context", {})
            local_repo_allowed = command_context.get("local_repository_mutation_allowed", False)
            command_class = command_context.get("command_class", "")
            has_local_write = local_repo_allowed or command_class == "REPOSITORY_DEVELOPMENT"

        mutation_allowed = bool(mutation_allowed)
        read_only = bool(read_only)
        has_local_write = bool(has_local_write)

        if read_only:
            mutation_allowed = False
            has_local_write = False

        if mutation_allowed or has_local_write:
            resolved_mode = access_mode or "WRITE_APPROVED"
        else:
            resolved_mode = "READ_ONLY"

        return {
            "read_only": read_only,
            "mutation_allowed": mutation_allowed or has_local_write,
            "local_repository_write": has_local_write,
            "access_mode": resolved_mode,
        }

    async def execute(
        self,
        path: str,
        content: str = "",
        mode: str = "overwrite",
        **kwargs,
    ) -> dict:
        """
        Perform a guarded file write or delete.

        ToolManager / AutonomousAgentLoop should pass command authority through
        kwargs["context"].
        """

        context = kwargs.get("context")
        authority = self._extract_authority(context)

        if not authority["mutation_allowed"] or not authority.get("local_repository_write"):
            return {
                "success": False,
                "data": None,
                "error": (
                    "WORKSPACE_WRITE_BLOCKED: CommandMutationGuard enforced "
                    "READ_ONLY policy. Explicit LOCAL_REPOSITORY_WRITE authorization is required."
                ),
                "access_mode": "READ_ONLY",
            }

        if mode not in self.ALLOWED_MODES:
            return {
                "success": False,
                "data": None,
                "error": (
                    f"INVALID_WRITE_MODE: '{mode}' is not supported. "
                    f"Allowed modes: {sorted(self.ALLOWED_MODES)}"
                ),
                "access_mode": authority["access_mode"],
            }

        if not isinstance(content, str):
            return {
                "success": False,
                "data": None,
                "error": "INVALID_CONTENT: Workspace writer expects string content.",
                "access_mode": authority["access_mode"],
            }

        root = self._get_project_root()
        target_path = self._resolve_safe_path(root, path)

        if target_path is None:
            return {
                "success": False,
                "data": None,
                "error": (
                    "OUTSIDE_WORKSPACE: Target path is outside the configured "
                    "NapsterTec project root."
                ),
                "access_mode": authority["access_mode"],
            }

        # Never allow replacing or deleting the project root itself.
        if target_path == root:
            return {
                "success": False,
                "data": None,
                "error": "INVALID_TARGET: Project root cannot be modified or deleted as a file.",
                "access_mode": authority["access_mode"],
            }

        parent_dir = os.path.dirname(target_path)

        try:
            os.makedirs(parent_dir, exist_ok=True)

            backup_path = None
            bytes_written = 0

            if mode == "delete":
                if os.path.exists(target_path):
                    backup_path = target_path + ".bak"
                    shutil.copy2(target_path, backup_path)
                    os.remove(target_path)
                    logger.info("[WorkspaceWriter] Created safety backup at %s and deleted file.", backup_path)
                else:
                    logger.info("[WorkspaceWriter] Delete requested but file %s did not exist.", target_path)

            elif mode == "append":
                with open(
                    target_path,
                    "a",
                    encoding="utf-8",
                    newline="",
                ) as handle:
                    handle.write(content)
                bytes_written = len(content.encode("utf-8"))

            else: # overwrite
                # Preserve the previous version before replacement.
                if os.path.exists(target_path):
                    backup_path = target_path + ".bak"
                    shutil.copy2(target_path, backup_path)
                    logger.info(
                        "[WorkspaceWriter] Created safety backup at %s",
                        backup_path,
                    )

                # Atomic overwrite:
                # write temporary file in the same directory, then replace.
                fd, temp_path = tempfile.mkstemp(
                    prefix=".nie_write_",
                    suffix=".tmp",
                    dir=parent_dir,
                    text=True,
                )

                try:
                    with os.fdopen(
                        fd,
                        "w",
                        encoding="utf-8",
                        newline="",
                    ) as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())

                    os.replace(temp_path, target_path)
                    bytes_written = len(content.encode("utf-8"))

                except Exception:
                    try:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                    except Exception:
                        pass
                    raise

            logger.info(
                "[WorkspaceWriter] %s completed for %s",
                mode,
                path,
            )

            return {
                "success": True,
                "data": {
                    "path": path,
                    "bytes_written": bytes_written,
                    "operation": mode,
                    "backup_created": backup_path is not None,
                    "backup_path": (
                        os.path.relpath(backup_path, root)
                        if backup_path
                        else None
                    ),
                },
                "error": None,
                "access_mode": authority["access_mode"],
            }

        except Exception as exc:
            logger.error(
                "[WorkspaceWriterTool] Failed to perform %s on %s: %s",
                mode,
                path,
                str(exc),
                exc_info=True,
            )

            return {
                "success": False,
                "data": None,
                "error": f"WORKSPACE_ERROR: {str(exc)}",
                "access_mode": authority["access_mode"],
            }