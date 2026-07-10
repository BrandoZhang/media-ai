"""Base class for HTTP-backed providers.

Centralizes credential handling and the trust boundary: given a resolved
:class:`Credential`, it produces the ``(base_url, auth_headers)`` a request needs.
For a :class:`Secret` the real key is revealed into a local header (single-process
mode). For a :class:`BrokeredHandle` the request is aimed at the broker with only a
session token — the broker injects the real credential at egress, so this process
never holds the provider key (managed/hosted mode). Adapters call
:meth:`_prepare` and never branch on the credential type themselves.
"""

from __future__ import annotations

from ..core.errors import MediaError
from ..core.provider import Provider
from ..credentials.secret import BrokeredHandle, Credential
from ._http import HttpClient


class HttpProvider(Provider):
    base_url: str = ""
    auth_scheme: str = "bearer"  # "bearer" | "x-goog"
    http_timeout: float = 120.0

    def _auth(self, cred: Credential) -> tuple[str, dict]:
        if isinstance(cred, BrokeredHandle):
            # Route through the broker; it injects the real key. We send only the
            # session token + which upstream to forward to. No secret here.
            return cred.endpoint.rstrip("/"), {
                "X-Media-Provider": self.name,
                "X-Media-Upstream": self.base_url,
                "X-Media-Session": cred.token,
            }
        key = cred.reveal()
        if self.auth_scheme == "x-goog":
            return self.base_url, {"x-goog-api-key": key}
        return self.base_url, {"Authorization": f"Bearer {key}"}

    def _prepare(self, **client_kw) -> tuple[HttpClient, dict]:
        """Resolve the credential (per call, for rotation) and return an
        :class:`HttpClient` bound to the right base URL plus the auth headers."""
        cred = self.credential()
        base, headers = self._auth(cred)
        client = HttpClient(
            base_url=base, provider=self.name, error_mapper=self._error,
            timeout=client_kw.pop("timeout", self.http_timeout), **client_kw,
        )
        return client, headers

    def _error(self, status: int, body: str) -> MediaError:  # overridable per provider
        return HttpClient(base_url=self.base_url, provider=self.name)._default_error(status, body)
