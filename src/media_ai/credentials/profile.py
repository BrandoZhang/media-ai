"""Provider profiles: named config bundles so one CLI can route calls to different
endpoints / projects / tenants — e.g. an image endpoint on account A and a video
endpoint on account B, each with its own key and (optionally) base URL.

A profile binds: `provider`, a default `model`, an optional `base_url`, and a
`credential` **reference** (or an ordered *list* of references for fallback). It is
*non-secret* — a reference is `cred://<name>` (a named credential from
credentials.toml), `env://VAR`, `op://…`, a Vault path, an ARN, … resolved lazily
through the same machinery as the credential chain, never a raw key — so the config
file is safe to share and the trust boundary is unchanged (the CLI passes a profile
*name*; the key materializes only inside the adapter's request builder).

Profiles live in `~/.config/media-ai/config.toml` (override with `$MEDIA_CONFIG_FILE`)
and are selected with `--provider-profile NAME` or `$MEDIA_PROFILE`:

    [profiles.prod_video]
    provider   = "volc"
    model      = "ep-20260214051115-zrbtw"
    base_url   = "https://ark.cn-beijing.volces.com/api/v3"   # optional
    credential = "cred://volc_account_b"                      # a named credential

    [profiles.prod_video_ha]
    provider   = "volc"
    model      = "ep-20260214051115-zrbtw"
    # Ordered fallback: the first reference that resolves at call time wins, so you
    # can configure more keys than you routinely use and degrade gracefully.
    credential = ["cred://volc_account_b", "cred://volc_shared", "env://ARK_API_KEY"]
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from ..core.logging import get_logger
from .resolver import CredentialProvider
from .secret import Credential, Secret
from .stores import _REFERENCE_PREFIXES, resolve_reference, try_resolve_reference


@dataclass
class Profile:
    name: str
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    # A reference (cred://…, env://…, op://…) or an ordered list of them; never a raw
    # value. A list is tried in order at call time (first that resolves wins).
    credential: str | list[str] | None = None


def config_path() -> Path:
    return Path(os.getenv("MEDIA_CONFIG_FILE", "~/.config/media-ai/config.toml")).expanduser()


def _looks_like_reference(cred: str) -> bool:
    """Whether ``cred`` is a credential *reference* rather than a raw key.

    Mirrors :func:`resolve_reference`, which accepts both the ``scheme://…`` form
    (``cred://``, ``env://``, ``op://``, …) and the bare ``scheme:…`` form used by
    recognized secret managers (e.g. ``arn:aws:secretsmanager:…``). Anything else is
    treated as a raw key and refused, so a key never reaches the resolver.
    """
    return "://" in cred or cred.startswith(_REFERENCE_PREFIXES)


def _validate_references(name: str, cred: str | list[str]) -> list[str]:
    """Validate that every entry in ``cred`` is a reference (not a raw key) and return
    the list form. A raw key in the shareable config file is refused."""
    refs = [cred] if isinstance(cred, str) else list(cred)
    for r in refs:
        if not isinstance(r, str) or not _looks_like_reference(r):
            raise MediaError(
                f"profile {name!r}: `credential` must be a reference (e.g. cred://<name>, "
                "env://VAR, op://…, or arn:aws:secretsmanager:…), not a raw key — put raw "
                "keys in credentials.toml as [credentials.<name>]",
                category=ErrorCategory.AUTH,
            )
    return refs


def load_profile(name: str) -> Profile:
    """Load ``[profiles.<name>]`` from the config file. Raises a clear CLI/AUTH
    error if the file/profile is missing or a raw key is used instead of a reference."""
    path = config_path()
    if not path.is_file():
        raise MediaError(
            f"profile {name!r} requested but no config file at {path}", category=ErrorCategory.CLI
        )
    import tomllib  # py311+

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = (data.get("profiles") or {}).get(name)
    if section is None:
        raise MediaError(f"profile {name!r} not found in {path}", category=ErrorCategory.CLI)
    cred = section.get("credential")
    if cred is not None:
        _validate_references(name, cred)  # refuse a raw key early, with a clear message
    return Profile(
        name=name, provider=section.get("provider"), model=section.get("model"),
        base_url=section.get("base_url"), credential=cred,
    )


class ProfileCredentialProvider(CredentialProvider):
    """Resolve a profile's credential reference(s); delegate to a fallback chain when
    the profile declares no credential of its own (so `credential`-less profiles
    still get the normal env/keychain/… resolution, e.g. `ARK_API_KEY`).

    A single reference is resolved **strictly** — an absent source is an error, never
    a silent jump to a different account's key. A *list* is an explicit opt-in to
    fallback: each reference is tried in order and the first that resolves wins; if
    the whole list is exhausted, the error names what was tried.
    """

    def __init__(self, profile: Profile, fallback: CredentialProvider) -> None:
        self.profile = profile
        self.fallback = fallback

    def resolve(self, provider: str) -> Credential:
        cred = self.profile.credential
        if not cred:  # None or empty -> normal chain (e.g. ARK_API_KEY)
            return self.fallback.resolve(provider)
        source = f"profile:{self.profile.name}"
        if isinstance(cred, str):
            return Secret(resolve_reference(cred), provider=provider, source=source)
        # Ordered fallback list: first reference that resolves at call time wins.
        for ref in cred:
            value = try_resolve_reference(ref)
            if value:
                return Secret(value, provider=provider, source=source)
            get_logger().warning(
                "profile %r: credential %s did not resolve; trying next fallback", self.profile.name, ref
            )
        raise MediaError(
            f"profile {self.profile.name!r}: none of the fallback credentials resolved "
            f"({', '.join(cred)}); check credentials.toml / environment",
            category=ErrorCategory.AUTH,
            provider=provider,
        )
