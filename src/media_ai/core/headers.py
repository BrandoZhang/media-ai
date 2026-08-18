"""Extra HTTP headers one call carries — what may be set, and what it may say.

A request id is the case this exists for. A pipeline fanning out a run wants its own id
on each request (``x-request-id``, or whatever the provider reads), so a generation that
comes back wrong can be found afterwards in the provider's own logs. That is a property
of *this call*: it changes every time, nothing can derive it, and no configuration could
hold it.

Three rules, and each is a failure this would otherwise have:

* **A header is bytes on a wire, not a string.** A newline in a value splits the request
  in two, which is header injection; a non-ASCII character does not survive the latin-1
  encoding the standard library applies. Both are refused here rather than raising from
  inside ``http.client``, about a value it cannot name.
* **It may not be the credential, and it may not be framing.** The key is named by the
  binding (``credential = "env://…"``); ``Content-Type``, ``Accept``, ``Content-Length``
  and ``Host`` are decided per request by the transport; and ``X-Media-*`` is the
  namespace a credential broker routes through. The first two would be *silently* lost
  under the headers the adapter sets — a setting that quietly does nothing is worse than
  a refusal — and the third would be neither lost nor ignored, which is worse still.
* **A value is text.** Nothing is resolved and nothing should need to be: this is not a
  place for a secret, because ``argv`` is readable by every process on the machine.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .errors import ErrorCategory, MediaError

__all__ = ["RESERVED_HEADERS", "RESERVED_PREFIX", "parse_headers", "split_header_argument"]

#: RFC 9110 §5.1 ``field-name``: a token, so the name can go on a wire unquoted.
_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")

#: What a value may hold: visible ASCII plus space and tab (RFC 9110 §5.5).
_VALUE = re.compile(r"[\t\x20-\x7e]*")

#: Refused, with the reason — "reserved" alone leaves the reader guessing whether it is a
#: bug. The provider's *own* auth header is not here: it differs per binding, so it is
#: checked where the headers about to be sent are known (``providers/_base.py``).
RESERVED_HEADERS: dict[str, str] = {
    "authorization": "the key is named by the binding's `credential`, never passed here",
    "proxy-authorization": "the key is named by the binding's `credential`, never passed here",
    "host": "the transport sets this from the URL",
    "content-type": "the transport sets this from the body it is about to send",
    "content-length": "the transport sets this from the body it is about to send",
    "transfer-encoding": "the transport decides how the body is framed",
    "connection": "hop-by-hop: it describes this connection, not the request",
    "accept": "the transport asks for the response shape each endpoint answers with",
}

#: Reserved as a *prefix*, because this is the namespace a brokered credential writes
#: into (``X-Media-Provider``/``-Upstream``/``-Session``) and the marker an adapter reads
#: back to tell a brokered call from a direct one. A caller who could set one would send
#: the broker's own routing headers upstream, and make a binding holding a real key
#: describe itself as brokered — which is a refusal on the paths a broker cannot carry.
RESERVED_PREFIX = "x-media-"


def _fail(message: str, *, code: str) -> MediaError:
    return MediaError(message, category=ErrorCategory.VALIDATION, code=code)


def split_header_argument(argument: str) -> tuple[str, str]:
    """``"Name: value"`` → ``("Name", "value")``, the spelling curl and HTTP itself use.

    Only the first colon separates, so a value with one in it — a URL, a timestamp —
    survives. Leading whitespace is dropped: ``Name: value`` and ``Name:value`` are the
    same header.
    """
    name, sep, value = argument.partition(":")
    if not sep:
        raise _fail(f"--header expects 'Name: value', got {argument!r}", code="header_name_invalid")
    return name.strip(), value.lstrip(" \t")


def parse_headers(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Validate the headers this call asked for, or raise naming the one at fault.

    Takes the **pairs**, in the order they were given, rather than a mapping built from
    them. A caller repeating one name (``--header 'x-request-id: a' --header
    'x-request-id: b'``) has said something contradictory and gets told so; building the
    dict first would collapse the two and send the last quietly — while the *same*
    mistake in different case reached the refusal below, which is an inconsistency with
    no defensible reading.
    """
    seen: dict[str, str] = {}
    headers: dict[str, str] = {}
    for name, value in pairs:
        if not _NAME.fullmatch(name):
            raise _fail(f"--header: {name!r} is not a valid HTTP header name", code="header_name_invalid")
        # Field names are case-insensitive, so two spellings are one header with two
        # values — and which one survived would come down to dict order.
        folded = name.lower()
        if folded in seen:
            raise _fail(f"--header: {name!r} and {seen[folded]!r} are the same header",
                        code="header_duplicated")
        seen[folded] = name
        if folded in RESERVED_HEADERS:
            raise _fail(f"--header: {name!r} cannot be set — {RESERVED_HEADERS[folded]}",
                        code="header_reserved")
        if folded.startswith(RESERVED_PREFIX):
            raise _fail(
                f"--header: {name!r} cannot be set — {RESERVED_PREFIX}* belongs to the credential broker",
                code="header_reserved",
            )
        if not _VALUE.fullmatch(value):
            raise _fail(
                f"--header: the value of {name!r} holds a character no header can carry "
                f"(a line break, or a byte outside ASCII)",
                code="header_value_invalid",
            )
        headers[name] = value
    return headers
