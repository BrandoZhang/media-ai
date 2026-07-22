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
    e = dict(os.environ)
    e["MEDIA_PROVIDER"] = "mock"
    e["MEDIA_USAGE_LOG"] = str(tmp_path / "usage.jsonl")
    for k in ("ARK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        e.pop(k, None)
    return e


def test_dispatcher_lists_groups():
    proc = subprocess.run([sys.executable, "-m", "media_ai"], capture_output=True, text=True)
    assert proc.returncode != 0
    listing = (proc.stdout + proc.stderr).lower()
    for name in ("image", "video", "concat", "job", "capabilities", "usage"):
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


def test_full_storyboard_pipeline(env, tmp_path):
    w = tmp_path
    ref = json_out(run(env, "image", "generate", "--prompt", "astronaut", "--output", str(w / "ref.png"), "--seed", "7"))
    assert ref["ok"]
    edit = json_out(run(env, "image", "edit", "--reference", str(w / "ref.png"), "--prompt", "low angle",
                        "--output", str(w / "ref2.png")))
    assert edit["ok"] and edit["operation"] == "image.edit"
    shot1 = json_out(run(env, "video", "generate", "--prompt", "turns", "--first-frame", str(w / "ref.png"),
                         "--output", str(w / "s1.mp4"), "--duration", "1", "--resolution", "480p",
                         "--return-last-frame", "true"))
    assert shot1["extra_paths"], "return-last-frame should emit a frame"
    shot2 = json_out(run(env, "video", "generate", "--prompt", "twin suns", "--output", str(w / "s2.mp4"),
                         "--duration", "1", "--resolution", "480p"))
    assert shot2["ok"]
    final = json_out(run(env, "concat", "--inputs", json.dumps([str(w / "s1.mp4"), str(w / "s2.mp4")]),
                         "--output", str(w / "final.mp4")))
    assert (w / "final.mp4").is_file() and final["bytes"] > 0
    totals = json_out(run(env, "usage"))["totals"]
    assert totals["total_tokens"] > 0 and totals["images_generated"] >= 2 and totals["video_seconds"] >= 2


def test_async_job_roundtrip_offline(env, tmp_path):
    out = tmp_path / "async.mp4"
    handle = json_out(run(env, "video", "generate", "--prompt", "wave", "--output", str(out),
                          "--duration", "1", "--wait", "false"))
    assert handle["status"] == "queued"
    jid = handle["job"]["id"]
    done = json_out(run(env, "job", "query", "--provider", "mock", "--id", jid, "--output", str(out)))
    assert done["status"] == "succeeded" and out.is_file()


def test_capabilities_discovery(env):
    res = json_out(run(env, "capabilities", "--provider", "openai"))
    models = {m["model"] for m in res["providers"][0]["models"]}
    assert "gpt-image-2" in models


def test_unsupported_option_exits_3_with_json(env, tmp_path):
    proc = run(env, "image", "generate", "--prompt", "p", "--output", str(tmp_path / "x.png"),
               "--provider", "volc", "--model", "doubao-seedream-4-5-251128", "--background", "transparent", expect=3)
    err = json_out(proc)
    assert err["ok"] is False and err["error"]["category"] == "unsupported"
    assert not (tmp_path / "x.png").exists()


def test_missing_credentials_exits_4(env, tmp_path):
    proc = run(env, "image", "generate", "--prompt", "p", "--output", str(tmp_path / "x.png"),
               "--provider", "volc", "--model", "doubao-seedream-4-5-251128", "--size", "2560x1440", expect=4)
    assert json_out(proc)["error"]["category"] == "auth"


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
