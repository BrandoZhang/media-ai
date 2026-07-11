"""Provider profiles: named config bundles so one CLI can route calls to different
endpoints / projects / tenants — e.g. an image endpoint on account A and a video
endpoint on account B, each with its own key and (optionally) base URL.

A profile binds: `provider`, a default `model`, an optional `base_url`, and a
`credential` **reference**. It is *non-secret* — `credential` is a reference
(`env://VAR`, `op://…`, a Vault path, …) resolved lazily through the same machinery
as the credential chain, never a raw key — so the config file is safe to share and
the trust boundary is unchanged (the CLI passes a profile *name*; the key
materializes only inside the adapter's request builder).

Profiles live in `~/.config/media-ai/config.toml` (override with `$MEDIA_CONFIG_FILE`)
and are selected with `--provider-profile NAME` or `$MEDIA_PROFILE`:

    [profiles.prod_video]
    provider   = "volc"
    model      = "ep-20260214051115-zrbtw"
    base_url   = "https://ark.cn-beijing.volces.com/api/v3"   # optional
    credential = "env://ARK_PROD_VIDEO_KEY"                    # optional reference
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from .resolver import CredentialProvider
from .secret import Credential, Secret
from .stores import resolve_reference


@dataclass
class Profile:
    name: str
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    credential: str | None = None  # a reference (env://…, op://…), never a raw value


def config_path() -> Path:
    return Path(os.getenv("MEDIA_CONFIG_FILE", "~/.config/media-ai/config.toml")).expanduser()


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
    if cred is not None and "://" not in cred:
        raise MediaError(
            f"profile {name!r}: `credential` must be a reference (e.g. env://VAR or op://…), "
            "not a raw key — put raw keys in credentials.toml",
            category=ErrorCategory.AUTH,
        )
    return Profile(
        name=name, provider=section.get("provider"), model=section.get("model"),
        base_url=section.get("base_url"), credential=cred,
    )


class ProfileCredentialProvider(CredentialProvider):
    """Resolve a profile's credential reference; delegate to a fallback chain when
    the profile declares no credential of its own (so `credential`-less profiles
    still get the normal env/keychain/… resolution, e.g. `ARK_API_KEY`)."""

    def __init__(self, profile: Profile, fallback: CredentialProvider) -> None:
        self.profile = profile
        self.fallback = fallback

    def resolve(self, provider: str) -> Credential:
        if self.profile.credential:
            value = resolve_reference(self.profile.credential)
            return Secret(value, provider=provider, source=f"profile:{self.profile.name}")
        return self.fallback.resolve(provider)
