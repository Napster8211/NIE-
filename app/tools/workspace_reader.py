"""
NapsterTec AI - Workspace Reader Tool
Module: app/tools/workspace_reader.py
"""

import os
import glob
import logging
from typing import Dict, Any, Optional

from pydantic import BaseModel, Field
from app.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class WorkspaceReaderInput(BaseModel):
    action: str = Field(
        ...,
        description=(
            "Action to perform: "
            "'list_directory', 'read_file', 'search_codebase', or 'find_text'"
        ),
    )
    path: Optional[str] = Field(
        None,
        description="Target path relative to project root. Defaults to root.",
    )
    query: Optional[str] = Field(
        None,
        description="Search term for 'search_codebase' or 'find_text'.",
    )
    start_line: int = Field(
        default=1,
        ge=1,
        description="1-based starting line for read_file.",
    )
    max_lines: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="Maximum number of lines returned by read_file.",
    )
    max_chars: int = Field(
        default=12000,
        ge=1000,
        le=50000,
        description="Maximum characters returned in one read_file response.",
    )
    context_lines: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Context lines before/after matches for find_text.",
    )


class WorkspaceReaderOutput(BaseModel):
    success: bool
    data: Any
    error: Optional[str] = None
    access_mode: str = "READ_ONLY"


class WorkspaceReaderTool(BaseTool):
    """
    Safe read-only workspace inspection tool.

    Supports:
    - bounded directory listing
    - bounded line-range file reads
    - codebase search
    - targeted in-file text lookup with surrounding context
    """

    name: str = "workspace_reader"
    description: str = (
        "Inspects local project directories, reads bounded file ranges, "
        "searches the codebase, and locates text/symbols. Strictly read-only."
    )
    input_schema = WorkspaceReaderInput
    output_schema = WorkspaceReaderOutput
    capabilities = ["read", "filesystem"]
    permissions = ["read"]

    def _get_project_root(self) -> str:
        root = os.environ.get("NAPSTERTEC_PROJECT_ROOT", os.getcwd())
        return os.path.realpath(os.path.abspath(root))

    def _resolve_safe_path(self, root: str, target: str) -> Optional[str]:
        candidate = os.path.realpath(
            os.path.abspath(os.path.join(root, target or ""))
        )

        try:
            if os.path.commonpath([root, candidate]) != root:
                return None
        except ValueError:
            return None

        return candidate

    @staticmethod
    def _truncate_chars(text: str, max_chars: int) -> tuple[str, bool]:
        if len(text) <= max_chars:
            return text, False

        return (
            text[:max_chars]
            + f"\n...[TRUNCATED {len(text) - max_chars} CHARACTERS BY WORKSPACE_READER]...",
            True,
        )

    async def execute(
        self,
        action: str,
        path: Optional[str] = None,
        query: Optional[str] = None,
        start_line: int = 1,
        max_lines: int = 200,
        max_chars: int = 12000,
        context_lines: int = 20,
        **kwargs,
    ) -> dict:
        root = self._get_project_root()
        target_path = self._resolve_safe_path(root, path or "")

        if target_path is None:
            return {
                "success": False,
                "data": None,
                "error": "OUTSIDE_WORKSPACE: Path traversal blocked.",
                "access_mode": "READ_ONLY",
            }

        try:
            if action == "list_directory":
                if not os.path.isdir(target_path):
                    return {
                        "success": False,
                        "data": None,
                        "error": "FILE_NOT_FOUND: Directory does not exist.",
                        "access_mode": "READ_ONLY",
                    }

                directories = []
                files = []

                for item in sorted(os.listdir(target_path)):
                    if item.startswith(".") or item == "__pycache__":
                        continue

                    full_item = os.path.join(target_path, item)

                    if os.path.isdir(full_item):
                        directories.append(item)
                    else:
                        files.append(item)

                return {
                    "success": True,
                    "data": {
                        "path": path or "/",
                        "contents": {
                            "directories": directories,
                            "files": files,
                        },
                    },
                    "error": None,
                    "access_mode": "READ_ONLY",
                }

            if action == "read_file":
                if not os.path.isfile(target_path):
                    return {
                        "success": False,
                        "data": None,
                        "error": "FILE_NOT_FOUND: File does not exist.",
                        "access_mode": "READ_ONLY",
                    }

                with open(
                    target_path,
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as handle:
                    lines = handle.readlines()

                total_lines = len(lines)

                if total_lines == 0:
                    return {
                        "success": True,
                        "data": {
                            "path": path,
                            "content": "",
                            "start_line": 1,
                            "end_line": 0,
                            "total_lines": 0,
                            "has_more": False,
                            "next_start_line": None,
                            "truncated_by_chars": False,
                        },
                        "error": None,
                        "access_mode": "READ_ONLY",
                    }

                safe_start = max(1, min(start_line, total_lines))
                start_index = safe_start - 1
                end_index = min(total_lines, start_index + max_lines)

                selected_lines = lines[start_index:end_index]

                numbered = "".join(
                    f"{line_number}: {line}"
                    for line_number, line in enumerate(
                        selected_lines,
                        start=safe_start,
                    )
                )

                content, truncated_by_chars = self._truncate_chars(
                    numbered,
                    max_chars,
                )

                has_more = (
                    end_index < total_lines
                    or truncated_by_chars
                )

                next_start_line = (
                    end_index + 1
                    if end_index < total_lines
                    else None
                )

                return {
                    "success": True,
                    "data": {
                        "path": path,
                        "content": content,
                        "start_line": safe_start,
                        "end_line": end_index,
                        "total_lines": total_lines,
                        "has_more": has_more,
                        "next_start_line": next_start_line,
                        "truncated_by_chars": truncated_by_chars,
                        "requested_max_lines": max_lines,
                        "requested_max_chars": max_chars,
                    },
                    "error": None,
                    "access_mode": "READ_ONLY",
                }

            if action == "find_text":
                if not query:
                    return {
                        "success": False,
                        "data": None,
                        "error": "QUERY_REQUIRED: find_text requires a query.",
                        "access_mode": "READ_ONLY",
                    }

                if not os.path.isfile(target_path):
                    return {
                        "success": False,
                        "data": None,
                        "error": "FILE_NOT_FOUND: File does not exist.",
                        "access_mode": "READ_ONLY",
                    }

                with open(
                    target_path,
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as handle:
                    lines = handle.readlines()

                query_lower = query.lower()
                matches = []

                for index, line in enumerate(lines):
                    if query_lower not in line.lower():
                        continue

                    match_line = index + 1
                    context_start = max(1, match_line - context_lines)
                    context_end = min(
                        len(lines),
                        match_line + context_lines,
                    )

                    snippet = "".join(
                        f"{line_number}: {lines[line_number - 1]}"
                        for line_number in range(
                            context_start,
                            context_end + 1,
                        )
                    )

                    snippet, snippet_truncated = self._truncate_chars(
                        snippet,
                        max_chars,
                    )

                    matches.append(
                        {
                            "line_number": match_line,
                            "match": line.strip(),
                            "context_start_line": context_start,
                            "context_end_line": context_end,
                            "context": snippet,
                            "context_truncated": snippet_truncated,
                        }
                    )

                    if len(matches) >= 20:
                        break

                return {
                    "success": True,
                    "data": {
                        "path": path,
                        "query": query,
                        "matches": matches,
                        "match_count": len(matches),
                        "total_lines": len(lines),
                        "search_complete": True,
                    },
                    "error": None,
                    "access_mode": "READ_ONLY",
                }

            if action == "search_codebase":
                if not query:
                    return {
                        "success": False,
                        "data": None,
                        "error": (
                            "QUERY_REQUIRED: search_codebase requires a query."
                        ),
                        "access_mode": "READ_ONLY",
                    }

                search_root = target_path

                if not os.path.isdir(search_root):
                    return {
                        "success": False,
                        "data": None,
                        "error": (
                            "FILE_NOT_FOUND: Search path is not a directory."
                        ),
                        "access_mode": "READ_ONLY",
                    }

                results = []

                extensions = (
                    "*.py",
                    "*.js",
                    "*.jsx",
                    "*.ts",
                    "*.tsx",
                    "*.json",
                    "*.md",
                )

                for extension in extensions:
                    pattern = os.path.join(
                        search_root,
                        "**",
                        extension,
                    )

                    for filepath in glob.glob(pattern, recursive=True):
                        lowered = filepath.lower()

                        if any(
                            blocked in lowered
                            for blocked in (
                                os.sep + "venv" + os.sep,
                                os.sep + ".git" + os.sep,
                                os.sep + "__pycache__" + os.sep,
                                os.sep + "node_modules" + os.sep,
                            )
                        ):
                            continue

                        try:
                            with open(
                                filepath,
                                "r",
                                encoding="utf-8",
                                errors="ignore",
                            ) as handle:
                                for line_number, line in enumerate(
                                    handle,
                                    start=1,
                                ):
                                    if query.lower() in line.lower():
                                        results.append(
                                            {
                                                "file": os.path.relpath(
                                                    filepath,
                                                    root,
                                                ),
                                                "line_number": line_number,
                                                "match": line.strip(),
                                            }
                                        )

                                        if len(results) >= 100:
                                            break

                        except Exception:
                            continue

                        if len(results) >= 100:
                            break

                    if len(results) >= 100:
                        break

                return {
                    "success": True,
                    "data": {
                        "query": query,
                        "matches": results,
                        "match_count": len(results),
                        "results_truncated": len(results) >= 100,
                    },
                    "error": None,
                    "access_mode": "READ_ONLY",
                }

            return {
                "success": False,
                "data": None,
                "error": f"UNKNOWN_ACTION: {action}",
                "access_mode": "READ_ONLY",
            }

        except Exception as exc:
            logger.error(
                "[WorkspaceReaderTool] Failed: %s",
                str(exc),
                exc_info=True,
            )

            return {
                "success": False,
                "data": None,
                "error": f"WORKSPACE_ERROR: {str(exc)}",
                "access_mode": "READ_ONLY",
            }