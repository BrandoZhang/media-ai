# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This repo uses **uv** for env/deps (see `docs/DEVELOPMENT.md`). Prefer `uv run`:

```bash
uv sync                                 # .venv + editable install + dev group (pytest, ruff); writes uv.lock
uv run pytest -q                        # full suite (offline: no creds, no network)
uv run pytest -q tests/test_gemini_api.py::test_veo_lro_poll_and_download   # a single test
uv run ruff check media_ai tests        # lint (config in pyproject: line-length 130, py311, ignore E402/E741)
uv run ruff check media_ai tests --fix  # autofix
uv run media-ai image generate --prompt "x" --output /tmp/x.png   # run the CLI (mock by default)
# pip fallback: pip install -e ".[test]" ruff  →  run ruff/pytest/`python -m media_ai` directly
```

Requires Python **3.11+** (uses `tomllib`); `uv` fetches a matching interpreter. ffmpeg + Pillow come bundled via `imageio-ffmpeg`/`pillow` (no system install needed). CI (`.github/workflows/ci.yml`) runs `ruff check` + `pytest -q`. The CLI-integration tests self-skip if Pillow/ffmpeg are absent, so the suite stays green on a bare box.

## Architecture

A provider-agnostic **core**, a thin per-provider **adapter** layer, and a thin **CLI** front end. Everything offline/default runs on the `mock` provider. See `docs/ARCHITECTURE.md` for the diagram; the load-bearing ideas:

- **Dependency direction is a hard rule:** `core/` never imports `providers/`. Adapters depend on `core/` + `credentials/` + `providers/_http.py`. The CLI depends on `core/` + `registry`, never on a concrete provider. Don't add a `providers` import to anything under `core/`.
- **`Provider` (`core/provider.py`) is transport-agnostic** — no HTTP assumptions. `HttpProvider`/`HttpClient` (`providers/_base.py`, `providers/_http.py`) are an *optional* REST convenience; an RPC/gRPC/SDK backend subclasses `Provider` directly. HTTP is confined to the four built-in adapters + `_base`/`_http`.
- **Request flow:** CLI parses argv → normalized `ImageRequest`/`VideoRequest` (`core/types.py`) → `registry.build()` resolves `(provider, model)` → `validate_request()` checks it against the model's `ModelCapabilities` **before any network call** → `provider.generate_*()` → `GenerationResult`/`JobHandle` → `common.emit_result` prints one JSON line.
- **Capabilities are the single source of truth** (`core/capabilities.py`). Each model's `ModelCapabilities` drives **both** `media-ai capabilities` discovery **and** pre-flight validation — keep them in sync. Unsupported operation/option/geometry raises `MediaError(UNSUPPORTED)` (exit 3) unless `--on-unsupported warn|ignore`.
- **Provider-specific features go through `options`**, not new common fields. A knob only one provider understands is passed as `--option key=value`, declared in that model's `caps.*.options`, and read from `req.options`. Cross-provider concepts (`--seed`, `--negative-prompt`, `--duration`, `--audio`, geometry) are first-class request fields.
- **Geometry is normalized** (`GeometrySpec`): either pixel `--size WxH` or `--aspect-ratio` + `--resolution` tier. Each adapter maps/validates it to its own wire format; `core/geometry.py` holds the shared primitives.

## Machine contract (do not break casually)

- **stdout = exactly one JSON object**, for success *and* failure. Success carries `artifacts[]` + `usage` + `meta` (+ compat aliases `path`/`extra_paths`/`bytes`). Failure is `{"ok": false, "error": {...}}`. Human logs go to **stderr only**.
- **Exit code = error category.** `MediaError` has a `category` → `exit_code` map (`core/errors.py`): `2` CLI · `3` validation/unsupported · `4` auth · `5` rate-limit · `6` provider · `7` timeout · `8` safety · `9` not-found. `cli/common.py::run()` wraps every command to enforce this; make new commands go through `run()`.
- **Result JSON carries `schema_version`.** Bump it in `core/result.py` if you change the shape incompatibly.

## Credentials & secrets (trust boundary)

- The CLI never touches secrets — it passes a **provider name**; the registry binds a `CredentialProvider`; the adapter resolves lazily and reveals the value **only inside its request builder** (`HttpProvider._prepare` / `credential().reveal()`).
- Resolution chain (`credentials/resolver.py`, most-secure first): broker → secret-manager reference → OS keychain → chmod-600 config file → env var. Re-resolved per invocation (rotation-friendly).
- `Secret` (`credentials/secret.py`) is reveal-only; its repr/str/pickle render `***` and it isn't JSON-serializable. All log/output/error sinks pass through `redact()` (`credentials/redaction.py`), which masks live secret values + key-shaped tokens. **Never** add a `--api-key` flag, log headers, or serialize a `Secret`.

## Conventions specific to this repo

- **Adding a provider requires no core changes.** Subclass `Provider`/`HttpProvider`, declare `ModelCapabilities` per model, then `register_provider(name, factory, model_hints=…)` in-process **or** ship a `media_ai.providers` entry point → a `Provider` subclass. Map the transport's errors to `MediaError` categories; use `media_ai.retry()` for non-HTTP retry. See `docs/EXTENDING.md`.
- **Async is a `Job`.** `video generate --wait false` returns a `JobHandle`; `job query --output` finalizes (downloads). Volc's blocking poll cancels the billed task on SIGTERM/SIGINT/timeout — preserve that when touching `providers/volc.py::_poll`.
- **HTTP idempotency:** `HttpClient` retries 429 always, but transient 5xx/network **only on GET/DELETE**, so a POST create-task can't double-submit a billed job. Don't relax this. A provider may supply `retry_classifier(status, body)` to *veto* a would-be retry it knows is pointless (e.g. Ark 429 `QuotaExceeded`); it can only turn a retry off, never on.
- **Only send provider params the caller set.** Optional wire fields with no deliberate default (e.g. Volc `camera_fixed`) are omitted unless provided via `--option`, since some models reject an unrequested parameter. `watermark` is the deliberate exception (defaults to `false` = no watermark).
- **The 8 legacy console-scripts** (`text2image`, …) are thin shims in `cli/legacy.py` that build the same normalized requests; `media_ai/mediakit.py` is a re-export compat shim only. New work targets the `media-ai <group> <op>` surface.
- **Tests are offline.** Adapters are exercised with a `FakeClient` (`tests/conftest.py`) that records request bodies and returns canned responses; the mock provider covers the local ffmpeg/Pillow path. Real-API calls are never made in CI. New adapters get: a mocked-API test (request body + response parse + error mapping) and they're auto-covered by the parametrized `tests/test_contract.py`.
