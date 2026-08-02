import base64
import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx2


@dataclass
class PlanWiseAuth:
    base_url: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)

    async def get_access_token(self) -> str:
        if time.time() < _expires_at(self.access_token) - 30:
            return self.access_token

        async with httpx2.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
            cookies={"refreshToken": self.refresh_token},
        ) as client:
            response = await client.post("/api/auth/refresh")
        self.access_token, self.refresh_token = _tokens(response)
        return self.access_token


async def login_planwise(
    mcp_url: str,
    username: str,
    password: str,
) -> PlanWiseAuth:
    """Log in to PlanWise and keep the tokens needed by MCP."""
    if not username or not password:
        raise ValueError("PlanWise username and password are required")

    parsed = urlsplit(mcp_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    async with httpx2.AsyncClient(base_url=base_url, timeout=10.0) as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )

    access_token, refresh_token = _tokens(response)
    return PlanWiseAuth(base_url, access_token, refresh_token)


def _tokens(response: httpx2.Response) -> tuple[str, str]:
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if response.is_error:
        message = body.get("message") if isinstance(body, dict) else None
        raise ValueError(
            message if isinstance(message, str) else "PlanWise login failed"
        )
    data = body.get("data") if isinstance(body, dict) else None
    token = data.get("accessToken") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise ValueError("PlanWise response has no access token")
    refresh_token = response.cookies.get("refreshToken")
    if not refresh_token:
        raise ValueError("PlanWise response has no refresh token")
    return token, refresh_token


def _expires_at(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        expires_at = json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid PlanWise access token") from error
    if not isinstance(expires_at, int):
        raise ValueError("Invalid PlanWise access token expiry")
    return expires_at
