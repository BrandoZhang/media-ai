"""``--header`` — one call's own HTTP headers.

A request id is the case this exists for: a pipeline fanning out a run wants its id on
each request, so a generation that comes back wrong can be found afterwards in the
provider's logs. It changes every time, so nothing can configure it and nothing can
derive it — the caller names it.

What is worth testing is the boundaries, not "does a header arrive": it must not become
a second way to send a credential, it must not carry bytes that split the request, and
it must not be quietly dropped by a command that never opens a socket.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from conftest import PNG_1x1, bound

from media_ai.cli import image as image_mod
from media_ai.cli import video as video_mod
from media_ai.core.errors import MediaError
from media_ai.core.headers import split_header_argument
from media_ai.providers import _http


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


@pytest.fixture
def sent(monkeypatch):
    """Every request that reached the wire, as its headers. A real generation runs."""
    seen: list[dict] = []
    payload = json.dumps({"data": [{"b64_json": PNG_1x1}], "usage": {}}).encode()
    monkeypatch.setattr(
        _http.urllib.request, "urlopen",
        lambda req, timeout=None: (seen.append(dict(req.header_items())) or _Resp(payload)),
    )
    return seen


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from media_ai.core.config import Config, UserBinding, render_config

    path = tmp_path / "config.toml"
    path.write_text(render_config(Config(bindings={"openai/gpt-image-2": UserBinding(
        id="openai/gpt-image-2", credential="env://OPENAI_API_KEY")})), encoding="utf-8")
    monkeypatch.setenv("MEDIA_AI_CONFIG_FILE", str(path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return path


def generate(module, *argv) -> int:
    """One command, exactly as the console entry point runs it."""
    old, sys.argv = sys.argv, [f"media-ai {module.__name__.rsplit('.', 1)[-1]}", *argv]
    try:
        return module.main()
    except SystemExit as exc:
        return exc.code
    finally:
        sys.argv = old


def image(tmp_path, *extra) -> list[str]:
    return ["generate", "--binding", "openai/gpt-image-2", "--prompt", "a cat",
            "--output", str(tmp_path / "a.png"), *extra]


def header(headers: dict, name: str):
    """One header out of a sent request, matched the way HTTP matches: case-blind.

    ``urllib`` capitalizes what it is given (``x-request-id`` goes out as
    ``X-request-id``), which is its business and a difference no server may act on — but
    a test reading them exactly would be pinning the standard library's spelling.
    """
    return next((v for k, v in headers.items() if k.lower() == name.lower()), None)


# ------------------------------------------------------------------------ on the wire


def test_a_header_named_on_the_command_line_reaches_the_request(configured, sent, tmp_path, capsys):
    code = generate(image_mod, *image(tmp_path, "--header", "x-request-id: run-4417-shard-12"))
    assert code == 0, capsys.readouterr().out
    assert header(sent[0], "x-request-id") == "run-4417-shard-12"
    assert header(sent[0], "Authorization") == "Bearer sk-test"


def test_the_flag_repeats(configured, sent, tmp_path):
    """`--header 'A: B' --header 'X: Y'` — two headers, both sent."""
    generate(image_mod, *image(tmp_path, "--header", "x-request-id: r-1", "--header", "x-run: shard-12"))
    assert header(sent[0], "x-request-id") == "r-1"
    assert header(sent[0], "x-run") == "shard-12"


def test_a_value_with_a_colon_in_it_survives(configured, sent, tmp_path):
    generate(image_mod, *image(tmp_path, "--header", "x-request-id: 2026-08-18T04:59:10Z"))
    assert header(sent[0], "x-request-id") == "2026-08-18T04:59:10Z"


# ------------------------------------------------------------------------- refusals


@pytest.mark.parametrize("argument,code", [
    ("Authorization: Bearer sk-someone-elses", "header_reserved"),
    ("Content-Type: text/plain", "header_reserved"),
    ("Host: elsewhere.test", "header_reserved"),
    ("Accept: application/json", "header_reserved"),
    ("X-Media-Session: forged", "header_reserved"),
    ("x request id: a", "header_name_invalid"),
    ("x-request-id: a\r\nX-Injected: yes", "header_value_invalid"),
    ("x-request-id: 咖啡", "header_value_invalid"),
])
def test_what_a_header_may_not_be(configured, sent, tmp_path, capsys, argument, code):
    """The reasons differ — a credential boundary, a framing header the transport owns,
    bytes that would split the request in two — and so do the codes, because a caller
    branches on them. Nothing is sent in any of these cases."""
    exit_code = generate(image_mod, *image(tmp_path, "--header", argument))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert exit_code == 3 and out["error"]["code"] == code
    assert not sent


@pytest.mark.parametrize("second", ["x-request-id: b", "X-Request-Id: b"])
def test_one_header_named_twice_is_refused_rather_than_sent_twice(configured, sent, tmp_path, capsys, second):
    """Contradictory instructions, in either spelling — HTTP compares field names
    case-blind, so both are the same header named twice.

    The two used to end differently: validating a *dict* built from the arguments
    collapsed the identical pair before anything looked at it, so `a` was dropped and `b`
    sent quietly, while the case-differing pair was refused. Same mistake, two answers.
    """
    exit_code = generate(image_mod, *image(tmp_path, "--header", "x-request-id: a",
                                           "--header", second))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert exit_code == 3 and out["error"]["code"] == "header_duplicated"
    assert not sent


def test_a_forged_broker_marker_cannot_make_a_direct_call_look_brokered(monkeypatch):
    """`X-Media-*` is what a brokered credential writes and what an adapter reads back to
    tell the two apart — Gemini refuses an over-ceiling upload on the one path a broker
    cannot carry. A caller able to set it would send the broker's routing headers
    upstream *and* make a binding holding a real key describe itself as brokered.

    Refused twice: at the CLI edge, and here at the layer that sends them — this builds
    the binding by hand, which is what a caller reaching the library rather than the
    command line does.
    """
    from dataclasses import replace

    from media_ai.core.registry import build_adapter

    monkeypatch.setenv("MEDIA_TEST_KEY", "sk-test")
    adapter = build_adapter(replace(bound("gemini/nano-banana-2"),
                                    headers={"X-Media-Session": "forged"}))
    with pytest.raises(MediaError) as ei:
        adapter._prepare()
    assert ei.value.code == "header_reserved"


def test_a_header_argument_without_a_colon_is_refused():
    with pytest.raises(MediaError) as ei:
        split_header_argument("x-request-id run-1")
    assert ei.value.code == "header_name_invalid"


def test_the_header_this_binding_signs_with_is_refused_at_the_adapter(monkeypatch):
    """Gemini's key rides in `x-goog-api-key`, which no static list could know about —
    only the binding's own manifest does. Silently dropping it would be a flag that did
    nothing; silently winning would be a request sent with somebody else's key."""
    from dataclasses import replace

    from media_ai.core.registry import build_adapter

    monkeypatch.setenv("MEDIA_TEST_KEY", "sk-test")
    # What `common.call_headers` hands the adapter: the binding, carrying this call's.
    adapter = build_adapter(replace(bound("gemini/nano-banana-2"), headers={"X-Goog-Api-Key": "other"}))
    with pytest.raises(MediaError) as ei:
        adapter._prepare()
    assert ei.value.code == "header_reserved" and ei.value.exit_code == 3


