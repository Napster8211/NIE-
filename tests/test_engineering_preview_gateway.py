import os
import unittest
from unittest.mock import patch

import httpx

from app.api.engineering_workspace import router
from app.services.engineering_authorization import require_engineering_access
from app.services.engineering_execution_service import ToolExecutionError
from app.services.engineering_preview_gateway import (
    EngineeringPreviewGateway,
    preview_cookie_name,
    preview_gateway_path,
)


class PreviewGatewayTests(unittest.IsolatedAsyncioTestCase):
    gateway_path = "/api/v1/engineering/workspaces/ews_test/processes/wpr_test/preview/"

    async def test_owner_gateway_forwards_only_safe_headers_and_rewrites_provider_origin(self):
        captured = {}

        async def handler(request):
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "set-cookie": "provider=secret",
                    "location": "https://evil.example",
                },
                content=b'<a href="https://preview-123.vercel.run/docs">docs</a>',
            )

        gateway = EngineeringPreviewGateway(transport=httpx.MockTransport(handler))
        result = await gateway.fetch(
            provider_url="https://preview-123.vercel.run",
            public_gateway_path=self.gateway_path,
            preview_path="docs/index.html",
            query_items=[("theme", "dark")],
            request_headers={
                "Authorization": "Bearer must-not-forward",
                "Cookie": "session=must-not-forward",
                "Host": "attacker.example",
                "X-Forwarded-Host": "attacker.example",
                "Accept": "text/html",
            },
            method="GET",
        )

        self.assertEqual(200, result.status_code)
        self.assertIn(self.gateway_path.rstrip("/").encode(), result.content)
        self.assertNotIn(b"preview-123.vercel.run", result.content)
        self.assertNotIn("authorization", captured["headers"])
        self.assertNotIn("cookie", captured["headers"])
        self.assertNotIn("x-forwarded-host", captured["headers"])
        self.assertEqual("preview-123.vercel.run", httpx.URL(captured["url"]).host)
        self.assertNotIn("set-cookie", {key.casefold() for key in result.headers})
        self.assertNotIn("location", {key.casefold() for key in result.headers})
        self.assertEqual("no-referrer", result.headers["Referrer-Policy"])

    async def test_client_supplied_headers_and_path_cannot_change_upstream(self):
        seen = []

        async def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

        gateway = EngineeringPreviewGateway(transport=httpx.MockTransport(handler))
        await gateway.fetch(
            provider_url="https://safe-preview.vercel.run",
            public_gateway_path=self.gateway_path,
            preview_path="nested/file.txt",
            query_items=[("next", "http://169.254.169.254/latest")],
            request_headers={"Host": "169.254.169.254", "X-Forwarded-Proto": "http"},
            method="GET",
        )

        self.assertEqual("safe-preview.vercel.run", httpx.URL(seen[0]).host)
        self.assertEqual("/nested/file.txt", httpx.URL(seen[0]).path)

    async def test_ssrf_schemes_hosts_ports_and_path_traversal_are_rejected(self):
        invalid = [
            "http://safe-preview.vercel.run",
            "https://127.0.0.1",
            "https://safe-preview.vercel.run:8443",
            "https://vercel.run",
            "https://safe-preview.vercel.run?target=other",
            "https://user:password@safe-preview.vercel.run",
        ]
        for provider_url in invalid:
            with self.subTest(provider_url=provider_url):
                with self.assertRaisesRegex(ToolExecutionError, "PREVIEW_UPSTREAM_ROUTE_INVALID"):
                    EngineeringPreviewGateway._validated_upstream(provider_url, "", [])
        with self.assertRaisesRegex(ToolExecutionError, "PREVIEW_PATH_INVALID"):
            EngineeringPreviewGateway._validated_upstream(
                "https://safe-preview.vercel.run",
                "../secret",
                [],
            )

    async def test_upstream_redirect_is_never_returned(self):
        gateway = EngineeringPreviewGateway(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(302, headers={"location": "https://other.vercel.run"})
            )
        )
        with self.assertRaisesRegex(ToolExecutionError, "PREVIEW_UPSTREAM_REDIRECT_REJECTED"):
            await gateway.fetch(
                provider_url="https://safe-preview.vercel.run",
                public_gateway_path=self.gateway_path,
                preview_path="",
                query_items=[],
                request_headers={},
                method="GET",
            )

    async def test_oversized_and_timed_out_responses_fail_closed(self):
        with patch.dict(os.environ, {"NIE_ENGINEERING_PREVIEW_MAX_RESPONSE_BYTES": "10000"}, clear=False):
            oversized = EngineeringPreviewGateway(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        headers={"content-length": "10001", "content-type": "application/octet-stream"},
                        content=b"x",
                    )
                )
            )
        with self.assertRaisesRegex(ToolExecutionError, "PREVIEW_RESPONSE_TOO_LARGE"):
            await oversized.fetch(
                provider_url="https://safe-preview.vercel.run",
                public_gateway_path=self.gateway_path,
                preview_path="asset.bin",
                query_items=[],
                request_headers={},
                method="GET",
            )

        async def timeout(_):
            raise httpx.ReadTimeout("timeout")

        timed_out = EngineeringPreviewGateway(transport=httpx.MockTransport(timeout))
        with self.assertRaisesRegex(ToolExecutionError, "PREVIEW_UPSTREAM_TIMEOUT"):
            await timed_out.fetch(
                provider_url="https://safe-preview.vercel.run",
                public_gateway_path=self.gateway_path,
                preview_path="",
                query_items=[],
                request_headers={},
                method="GET",
            )

    def test_gateway_url_and_cookie_name_contain_no_provider_identifier(self):
        path = preview_gateway_path("ews_test", "wpr_test")
        cookie = preview_cookie_name("wpr_test")
        self.assertEqual(self.gateway_path, path)
        self.assertNotIn("vercel", path.casefold())
        self.assertNotIn("wpr_test", cookie)

    def test_preview_routes_require_authenticated_engineering_principal(self):
        preview_routes = [
            route
            for route in router.routes
            if getattr(route, "path", "").endswith("/preview")
            or getattr(route, "path", "").endswith("/preview/{preview_path:path}")
        ]
        self.assertEqual(2, len(preview_routes))
        for route in preview_routes:
            dependencies = {dependency.call for dependency in route.dependant.dependencies}
            self.assertIn(require_engineering_access, dependencies)


if __name__ == "__main__":
    unittest.main()
