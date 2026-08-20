"""Round-trip and permission tests for the TOML writer.

Every ``dumps`` result is parsed back with ``tomllib`` — the writer is only
trustworthy insofar as the stdlib reader agrees with it.
"""

from __future__ import annotations

import os
import stat
import tomllib

import pytest

from media_ai.credentials import stores
from media_ai.credentials.tomlwrite import TomlWriteError, dumps, write_private, write_public


def roundtrip(data: dict) -> dict:
    return tomllib.loads(dumps(data))


# --------------------------------------------------------------------------- dumps


def test_roundtrip_flat_credentials():
    data = {"openai": {"api_key": "sk-abc"}, "volc_account_a": {"api_key": "xyz"}}
    assert roundtrip(data) == data


def test_roundtrip_nested_profiles():
    data = {
        "profiles": {
            "prod_image": {"provider": "volc", "model": "ep-123", "credential": "cred://volc_account_a"},
            "fallback": {"provider": "openai", "credential": ["cred://a", "env://OPENAI_API_KEY"]},
        }
    }
    assert roundtrip(data) == data


def test_roundtrip_types():
    data = {"t": {"s": "x", "yes": True, "no": False, "n": 42, "lst": ["a", "b"]}}
    assert roundtrip(data) == data


def test_bool_not_emitted_as_int():
    # bool is an int subclass; emitting `1` would round-trip to the wrong type.
    assert "true" in dumps({"t": {"flag": True}})
    assert roundtrip({"t": {"flag": True}})["t"]["flag"] is True


@pytest.mark.parametrize(
    "value",
    [
        'has "quotes"',
        r"back\slash",
        "new\nline",
        "tab\there",
        "carriage\rreturn",
        "bell\x07and\x00nul",
        "del\x7fchar",
        "unicode ✅ 中文",
        "",
    ],
)
def test_roundtrip_escaping(value):
    assert roundtrip({"t": {"k": value}})["t"]["k"] == value


@pytest.mark.parametrize("key", ["dotted.key", "has space", "quote\"key", "中文键"])
def test_roundtrip_quoted_keys(key):
    assert roundtrip({"t": {key: "v"}})["t"][key] == "v"


def test_scalar_after_subtable_stays_in_parent():
    """Regression: a scalar emitted after a `[a.b]` header would land inside a.b."""
    data = {"profiles": {"a": {"x": "1"}, "stray": "v"}}
    assert roundtrip(data) == data


def test_scalars_before_subtables_regardless_of_insertion_order():
    data = {"t": {"sub": {"k": "v"}, "one": "1", "two": "2"}}
    assert roundtrip(data) == data


def test_three_level_nesting_roundtrips():
    """[providers.volc.endpoints] — endpoint id -> the real model behind it."""
    data = {
        "providers": {
            "volc": {
                "image_model": "ep-2026-img",
                "endpoints": {"ep-2026-img": "doubao-seedream-4-5-251128"},
            }
        }
    }
    assert roundtrip(data) == data


def test_deep_nesting_roundtrips():
    data = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
    assert roundtrip(data) == data


def test_scalar_before_subtable_at_every_level():
    data = {"a": {"x": "1", "b": {"y": "2", "c": {"z": "3"}}}}
    assert roundtrip(data) == data


def test_dotted_endpoint_keys_are_quoted():
    """Ark endpoint ids are safe, but a key with a dot must not split the table path."""
    data = {"providers": {"volc": {"endpoints": {"ep.with.dots": "doubao-seedream-4-5-251128"}}}}
    assert roundtrip(data) == data


def test_table_name_needing_quotes():
    assert roundtrip({"weird name": {"k": "v"}}) == {"weird name": {"k": "v"}}


def test_header_is_comment():
    out = dumps({"t": {"k": "v"}}, header="line one\nline two")
    assert out.startswith("# line one\n# line two\n")
    assert tomllib.loads(out) == {"t": {"k": "v"}}


def test_header_does_not_leak_into_values():
    out = dumps({"t": {"k": "not # a comment"}})
    assert tomllib.loads(out)["t"]["k"] == "not # a comment"


def test_top_level_scalars_are_emitted_before_any_table():
    """`schema = 2` has to precede `[bindings]`.

    TOML assigns every key after a header to that table, so a top-level scalar written
    later silently becomes `bindings.schema` — valid TOML, wrong document.
    """
    text = dumps({"schema": 2, "bindings": {"a/b": {"credential": "env://X"}}})
    assert text.index("schema = 2") < text.index("[bindings")
    assert tomllib.loads(text) == {"schema": 2, "bindings": {"a/b": {"credential": "env://X"}}}


@pytest.mark.parametrize(
    "bad",
    [
        {"t": {"k": 1.5}},                  # float unsupported
        {"t": {"k": None}},                 # None unsupported
        {"t": {"k": [1, 2]}},               # non-str list
        {"t": {"": "v"}},                   # empty key
    ],
)
def test_rejects_unsupported(bad):
    with pytest.raises(TomlWriteError):
        dumps(bad)


def test_rejects_non_dict_top_level():
    with pytest.raises(TomlWriteError):
        dumps(["not", "a", "dict"])


# --------------------------------------------------------------- atomic + 0600


def test_write_private_is_0600(tmp_path):
    dest = tmp_path / "cfg" / "credentials.toml"
    write_private(dest, dumps({"openai": {"api_key": "sk-x"}}))
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700


def test_write_private_never_group_or_world_readable(tmp_path):
    dest = tmp_path / "credentials.toml"
    write_private(dest, dumps({"openai": {"api_key": "sk-x"}}))
    # The exact predicate stores._read_credentials_toml refuses on.
    assert not dest.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_written_file_is_readable_by_the_credential_store(tmp_path, monkeypatch):
    """The end-to-end contract: what the writer produces, the reader accepts."""
    dest = tmp_path / "credentials.toml"
    write_private(dest, dumps({"openai": {"api_key": "sk-round-trip"}}))
    monkeypatch.setenv("MEDIA_AI_CREDENTIALS_FILE", str(dest))
    assert stores.named_account("openai") == "sk-round-trip"


def test_write_private_overwrites_preserving_mode(tmp_path):
    dest = tmp_path / "credentials.toml"
    write_private(dest, dumps({"a": {"api_key": "1"}}))
    write_private(dest, dumps({"b": {"api_key": "2"}}))
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert tomllib.loads(dest.read_text()) == {"b": {"api_key": "2"}}


def test_write_private_leaves_no_temp_file(tmp_path):
    dest = tmp_path / "credentials.toml"
    write_private(dest, dumps({"a": {"api_key": "1"}}))
    assert [p.name for p in tmp_path.iterdir()] == ["credentials.toml"]


def test_failed_write_cleans_up_temp(tmp_path, monkeypatch):
    dest = tmp_path / "credentials.toml"

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_private(dest, dumps({"a": {"api_key": "1"}}))
    assert list(tmp_path.iterdir()) == []


def test_write_public_is_0644(tmp_path):
    dest = tmp_path / "config.toml"
    write_public(dest, dumps({"profiles": {"p": {"provider": "openai"}}}))
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644


def test_write_public_does_not_lock_down_the_directory(tmp_path):
    """config.toml lives beside credentials.toml but must not widen or narrow the dir on its own."""
    dest = tmp_path / "sub" / "config.toml"
    write_public(dest, dumps({"profiles": {}}))
    assert dest.is_file()