def test_a_binding_that_speaks_no_http_refuses_it(tmp_path, capsys):
    """`video concat` runs ffmpeg. Accepting a header there and dropping it would leave a
    pipeline believing its request id was sent."""
    clip = tmp_path / "in.mp4"
    clip.write_bytes(b"not a video")
    code = generate(video_mod, "concat", "--inputs", str(clip), "--output", str(tmp_path / "out.mp4"),
                    "--binding", "local/ffmpeg", "--header", "x-request-id: a")
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 2 and out["error"]["code"] == "header_unsupported"
    assert out["error"]["details"]["transport"] == "local"


def test_the_commands_that_open_no_socket_have_no_such_flag(capsys):
    """A flag `doctor` accepted and ignored would be worse than one it does not have."""
    from media_ai.cli import doctor as doctor_mod

    assert generate(doctor_mod, "--header", "x-request-id: a") == 2  # argparse: unrecognized


def test_every_adapter_the_cli_builds_has_been_offered_the_call_headers():
    """Three commands resolve a binding for themselves — `bind`, `job` and `video
    concat` — and a fourth would forget. Read off the syntax tree, because "a --header
    accepted and dropped" is exactly the failure this flag must not have.

    `_verify` is the exception and stays one: a credential probe has no call whose id it
    would carry, so it takes no `--header` to drop.
    """
    from media_ai.cli import common as common_mod

    offenders = [
        f"{path.name}:{node.name}"
        for path in sorted(Path(common_mod.__file__).parent.glob("*.py")) if path.name != "_verify.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
        and "build_adapter(" in ast.unparse(node) and "call_headers(" not in ast.unparse(node)
    ]
    assert not offenders, f"these build an adapter without merging --header: {offenders}"
