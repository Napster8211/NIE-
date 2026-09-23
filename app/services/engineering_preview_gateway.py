"""Owner-scoped HTTP gateway for isolated Engineering preview processes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from app.services.engineering_execution_service import ToolExecutionError

_VERCEL_ROUTE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.vercel\.run$"
)
_REQUEST_HEADERS = {"accept", "accept-language", "if-none-match", "if-modified-since", "range"}
_RESPONSE_HEADERS = {
    "accept-ranges",
    "content-language",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
}


def preview_gateway_path(workspace_id: str, process_id: str) -> str:
    return f"/api/v1/engineering/workspaces/{workspace_id}/processes/{process_id}/preview/"


def preview_cookie_name(process_id: str) -> str:
    suffix = hashlib.sha256(process_id.encode("utf-8")).hexdigest()[:20]
    return f"nie_eng_preview_{suffix}"


def preview_cookie_secure() -> bool:
    configured = os.getenv("NIE_ENGINEERING_PREVIEW_COOKIE_SECURE")
    if configured is not None:
        return configured.strip().casefold() in {"1", "true", "yes", "on"}
    environment = (os.getenv("NIE_ENV") or os.getenv("ENVIRONMENT") or "development").strip().casefold()
    return environment in {"production", "prod", "staging", "stage"}


def preview_cookie_samesite() -> str:
    configured = os.getenv("NIE_ENGINEERING_PREVIEW_COOKIE_SAMESITE", "").strip().casefold()
    if configured:
        if configured not in {"lax", "strict", "none"}:
            raise ToolExecutionError("ENGINEERING_PREVIEW_COOKIE_SAMESITE_INVALID")
        return configured
    return "none" if preview_cookie_secure() else "lax"


@dataclass(frozen=True)
class PreviewGatewayResult:
    status_code: int
    headers: dict[str, str]
    content: bytes


class EngineeringPreviewGateway:
    """Fetch preview content without disclosing or redirecting to its provider URL."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.max_response_bytes = self._bounded_int(
            "NIE_ENGINEERING_PREVIEW_MAX_RESPONSE_BYTES", 5_000_000, 10_000, 20_000_000
        )
        self.connect_timeout_seconds = self._bounded_float(
            "NIE_ENGINEERING_PREVIEW_CONNECT_TIMEOUT_SECONDS", 3.0, 0.2, 15.0
        )
        self.read_timeout_seconds = self._bounded_float("NIE_ENGINEERING_PREVIEW_READ_TIMEOUT_SECONDS", 10.0, 0.5, 60.0)
        self.total_timeout_seconds = self._bounded_float(
            "NIE_ENGINEERING_PREVIEW_TOTAL_TIMEOUT_SECONDS", 15.0, 1.0, 90.0
        )

    @staticmethod
    def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise ToolExecutionError(f"{name}_INVALID") from exc
        if value < minimum or value > maximum:
            raise ToolExecutionError(f"{name}_INVALID")
        return value

    @staticmethod
    def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except ValueError as exc:
            raise ToolExecutionError(f"{name}_INVALID") from exc
        if value < minimum or value > maximum:
            raise ToolExecutionError(f"{name}_INVALID")
        return value

    @staticmethod
    def _validated_upstream(provider_url: str, preview_path: str, query_items: list[tuple[str, str]]) -> str:
        parsed = urlsplit(provider_url)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or not _VERCEL_ROUTE.fullmatch(hostname)
        ):
            raise ToolExecutionError("PREVIEW_UPSTREAM_ROUTE_INVALID")
        normalized = preview_path.replace("\\", "/").lstrip("/")
        parts = tuple(part for part in normalized.split("/") if part)
        if "\x00" in normalized or any(part in {".", ".."} for part in parts):
            raise ToolExecutionError("PREVIEW_PATH_INVALID")
        encoded_path = "/".join(quote(part, safe="-._~@") for part in parts)
        base_path = parsed.path.rstrip("/")
        path = f"{base_path}/{encoded_path}" if encoded_path else f"{base_path}/"
        query = urlencode(query_items, doseq=True)
        return urlunsplit(("https", hostname, path, query, ""))

    @staticmethod
    def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
        forwarded = {
            name: value for raw_name, value in headers.items() if (name := raw_name.casefold()) in _REQUEST_HEADERS
        }
        forwarded["accept-encoding"] = "identity"
        return forwarded

    @staticmethod
    def _response_headers(headers: httpx.Headers) -> dict[str, str]:
        safe = {name: value for name, value in headers.items() if name.casefold() in _RESPONSE_HEADERS}
        safe.update(
            {
                "Cache-Control": "private, no-store, max-age=0",
                "Content-Security-Policy": (
                    "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
                    "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            }
        )
        return safe

    async def fetch(
        self,
        *,
        provider_url: str,
        public_gateway_path: str,
        preview_path: str,
        query_items: list[tuple[str, str]],
        request_headers: dict[str, str],
        method: str,
    ) -> PreviewGatewayResult:
        if method not in {"GET", "HEAD"}:
            raise ToolExecutionError("PREVIEW_METHOD_REJECTED")
        upstream = self._validated_upstream(provider_url, preview_path, query_items)
        timeout = httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.connect_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )

        async def fetch_bounded() -> tuple[int, httpx.Headers, bytes]:
            async with httpx.AsyncClient(
                transport=self.transport,
                follow_redirects=False,
                timeout=timeout,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method,
                    upstream,
                    headers=self._forward_headers(request_headers),
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise ToolExecutionError("PREVIEW_UPSTREAM_REDIRECT_REJECTED")
                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            if int(declared_size) > self.max_response_bytes:
                                raise ToolExecutionError("PREVIEW_RESPONSE_TOO_LARGE")
                        except ValueError as exc:
                            raise ToolExecutionError("PREVIEW_UPSTREAM_RESPONSE_INVALID") from exc
                    chunks: list[bytes] = []
                    size = 0
                    if method != "HEAD":
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.max_response_bytes:
                                raise ToolExecutionError("PREVIEW_RESPONSE_TOO_LARGE")
                            chunks.append(chunk)
                    return response.status_code, httpx.Headers(response.headers), b"".join(chunks)

        try:
            status_code, response_headers, content = await asyncio.wait_for(
                fetch_bounded(),
                timeout=self.total_timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ToolExecutionError("PREVIEW_UPSTREAM_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError("PREVIEW_UPSTREAM_UNAVAILABLE") from exc
        content_type = response_headers.get("content-type", "").casefold()
        if content and any(marker in content_type for marker in ("text/", "javascript", "json", "xml", "svg")):
            provider_origin = provider_url.rstrip("/").encode("utf-8")
            gateway_origin = public_gateway_path.rstrip("/").encode("utf-8")
            content = content.replace(provider_origin, gateway_origin)
            provider_host = (urlsplit(provider_url).hostname or "").encode("utf-8")
            if provider_host and provider_host in content:
                raise ToolExecutionError("PREVIEW_PROVIDER_REFERENCE_REJECTED")
        return PreviewGatewayResult(
            status_code=status_code,
            headers=self._response_headers(response_headers),
            content=content,
        )
