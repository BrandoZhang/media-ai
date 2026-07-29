"""End-to-end CLI tests: drive the tools the way an agent does — as processes.

Proves the console-script wiring, the JSON-on-stdout contract, category exit codes,
credential redaction, and the full storyboard pipeline offline on the mock provider.
Skipped automatically if ffmpeg/Pillow aren't available so the suite stays green on
a bare environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from conftest import have_media_stack

pytestmark = pytest.mark.skipif(not have_media_stack(), reason="needs Pillow + ffmpeg")


def run(env, *args, expect=0):
    proc = subprocess.run([sys.executable, "-m", "media_ai", *args], capture_output=True, text=True, env=env)
    assert proc.returncode == expect, f"{args} -> {proc.returncode}: {proc.stderr}"
    return proc


def json_out(proc):
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def env(tmp_path):
    """A machine configured the way `media-ai init` would leave it, pointed at mock.

    Written as real config rather than an env override because that *is* the mechanism
    now: a call naming no binding works only if a default names one. These end-to-end
    tests are where that claim gets proved.
    """
    from media_ai.core.config import Config, UserBinding, render_config
    from media_ai.core.registry import catalog
    from media_ai.core.scene import Scene

    # Which scenes go to the local backend is read from its manifest rather than listed:
    # `local/ffmpeg` serves everything the mock deliberately does not implement, and a
    # hardcoded list here would quietly point a new one at a backend that refuses it.
    local = catalog().get("local/ffmpeg").scenes
    config = tmp_path / "config.toml"
    config.write_text(render_config(Config(
        bindings={"mock/mock": UserBinding(id="mock/mock")},
        defaults={s.value: ("local/ffmpeg" if s in local else "mock/mock") for s in Scene},
    )), encoding="utf-8")

    e = dict(os.environ)
    e["MEDIA_CONFIG_FILE"] = str(config)
    e["MEDIA_USAGE_LOG"] = str(tmp_path / "usage.jsonl")
    for k in ("ARK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        e.pop(k, None)
    return e


def test_dispatcher_lists_groups():
    proc = subprocess.run([sys.executable, "-m", "media_ai"], capture_output=True, text=True)
    assert proc.returncode != 0
    listing = (proc.stdout + proc.stderr).lower()
    for name in ("image", "video", "job", "capabilities", "bindings", "config", "usage"):
        assert name in listing


def test_bad_flag_emits_json_error_on_stdout(env):
    # An argparse parse error must still produce the one-JSON-object failure contract on
    # stdout (category cli, exit 2), with the human-readable specifics on stderr.
    proc = run(env, "usage", "--bogus", expect=2)
    err = json_out(proc)
    assert err["ok"] is False and err["error"]["category"] == "cli"
    assert "bogus" in proc.stderr  # argparse detail goes to stderr, not stdout


def test_image_generate_contract(env, tmp_path):
    out = tmp_path / "ref.png"
    res = json_out(run(env, "image", "generate", "--prompt", "a red dune", "--output", str(out), "--size", "128x128"))
    assert res["ok"] and res["modality"] == "image" and res["provider"] == "mock"
    assert res["artifacts"][0]["bytes"] > 0 and out.is_file()
    assert res["usage"]["total_tokens"] > 0
    assert res["meta"]["binding"] == "mock/mock" and res["meta"]["scene"] == "image.text_to_image"


def test_edit_and_generate_with_a_reference_are_the_same_scene(env, tmp_path):
    """`edit` declares an intent; it does not select a scene.

    The references do that, so both commands run `image.image_to_image`. What `edit`
    buys is the guard: a caller who meant to transform an image and forgot the input
    gets an error rather than an unrelated new picture.
    """
    ref = tmp_path / "in.png"
    assert json_out(run(env, "image", "generate", "--prompt", "a dune", "--output", str(ref)))["ok"]

    scenes = set()
    for op in ("generate", "edit"):
        res = json_out(run(env, "image", op, "--prompt", "low angle", "--reference", str(ref),
                           "--output", str(tmp_path / f"{op}.png")))
        assert res["ok"]
        scenes.add(res["meta"]["scene"])
    assert scenes == {"image.image_to_image"}

    proc = run(env, "image", "edit", "--prompt", "low angle", "--output", str(tmp_path / "x.png"), expect=2)
    assert json_out(proc)["error"]["code"] == "missing_reference"


def test_full_storyboard_pipeline(env, tmp_path):
    w = tmp_path
    ref = json_out(run(env, "image", "generate", "--prompt", "astronaut", "--output", str(w / "ref.png"), "--seed", "7"))
    assert ref["ok"]
    edit = json_out(run(env, "image", "edit", "--reference", str(w / "ref.png"), "--prompt", "low angle",
                        "--output", str(w / "ref2.png")))
    assert edit["ok"] and edit["meta"]["scene"] == "image.image_to_image"
    shot1 = json_out(run(env, "video", "generate", "--prompt", "turns", "--first-frame", str(w / "ref.png"),
                         "--output", str(w / "s1.mp4"), "--duration", "1", "--resolution", "480p",
                         "--return-last-frame", "true"))
    assert [a for a in shot1["artifacts"] if a["role"] == "last_frame"], "return-last-frame should emit a frame"
    shot2 = json_out(run(env, "video", "generate", "--prompt", "twin suns", "--output", str(w / "s2.mp4"),
                         "--duration", "1", "--resolution", "480p"))
    assert shot2["ok"]
    final = json_out(run(env, "video", "concat", "--inputs", json.dumps([str(w / "s1.mp4"), str(w / "s2.mp4")]),
                         "--output", str(w / "final.mp4")))
    assert (w / "final.mp4").is_file() and final["artifacts"][0]["bytes"] > 0
    totals = json_out(run(env, "usage"))["totals"]
    assert totals["total_tokens"] > 0 and totals["images_generated"] >= 2 and totals["video_seconds"] >= 2


def test_async_job_roundtrip_offline(env, tmp_path):
    out = tmp_path / "async.mp4"
    handle = json_out(run(env, "video", "generate", "--prompt", "wave", "--output", str(out),
                          "--duration", "1", "--wait", "false"))
    assert handle["status"] == "queued"
    jid = handle["job"]["id"]
    assert handle["poll"].startswith("media-ai job query --binding mock/mock")
    done = json_out(run(env, "job", "query", "--binding", "mock/mock", "--id", jid, "--output", str(out)))
    assert done["status"] == "succeeded" and out.is_file()


def test_capabilities_discovery(env):
    res = json_out(run(env, "capabilities", "--provider", "openai"))
    entry = res["bindings"][0]
    assert entry["binding"] == "openai/gpt-image-2"
    assert entry["model_id"] == "gpt-image-2"
    # Declared, but nothing on this machine can call it — an agent choosing where to
    # send work needs both halves of that.
    assert entry["available"] is False
    assert entry["setup_hint"]


def test_capabilities_can_answer_who_serves_a_scene(env):
    res = json_out(run(env, "capabilities", "--scene", "video.extend"))
    assert {b["binding"] for b in res["bindings"]} == {"gemini/veo-3.1", "mock/mock"}


def test_capabilities_reports_what_is_reachable_right_now(env):
    res = json_out(run(env, "capabilities", "--configured"))
    assert {b["binding"] for b in res["bindings"]} == {"local/ffmpeg", "mock/mock"}
    assert res["defaults"]["image.text_to_image"] == "mock/mock"


def _with_ark(env, tmp_path):
    """Configure Seedream 4.5 against an env var that is deliberately not set.

    Enough to get *past* resolution and into validation — which is the point: an
    unsupported request must be rejected without a network call, and without a key.
    """
    from media_ai.core.config import Config, UserBinding, render_config

    path = tmp_path / "ark.toml"
    path.write_text(render_config(Config(bindings={
        "volc-ark/seedream-4.5": UserBinding(id="volc-ark/seedream-4.5", credential="env://ARK_API_KEY"),
    })), encoding="utf-8")
    return dict(env) | {"MEDIA_CONFIG_FILE": str(path)}


def test_unsupported_option_exits_3_with_json(env, tmp_path):
    proc = run(_with_ark(env, tmp_path), "image", "generate", "--prompt", "p",
               "--output", str(tmp_path / "x.png"), "--binding", "volc-ark/seedream-4.5",
               "--background", "transparent", expect=3)
    err = json_out(proc)
    assert err["ok"] is False and err["error"]["category"] == "unsupported"
    assert not (tmp_path / "x.png").exists()


def test_missing_credentials_exits_4(env, tmp_path):
    proc = run(_with_ark(env, tmp_path), "image", "generate", "--prompt", "p",
               "--output", str(tmp_path / "x.png"), "--binding", "volc-ark/seedream-4.5",
               "--size", "2560x1440", expect=4)
    err = json_out(proc)["error"]
    assert err["category"] == "auth" and err["code"] == "credential_unresolved"
    assert "ARK_API_KEY" in err["message"]


def test_an_unconfigured_binding_says_how_to_configure_it(env, tmp_path):
    proc = run(env, "image", "generate", "--prompt", "p", "--output", str(tmp_path / "x.png"),
               "--binding", "openai/gpt-image-2", expect=4)
    err = json_out(proc)["error"]
    assert err["code"] == "binding_not_configured"
    assert err["hint"].startswith("media-ai bindings add openai/gpt-image-2")


def test_naming_no_binding_with_no_default_refuses_rather_than_guessing(tmp_path):
    """The failure mode this refactor exists to remove: a placeholder that exits 0.

    Before, an unconfigured machine silently fell back to the offline mock and
    returned ok:true with a drawn placeholder — indistinguishable from success to
    the agent that asked for a video.
    """
    e = dict(os.environ)
    e["MEDIA_CONFIG_FILE"] = str(tmp_path / "empty.toml")
    e["MEDIA_USAGE_LOG"] = str(tmp_path / "usage.jsonl")
    proc = run(e, "video", "generate", "--prompt", "p", "--output", str(tmp_path / "v.mp4"), expect=2)
    err = json_out(proc)["error"]
    assert err["code"] == "no_default_binding"
    assert err["details"]["scene"] == "video.text_to_video"
    assert not (tmp_path / "v.mp4").exists()

    # ...and the refusal must not talk the caller into it either. `mock/mock` is the
    # only thing an unconfigured machine can reach, so it is listed truthfully — but a
    # hint is read as an instruction, and this one would install the placeholder as the
    # default for every video this agent ever asks for.
    assert "mock/mock" in err["details"]["available"]
    assert err["hint"] == "media-ai bindings available"
    assert "mock" not in err["hint"]


def test_secret_never_appears_in_output(env, tmp_path):
    env = dict(env)
    env["ARK_API_KEY"] = "sk-LEAKY-SECRET-abcdef123456"
    # will fail at the network call (bad key / no net), but must not echo the key
    proc = subprocess.run([sys.executable, "-m", "media_ai", "image", "generate", "--prompt", "p",
                           "--output", str(tmp_path / "x.png"), "--provider", "volc",
                           "--model", "doubao-seedream-4-5-251128", "--size", "2560x1440"],
                          capture_output=True, text=True, env=env)
    assert "LEAKY-SECRET" not in proc.stdout
    assert "LEAKY-SECRET" not in proc.stderr


# --------------------------------------------- dispatch-level machine contract


class TestDispatchHonoursTheContract:
    """The Agent Skills tell callers every command prints exactly one JSON object.
    Dispatch-level mistakes used to exit non-zero with an empty stdout, so an agent
    that mistyped a group got nothing parseable and no way to find out why."""

    @staticmethod
    def run(*argv):
        import json
        import subprocess
        import sys

        res = subprocess.run([sys.executable, "-m", "media_ai", *argv],
                             capture_output=True, text=True, timeout=60)
        try:
            return res, json.loads(res.stdout)
        except json.JSONDecodeError:
            return res, None  # --help is deliberately human text

    def test_unknown_group_emits_the_error_contract(self):
        res, out = self.run("definitely-not-a-group")
        assert res.returncode == 2
        assert out is not None and out["ok"] is False
        assert out["error"]["category"] == "cli"

    def test_unknown_group_stdout_is_exactly_one_line(self):
        res, _ = self.run("definitely-not-a-group")
        assert len(res.stdout.strip().splitlines()) == 1

    def test_unknown_group_names_the_valid_groups_on_stderr(self):
        res, _ = self.run("definitely-not-a-group")
        assert "image" in res.stderr and "capabilities" in res.stderr

    def test_no_arguments_emits_the_error_contract(self):
        res, out = self.run()
        assert res.returncode == 2
        assert out is not None and out["ok"] is False

    def test_help_stays_human_and_zero(self):
        """--help is a request, not a mistake — the one deliberate exemption."""
        res, _ = self.run("--help")
        assert res.returncode == 0
        assert res.stdout.startswith("usage:")

    def test_help_short_form_matches(self):
        res, _ = self.run("-h")
        assert res.returncode == 0 and res.stdout.startswith("usage:")

    def test_every_group_is_reachable(self):
        from media_ai.__main__ import _GROUPS

        for group in _GROUPS:
            res, _ = self.run(group, "--help")
            assert res.returncode == 0, f"{group} --help failed: {res.stderr[:200]}"
