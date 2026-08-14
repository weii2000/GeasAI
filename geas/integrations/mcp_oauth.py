from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from urllib.parse import parse_qs, urlsplit, urlunsplit

from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.auth.oauth2 import OAuthContext
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl, ValidationError


OAUTH_CALLBACK_URL = "http://127.0.0.1:8765/callback"


class _OAuthContext(OAuthContext):
    def prepare_token_auth(
        self,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], dict[str, str]]:
        data, headers = super().prepare_token_auth(data, headers)
        if (
            self.client_info is not None
            and self.client_info.token_endpoint_auth_method
            == "client_secret_basic"
        ):
            data.pop("client_id", None)
        return data, headers


@dataclass(frozen=True)
class MCPOAuthConfig:
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.client_id == "" or self.client_secret == "":
            raise ValueError("MCP OAuth credentials cannot be empty")
        if self.client_secret is not None and self.client_id is None:
            raise ValueError("MCP OAuth client_secret requires client_id")


class MCPAuthorizationRequired(RuntimeError):
    def __init__(self, server: str) -> None:
        self.server = server
        super().__init__(f'MCP server "{server}" requires OAuth login')


class FileOAuthStorage:
    def __init__(
        self,
        server_url: str,
        root: Path | None = None,
    ) -> None:
        self.server_url = _normalize_server_url(server_url)
        self.root = root or Path.home() / ".geas" / "mcp" / "oauth"
        digest = hashlib.sha256(self.server_url.encode()).hexdigest()[:24]
        self.path = self.root / f"{digest}.json"

    async def get_tokens(self) -> OAuthToken | None:
        value = self._read().get("tokens")
        if value is None:
            return None
        try:
            return OAuthToken.model_validate(value)
        except ValidationError as error:
            raise ValueError(f"Invalid MCP OAuth token store: {self.path}") from error

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = self._read().get("client_info")
        if value is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(value)
        except ValidationError as error:
            raise ValueError(f"Invalid MCP OAuth client store: {self.path}") from error

    async def set_client_info(
        self,
        client_info: OAuthClientInformationFull,
    ) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(
            mode="json",
            exclude_none=True,
        )
        self._write(data)

    async def configure_client(self, config: MCPOAuthConfig) -> None:
        if config.client_id is None:
            return
        current = await self.get_client_info()
        if current is not None and current.client_id != config.client_id:
            raise ValueError(
                "Stored MCP OAuth client_id does not match configuration: "
                f"{self.path}"
            )
        if current is not None:
            if current.client_secret != config.client_secret:
                await self.set_client_info(
                    current.model_copy(
                        update={"client_secret": config.client_secret}
                    )
                )
            return
        client_info = OAuthClientInformationFull(
            client_id=config.client_id,
            client_secret=config.client_secret,
            redirect_uris=[AnyUrl(OAUTH_CALLBACK_URL)],
            token_endpoint_auth_method=(
                "client_secret_post"
                if config.client_secret is not None
                else "none"
            ),
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )
        await self.set_client_info(client_info)

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {
                "version": 1,
                "server_url": self.server_url,
            }
        self.root.chmod(0o700)
        self.path.chmod(0o600)
        try:
            value = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid MCP OAuth store: {self.path}") from error
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or value.get("server_url") != self.server_url
        ):
            raise ValueError(f"Invalid MCP OAuth store: {self.path}")
        return value

    def _write(self, data: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
        )
        temporary.chmod(0o600)
        # ponytail: one local Geas process owns a token file; add locking only
        # if multiple processes ever share the same OAuth account.
        temporary.replace(self.path)


class BrowserOAuthFlow:
    def __init__(self, timeout: float = 300.0, port: int = 8765) -> None:
        self.timeout = timeout
        self.port = port
        self._event = threading.Event()
        self._result: AuthorizationCodeResult | None = None
        self._error: str | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    async def __aenter__(self) -> BrowserOAuthFlow:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await asyncio.to_thread(self.close)

    async def redirect(self, authorization_url: str) -> None:
        self._start()
        print(f"Open this URL to authorize Geas:\n{authorization_url}")
        webbrowser.open(authorization_url)

    async def callback(self) -> AuthorizationCodeResult:
        completed = await asyncio.to_thread(self._event.wait, self.timeout)
        if not completed:
            raise TimeoutError("Timed out waiting for MCP OAuth callback")
        if self._error is not None:
            raise RuntimeError(f"MCP OAuth authorization failed: {self._error}")
        if self._result is None:
            raise RuntimeError("MCP OAuth callback contained no authorization code")
        return self._result

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def _start(self) -> None:
        if self._server is not None:
            return
        flow = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status, message = flow._accept(self.path)
                if status == 404:
                    self.send_error(404)
                    return
                self._finish(status, message)

            def _finish(self, status: int, message: str) -> None:
                body = message.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                flow._event.set()

            def log_message(self, _format: str, *_args: object) -> None:
                pass

        try:
            self._server = ThreadingHTTPServer(
                ("127.0.0.1", self.port),
                CallbackHandler,
            )
        except OSError as error:
            raise RuntimeError(
                f"OAuth callback port {self.port} is unavailable"
            ) from error
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()

    def _accept(self, target: str) -> tuple[int, str]:
        parsed = urlsplit(target)
        if parsed.path != "/callback":
            return 404, "Not found."
        query = parse_qs(parsed.query)
        if error := query.get("error", [None])[0]:
            self._error = str(error)[:200]
            self._event.set()
            return 400, "Authorization failed. Return to the terminal."
        code = query.get("code", [None])[0]
        if not code:
            self._event.set()
            return 400, "Missing authorization code."
        self._result = AuthorizationCodeResult(
            code=code,
            state=query.get("state", [None])[0],
            iss=query.get("iss", [None])[0],
        )
        self._event.set()
        return 200, "Authorization complete. You may close this window."


async def create_oauth_provider(
    server: str,
    server_url: str,
    config: MCPOAuthConfig,
    *,
    interactive: bool,
    storage_root: Path | None,
) -> tuple[OAuthClientProvider, BrowserOAuthFlow | None]:
    storage = FileOAuthStorage(server_url, storage_root)
    await storage.configure_client(config)
    flow = BrowserOAuthFlow() if interactive else None

    async def login_required(_authorization_url: str) -> None:
        raise MCPAuthorizationRequired(server)

    async def unreachable_callback() -> AuthorizationCodeResult:
        raise MCPAuthorizationRequired(server)

    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name="Geas MCP Client",
            redirect_uris=[AnyUrl(OAUTH_CALLBACK_URL)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=flow.redirect if flow else login_required,
        callback_handler=flow.callback if flow else unreachable_callback,
    )
    provider.context = _OAuthContext(**vars(provider.context))
    return provider, flow


def _normalize_server_url(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.query,
            "",
        )
    )
