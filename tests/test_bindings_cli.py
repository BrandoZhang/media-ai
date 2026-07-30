"""``media-ai bindings`` and ``media-ai config`` — the commands that write the config.

These are the only two that *edit* what a machine can call, so between them they own
the answer to "which key does this call use?". They were the refactor's new surface and
arrived without tests; the guards below are the ones worth having: a raw key must never
land in the shareable file, a default must name a binding that is both configured and
able to serve the scene, and ``--extends`` must produce a second callable binding rather
than a copy that drifts.

Driven through ``main()`` with a patched argv so the JSON contract and the exit code are
exercised too, not just the helper underneath.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from media_ai.cli import bindings as bindings_mod
from media_ai.cli import config as config_mod


def run(mod, *argv, expect=0, capsys=None):
    """Invoke one command exactly as the console entry point does."""
    import sys

    argv = [f"media-ai {mod.__name__.rsplit('.', 1)[-1]}", *argv]
    old, sys.argv = sys.argv, argv
    try:
        code = mod.main()
    except SystemExit as exc:  # argparse parse failure
        code = exc.code
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert code == expect, f"{argv} -> {code}: {out}"
    return json.loads(out.strip().splitlines()[-1])


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MEDIA_CONFIG_FILE", str(path))
    return path


def read(path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ bindings add


def test_add_writes_a_reference_and_never_a_key(cfg, capsys):
    res = run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    assert res["ok"] and res["binding"] == "openai/gpt-image-2"
    written = read(cfg)
    assert written["bindings"]["openai/gpt-image-2"]["credential"] == "env://OPENAI_API_KEY"
    assert written["schema"] == 2


def test_ark_endpoint_id_is_stored_by_its_vendor_name_and_resolves_to_model(cfg, capsys):
    endpoint = "ep-20260728-seedream50"
    run(
        bindings_mod, "add", "volc-ark/seedream-5.0", "--credential", "env://ARK_API_KEY",
        "--endpoint-id", endpoint, "--base-url", "https://ark.cn-beijing.volces.com/api/v3", capsys=capsys,
    )
    entry = read(cfg)["bindings"]["volc-ark/seedream-5.0"]
    assert entry["endpoint_id"] == endpoint and "model_id" not in entry
    listed = {b["binding"]: b for b in run(bindings_mod, "list", capsys=capsys)["bindings"]}
    assert listed["volc-ark/seedream-5.0"]["model_id"] == endpoint


def test_ark_endpoint_id_shape_is_checked_before_writing_config(cfg, capsys):
    res = run(
        bindings_mod, "add", "volc-ark/seedream-5.0", "--credential", "env://ARK_API_KEY",
        "--endpoint-id", "seedream-5", expect=2, capsys=capsys,
    )
    assert res["error"]["code"] == "endpoint_id_invalid"
    assert not cfg.exists()


def test_ark_endpoint_id_replaces_legacy_model_id_without_ambiguity(cfg, capsys):
    cfg.write_text(
        'schema = 2\n\n[bindings."volc-ark/seedream-5.0"]\n'
        'model_id = "ep-20260728-legacy"\ncredential = "env://ARK_API_KEY"\n', encoding="utf-8",
    )
    run(
        bindings_mod, "add", "volc-ark/seedream-5.0", "--credential", "env://ARK_API_KEY",
        "--endpoint-id", "ep-20260728-current", capsys=capsys,
    )
    entry = read(cfg)["bindings"]["volc-ark/seedream-5.0"]
    assert entry["endpoint_id"] == "ep-20260728-current" and "model_id" not in entry


def test_a_raw_key_is_refused_before_it_reaches_the_shareable_file(cfg, capsys):
    """`config.toml` is the file people paste into issues and commit to repos.

    The refusal has to happen here, at the only command that writes it — a check
    somewhere downstream would be a check after the secret was already on disk.
    """
    res = run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "sk-not-a-reference",
              expect=4, capsys=capsys)
    assert res["error"]["code"] == "credential_is_raw_key"
    assert not cfg.exists(), "nothing may be written on the path that rejects a key"
    assert "sk-not-a-reference" not in json.dumps(res), "the rejected key must not be echoed back"


def test_a_failure_carries_schema_version_like_every_other_stdout_object(cfg, capsys):
    """One schema for stdout, success and failure alike.

    A consumer validating the contract shouldn't need a second branch for the shape it
    gets when something went wrong — which is exactly when it can least afford to guess
    what it is holding.
    """
    from media_ai.core.result import SCHEMA_VERSION

    ok = run(bindings_mod, "add", "mock/mock", capsys=capsys)
    bad = run(bindings_mod, "add", "nope/nope", expect=9, capsys=capsys)
    assert bad["schema_version"] == ok["schema_version"] == SCHEMA_VERSION


def test_available_reports_the_env_candidates_a_manifest_declares(cfg, capsys):
    """``env`` is the manifest's list of conventional variables, never a resolved value.

    It is a declared ``tuple[str, ...]`` that defaults to empty, so a provider that
    names none — or a binding configured through ``cred://``/``keychain://``, which this
    field says nothing about — lists ``[]`` rather than breaking the listing.
    """
    from media_ai.core.registry import catalog

    entries = {b["binding"]: b for b in run(bindings_mod, "available", capsys=capsys)["bindings"]}
    declared = catalog().providers["openai"].auth.env
    assert entries["openai/gpt-image-2"]["env"] == list(declared)
    assert all(isinstance(b["env"], list) for b in entries.values())


def test_a_binding_that_needs_a_key_is_not_written_without_one(cfg, capsys):
    res = run(bindings_mod, "add", "openai/gpt-image-2", expect=4, capsys=capsys)
    assert res["error"]["code"] == "credential_missing"
    # The hint names the variable this provider's manifest declares, so it is runnable.
    assert res["error"]["hint"].startswith("media-ai bindings add openai/gpt-image-2 --credential env://")
    assert not cfg.exists()


def test_a_credential_free_binding_needs_no_credential(cfg, capsys):
    res = run(bindings_mod, "add", "mock/mock", capsys=capsys)
    assert res["ok"] and "mock/mock" in read(cfg)["bindings"]


def test_an_unknown_binding_is_refused_with_the_list(cfg, capsys):
    res = run(bindings_mod, "add", "nope/nope", expect=9, capsys=capsys)
    assert res["error"]["code"] == "unknown_binding"
    assert "openai/gpt-image-2" in res["error"]["details"]["declared"]


def test_extends_makes_a_second_account_a_first_class_binding(cfg, capsys):
    """One mechanism for a second account, a second region, and an opaque deployment id.

    The point is that the copy is not a copy: it inherits the declared capabilities of
    what it extends, so the two cannot drift, while keeping its own credential and its
    own wire id.
    """
    base = run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    res = run(bindings_mod, "add", "openai/gpt-image-2-eu",
              "--extends", "openai/gpt-image-2",
              "--credential", "env://OPENAI_EU_KEY",
              "--base-url", "https://eu.example/v1", capsys=capsys)
    assert res["ok"]
    entry = read(cfg)["bindings"]["openai/gpt-image-2-eu"]
    assert entry["extends"] == "openai/gpt-image-2"
    assert entry["credential"] == "env://OPENAI_EU_KEY" and entry["base_url"] == "https://eu.example/v1"
    # Inherited, not restated: exactly the scenes of what it extends, so a manifest
    # change reaches both and neither can drift from the other.
    assert res["scenes"] == base["scenes"]

    # And it is a real, separately addressable binding — its own row, its own endpoint.
    listed = {b["binding"]: b for b in run(bindings_mod, "list", capsys=capsys)["bindings"]}
    assert listed["openai/gpt-image-2-eu"]["base_url"] == "https://eu.example/v1"
    assert listed["openai/gpt-image-2-eu"]["credential"] == "env://OPENAI_EU_KEY"
    assert listed["openai/gpt-image-2"]["credential"] == "env://OPENAI_API_KEY"
    assert listed["openai/gpt-image-2-eu"]["model_id"] == listed["openai/gpt-image-2"]["model_id"]


# -------------------------------------------------------------- list / available


def test_available_offers_only_what_is_not_reachable_yet(cfg, capsys):
    before = {b["binding"] for b in run(bindings_mod, "available", capsys=capsys)["bindings"]}
    assert "openai/gpt-image-2" in before
    assert "mock/mock" not in before, "a binding needing no credential is already reachable"

    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    after = {b["binding"] for b in run(bindings_mod, "available", capsys=capsys)["bindings"]}
    assert "openai/gpt-image-2" not in after


def test_the_add_command_available_prints_is_the_one_that_works(cfg, capsys):
    """`available` hands out a command per binding; a wrong one is worse than none."""
    entry = next(b for b in run(bindings_mod, "available", capsys=capsys)["bindings"]
                 if b["binding"] == "openai/gpt-image-2")
    argv = entry["add"].split()
    assert argv[:2] == ["media-ai", "bindings"]
    res = run(bindings_mod, *argv[2:], capsys=capsys)
    assert res["ok"] and "openai/gpt-image-2" in read(cfg)["bindings"]


# ------------------------------------------------------------ config set-default


def test_a_group_expands_to_the_scenes_that_binding_actually_serves(cfg, capsys):
    """"images go to this one" is the decision people make; the stored form stays exact.

    Expanding is not the same as accepting everything under the group: a scene the
    binding does not serve is reported, not written, or the default would name a
    binding guaranteed to refuse.
    """
    run(bindings_mod, "add", "mock/mock", capsys=capsys)
    res = run(config_mod, "set-default", "video", "mock/mock", capsys=capsys)
    assert "video.text_to_video" in res["scenes"]
    assert "video.concat" not in res["scenes"], "mock does not serve concat"
    assert read(cfg)["defaults"]["video.text_to_video"] == "mock/mock"
    assert "video.concat" not in read(cfg)["defaults"]


def test_one_scene_can_be_set_on_its_own(cfg, capsys):
    run(bindings_mod, "add", "mock/mock", capsys=capsys)
    run(config_mod, "set-default", "image.image_to_image", "mock/mock", capsys=capsys)
    defaults = read(cfg)["defaults"]
    assert defaults == {"image.image_to_image": "mock/mock"}, "only the named scene moves"


def test_an_unconfigured_binding_cannot_become_a_default(cfg, capsys):
    res = run(config_mod, "set-default", "image", "openai/gpt-image-2", expect=2, capsys=capsys)
    assert res["error"]["code"] == "binding_not_configured"
    assert res["error"]["hint"].startswith("media-ai bindings add openai/gpt-image-2")


def test_a_binding_cannot_be_the_default_for_a_scene_it_does_not_serve(cfg, capsys):
    run(bindings_mod, "add", "mock/mock", capsys=capsys)
    res = run(config_mod, "set-default", "video.concat", "mock/mock", expect=3, capsys=capsys)
    assert res["error"]["code"] == "scene_not_supported"
    assert "video.text_to_video" in res["error"]["details"]["supported_scenes"]


def test_an_unknown_scene_lists_the_real_ones(cfg, capsys):
    run(bindings_mod, "add", "mock/mock", capsys=capsys)
    res = run(config_mod, "set-default", "image.inpaint", "mock/mock", expect=2, capsys=capsys)
    assert res["error"]["code"] == "unknown_scene"
    assert "image.image_to_image" in res["error"]["details"]["scenes"]
    assert "video" in res["error"]["details"]["groups"]


def test_show_reports_the_file_and_what_is_in_it(cfg, capsys):
    empty = run(config_mod, "show", capsys=capsys)
    assert empty["exists"] is False and empty["bindings"] == {} and empty["defaults"] == {}

    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    run(config_mod, "set-default", "image.text_to_image", "openai/gpt-image-2", capsys=capsys)
    shown = run(config_mod, "show", capsys=capsys)
    assert shown["exists"] is True
    assert shown["bindings"]["openai/gpt-image-2"]["credential"] == "env://OPENAI_API_KEY"
    assert shown["defaults"]["image.text_to_image"] == "openai/gpt-image-2"


def test_adding_a_binding_twice_replaces_rather_than_duplicates(cfg, capsys):
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OTHER_KEY", capsys=capsys)
    entries = read(cfg)["bindings"]
    assert entries["openai/gpt-image-2"]["credential"] == "env://OTHER_KEY"
    assert len(entries) == 1, "re-running must not accumulate; install is also the upgrade path"


def test_a_default_survives_adding_another_binding(cfg, capsys):
    """`add` rewrites the whole file, so it has to carry the defaults through."""
    run(bindings_mod, "add", "mock/mock", capsys=capsys)
    run(config_mod, "set-default", "image.text_to_image", "mock/mock", capsys=capsys)
    run(bindings_mod, "add", "openai/gpt-image-2", "--credential", "env://OPENAI_API_KEY", capsys=capsys)
    assert read(cfg)["defaults"]["image.text_to_image"] == "mock/mock"


# --------------------------------------------------- editing must not lose fields


def test_add_merges_into_an_existing_entry_instead_of_rebuilding_it(cfg, capsys):
    """Rotating a key must not un-configure the binding whose key is being rotated.

    `bindings add <id> --credential …` is the command every resolution error hints at,
    so it is the one most likely to be run against a binding that already exists. It
    used to rebuild the entry from the flags it happened to receive, deleting an
    account-specific `model_id` (an Ark `ep-…` endpoint), the `base_url` and the
    per-binding `options` — silently, and with no backup to recover them from.
    """
    cfg.write_text(
        'schema = 2\n\n'
        '[bindings."volc-ark/seedance-2.0"]\n'
        'model_id = "ep-my-private-endpoint"\n'
        'base_url = "https://ark.ap-southeast.volces.com/api/v3"\n'
        'credential = "env://ARK_API_KEY"\n'
        '[bindings."volc-ark/seedance-2.0".options]\n'
        'poll_timeout = 3600\n',
        encoding="utf-8",
    )
    res = run(bindings_mod, "add", "volc-ark/seedance-2.0", "--credential", "env://ARK_KEY_2", capsys=capsys)
    entry = read(cfg)["bindings"]["volc-ark/seedance-2.0"]
    assert entry["credential"] == "env://ARK_KEY_2", "the field that was asked for changes"
    assert entry["model_id"] == "ep-my-private-endpoint", "the endpoint id survives"
    assert entry["base_url"] == "https://ark.ap-southeast.volces.com/api/v3"
    assert entry["options"] == {"poll_timeout": 3600}
    assert res["backup"], "the previous file is kept"


def test_every_config_write_leaves_the_previous_file_behind(cfg, capsys):
    """`render_config` cannot round-trip comments, so an edit to one field rewrites the
    whole file — and a hand-written note explaining an endpoint choice is otherwise
    gone with no way back."""
    cfg.write_text('schema = 2\n\n# why: only this endpoint is enabled on our account\n'
                   '[bindings."mock/mock"]\n', encoding="utf-8")

    add = run(bindings_mod, "add", "mock/mock", capsys=capsys)
    assert "why: only this endpoint" in Path(add["backup"]).read_text(encoding="utf-8")

    default = run(config_mod, "set-default", "image.text_to_image", "mock/mock", capsys=capsys)
    assert default["backup"] and Path(default["backup"]).is_file()
