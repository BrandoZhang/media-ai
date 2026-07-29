"""Offline mock provider — the default, deterministic, credential-free backend.

Draws placeholder images with Pillow (the prompt baked into the frame) and turns
them into short clips with ffmpeg. Deterministic given ``(prompt, seed)``; costs
nothing. Token counts are synthesized with the same formulas the real APIs
document, so the cost-tracking path is exercised offline.

Async is simulated statelessly: ``video generate --wait false`` returns a
:class:`JobHandle` whose id encodes the request, and ``job query --output`` decodes
it and renders deterministically — so the whole submit→poll→finalize path is
testable without a network.
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from ..core.errors import ErrorCategory, MediaError
from ..core.geometry import resolve_image_pixels, resolve_video_pixels
from ..core.adapter import Adapter
from ..core.scene import Scene, derive_scene
from ..core.result import Artifact, GenerationResult, JobHandle, JobStatus
from ..core.types import (
    DialogueRequest,
    ImageRequest,
    JobRef,
    MusicPlanRequest,
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    VideoRequest,
)
from ..media import audio, ffmpeg, pillow

_DEFAULT_IMG = (768, 432)
_MOCK_RENDER_H = 360  # render small (fast); bill at requested resolution


def _image_tokens(w: int, h: int, n: int) -> dict:
    per = (w * h) // 256  # matches Ark: output_tokens = images * floor(w*h/256)
    return {"generated_images": n, "output_tokens": per * n, "total_tokens": per * n}


def _video_tokens(w: int, h: int, seconds: int) -> dict:
    tok = (w * h * seconds) // 1024
    return {"completion_tokens": tok, "total_tokens": tok}


class MockAdapter(Adapter):

    def honoured_flags(self) -> frozenset[str]:
        # The offline backend imitates whatever a manifest declares.
        return frozenset({"group_output", "streaming", "grounding"})

    def supported_scenes(self) -> frozenset[Scene]:
        # Subtracted by name rather than derived from a group, because every entry is a
        # scene this adapter has no code for: `video.concat` and `animation.*` are real
        # ffmpeg work with real output, and imitating them would mean writing a file that
        # is not an animation of anything. `local/ffmpeg` needs no key and costs nothing,
        # so there is nothing for a placeholder to stand in for here.
        return frozenset(Scene) - {Scene.VIDEO_CONCAT, Scene.ANIMATION_FROM_VIDEO, Scene.ANIMATION_FROM_FRAMES}


    # ---- images ----------------------------------------------------------
    def generate_image(self, req: ImageRequest) -> GenerationResult:
        base = None
        if req.references:
            ref0 = req.references[0]
            if ref0.is_local:
                if not ref0.path().is_file():
                    raise MediaError(f"reference image not found: {ref0.raw}", category=ErrorCategory.IO)
                base = ref0.path()
        w, h = resolve_image_pixels(req.geometry, _DEFAULT_IMG)
        out = Path(req.output)
        pillow.ensure_parent(out)
        scene = derive_scene(req)
        title = f"mock {scene.value}"
        pillow.draw_caption_image(out, title=title, prompt=req.prompt, w=w, h=h,
                                  rgb=pillow.palette(req.prompt, req.seed), base_image=base)
        artifacts = [Artifact.from_path(out, "image", mime="image/png")]
        for i in range(2, req.count + 1):
            p = out.with_name(f"{out.stem}_{i}{out.suffix}")
            pillow.draw_caption_image(p, title=f"{title} ({i}/{req.count})", prompt=req.prompt, w=w, h=h,
                                      rgb=pillow.palette(req.prompt + str(i), req.seed), base_image=base)
            artifacts.append(Artifact.from_path(p, "image", mime="image/png", role="group"))
        usage = _image_tokens(w, h, req.count)
        self.record(scene, kind="image", **usage)
        return GenerationResult(
            modality="image", provider=self.name, model="mock",
            artifacts=artifacts, usage=usage,
            meta={"prompt": req.prompt, "seed": req.seed, "size": [w, h],
                  "refs": [r.raw for r in req.references]},
        )

    # ---- video -----------------------------------------------------------
    def generate_video(self, req: VideoRequest):
        if not req.wait:
            return JobHandle(provider=self.name, model="mock", id=_encode_job(req), output=str(req.output),
                             meta={"note": "mock async; poll with `media-ai job query`"})
        return self._render_video(req, str(req.output))

    def _render_video(self, req: VideoRequest, output: str) -> GenerationResult:
        out = Path(output)
        ffmpeg.ensure_parent(out)
        bw, bh = resolve_video_pixels(req.geometry)  # billed dims
        rh = min(_MOCK_RENDER_H, bh)
        rw = max(2, (bw * rh // bh) // 2 * 2)
        base = None
        if req.first_frame and req.first_frame.is_local and req.first_frame.path().is_file():
            base = req.first_frame.path()
        elif req.reference_images:
            r0 = req.reference_images[0]
            base = r0.path() if (r0.is_local and r0.path().is_file()) else None
        tag = _ref_tag(req)
        scene = derive_scene(req)
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "frame.png"
            pillow.draw_caption_image(frame, title=f"mock {scene.value}", prompt=req.prompt + tag,
                                      w=rw, h=rh, rgb=pillow.palette(req.prompt, req.seed), base_image=base)
            ffmpeg.image_to_clip(frame, out, seconds=req.duration or 5, fps=ffmpeg.DEFAULT_FPS, w=rw, h=rh)
        artifacts = [Artifact.from_path(out, "video", mime="video/mp4")]
        if req.return_last_frame:
            lf = out.with_name(f"{out.stem}_lastframe.png")
            pillow.draw_caption_image(lf, title="mock · last frame", prompt=req.prompt, w=rw, h=rh,
                                      rgb=pillow.palette(req.prompt, req.seed), base_image=base)
            artifacts.append(Artifact.from_path(lf, "frame", mime="image/png", role="last_frame"))
        seconds = req.duration or 5
        usage = _video_tokens(bw, bh, seconds)
        self.record(scene, kind="video", seconds=seconds, **usage)
        return GenerationResult(
            modality="video", provider=self.name, model="mock",
            artifacts=artifacts, usage=usage,
            meta={"prompt": req.prompt, "seconds": seconds, "seed": req.seed, "render_size": [rw, rh]},
        )

    # ---- audio (speech / dialogue) ---------------------------------------
    def generate_speech(self, req: SpeechRequest) -> GenerationResult:
        out = Path(req.output)
        secs = audio.tone_seconds(len(req.text))
        audio.write_tone_wav(out, secs)
        artifacts = [Artifact.from_path(out, "audio", mime="audio/wav")]
        if req.timestamps:
            artifacts.append(self._write_alignment(out, {"alignment": audio.fake_alignment(req.text, secs)}))
        usage = {"characters": len(req.text)}
        self.record(Scene.SPEECH_TEXT_TO_SPEECH, kind="audio", **usage)
        return GenerationResult(
            modality="audio", provider=self.name, model="mock",
            artifacts=artifacts, usage=usage,
            meta={"voice": req.voice or "mock-voice-a", "seconds": round(secs, 3), "timestamps": req.timestamps},
        )

    def generate_dialogue(self, req: DialogueRequest) -> GenerationResult:
        if not req.turns or not req.cast:
            raise MediaError("dialogue requires turns and a cast", category=ErrorCategory.VALIDATION, provider=self.name)
        out = Path(req.output)
        total_chars = sum(len(t.text) for t in req.turns)
        secs = audio.tone_seconds(total_chars)
        audio.write_tone_wav(out, secs)
        artifacts = [Artifact.from_path(out, "audio", mime="audio/wav")]
        if req.timestamps:
            payload = {"alignment": audio.fake_alignment("".join(t.text for t in req.turns), secs),
                       "voice_segments": _mock_voice_segments(req.turns, req.cast, secs)}
            artifacts.append(self._write_alignment(out, payload))
        usage = {"characters": total_chars}
        self.record(Scene.SPEECH_DIALOGUE, kind="audio", **usage)
        return GenerationResult(
            modality="audio", provider=self.name, model="mock",
            artifacts=artifacts, usage=usage,
            meta={"voices": req.voices(), "instruction": req.instruction, "seconds": round(secs, 3),
                  "timestamps": req.timestamps},
        )

    def _write_alignment(self, out: Path, payload: dict) -> Artifact:
        sidecar = out.with_suffix(out.suffix + ".timestamps.json")
        sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return Artifact.from_path(sidecar, "timestamps", mime="application/json", role="alignment")

    # ---- music / sound ---------------------------------------------------
    def generate_music(self, req: MusicRequest) -> GenerationResult:
        if bool(req.prompt) == bool(req.composition_plan):
            raise MediaError("music requires exactly one of a prompt or a composition plan",
                             category=ErrorCategory.VALIDATION, provider=self.name)
        out = Path(req.output)
        if req.composition_plan is not None:
            secs = sum(s.get("duration_ms", 0) for s in req.composition_plan.get("sections", [])) / 1000 or 10.0
        else:
            secs = (req.duration_ms or 10000) / 1000
        secs = min(secs, 30.0)  # keep the offline tone short
        audio.write_tone_wav(out, secs, freq=330.0)
        artifacts = [Artifact.from_path(out, "audio", mime="audio/wav")]
        if req.detailed:
            sidecar = out.with_suffix(out.suffix + ".metadata.json")
            sidecar.write_text(json.dumps(_mock_plan(req.prompt or "composition"), ensure_ascii=False, indent=2),
                               encoding="utf-8")
            artifacts.append(Artifact.from_path(sidecar, "metadata", mime="application/json", role="metadata"))
        self.record(derive_scene(req), kind="audio")
        return GenerationResult(modality="audio", provider=self.name, model="mock",
                                artifacts=artifacts, usage={},
                                meta={"prompt": req.prompt, "from_plan": req.composition_plan is not None,
                                      "seconds": round(secs, 3), "detailed": req.detailed})

    def generate_music_plan(self, req: MusicPlanRequest) -> GenerationResult:
        out = Path(req.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(_mock_plan(req.prompt, req.duration_ms), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        self.record(Scene.MUSIC_PLAN, kind="plan")
        return GenerationResult(modality="audio", provider=self.name, model="mock",
                                artifacts=[Artifact.from_path(out, "plan", mime="application/json")],
                                usage={}, meta={"prompt": req.prompt, "free": True})

    def generate_sound(self, req: SoundEffectRequest) -> GenerationResult:
        out = Path(req.output)
        secs = req.duration_seconds if req.duration_seconds is not None else min(2.0, max(0.5, len(req.text) / 20))
        audio.write_tone_wav(out, secs, freq=180.0)
        self.record(Scene.SOUND_TEXT_TO_SOUND, kind="audio", characters=len(req.text))
        return GenerationResult(modality="audio", provider=self.name, model="mock",
                                artifacts=[Artifact.from_path(out, "audio", mime="audio/wav")],
                                usage={"characters": len(req.text)}, meta={"text": req.text, "seconds": round(secs, 3)})

    # ---- jobs ------------------------------------------------------------
    def get_job(self, ref: JobRef, *, output: Path | None = None) -> JobStatus:
        req = _decode_job(ref.id)
        result = None
        if output is not None:
            result = self._render_video(req, str(output))
        return JobStatus(provider=self.name, model="mock", id=ref.id, status="succeeded", op="query", result=result)

    def cancel_job(self, ref: JobRef) -> JobStatus:
        return JobStatus(provider=self.name, model="mock", id=ref.id, status="cancelled", op="cancel",
                         raw={"note": "mock generates synchronously; nothing to cancel"})


def _mock_plan(prompt: str, duration_ms: int | None = None) -> dict:
    """A deterministic placeholder composition plan (mirrors the ElevenLabs shape)."""
    return {
        "positive_global_styles": ["pop", "upbeat", "clean production"],
        "negative_global_styles": ["lo-fi"],
        "sections": [{
            "section_name": "Verse 1",
            "positive_local_styles": ["melodic"],
            "negative_local_styles": [],
            "duration_ms": duration_ms or 10000,
            "lines": [f"mock lyrics for: {prompt}"[:200]],
        }],
    }


def _mock_voice_segments(turns, cast: dict, seconds: float) -> list[dict]:
    """Fabricate per-turn voice segments (mock stand-in for the API's voice_segments)."""
    n = len(turns) or 1
    step = seconds / n
    segs, ci = [], 0
    for i, t in enumerate(turns):
        segs.append({"voice_id": cast.get(t.speaker, t.speaker), "start_time_seconds": round(i * step, 4),
                     "end_time_seconds": round((i + 1) * step, 4), "character_start_index": ci,
                     "character_end_index": ci + len(t.text), "dialogue_input_index": i})
        ci += len(t.text)
    return segs


def _ref_tag(req: VideoRequest) -> str:
    n = len(req.reference_images) + len(req.reference_videos) + len(req.reference_audios)
    parts = []
    if req.first_frame:
        parts.append("first")
    if req.last_frame:
        parts.append("last")
    if n:
        parts.append(f"refs:{n}")
    return f"  [{', '.join(parts)}]" if parts else ""


def _encode_job(req: VideoRequest) -> str:
    payload = {"prompt": req.prompt, "duration": req.duration, "seed": req.seed,
               "geometry": req.geometry.as_dict() if req.geometry else {},
               "return_last_frame": req.return_last_frame}
    return "mock-" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_job(job_id: str) -> VideoRequest:
    from ..core.types import GeometrySpec

    if not job_id.startswith("mock-"):
        raise MediaError(f"not a mock job id: {job_id!r}", category=ErrorCategory.NOT_FOUND, provider="mock")
    try:
        payload = json.loads(base64.urlsafe_b64decode(job_id[len("mock-"):]).decode())
    except Exception as exc:  # noqa: BLE001
        raise MediaError(f"corrupt mock job id: {job_id!r}", category=ErrorCategory.NOT_FOUND, provider="mock") from exc
    geo = payload.get("geometry") or {}
    return VideoRequest(
        prompt=payload.get("prompt", ""), output=Path("unused"),
        duration=payload.get("duration"), seed=payload.get("seed"),
        geometry=GeometrySpec(**geo) if geo else None,
        return_last_frame=payload.get("return_last_frame", False),
    )
