"""The binding catalog and adapter construction — the extension point.

A backend is added by shipping a **manifest** (what it can do) and an **adapter**
(how to talk to it). Neither requires editing this file, two ways:

1. **As an installed package** — an entry point in the ``media_ai.bindings`` group
   resolving to a manifest, either as a string of TOML or a path to one::

       # pyproject.toml of the plugin package
       [project.entry-points."media_ai.bindings"]
       acme = "acme_media:MANIFEST"

   The manifest's ``[provider].adapter`` names the class to import, so the adapter
   can live anywhere — including a private package wrapping an internal RPC
   platform, which is the case a declarative-only design could never serve.

2. **In-process** — :func:`register_manifest`, for tests and embedding.

A broken plugin is logged and skipped rather than breaking the CLI; a broken
built-in raises, because that is a packaging bug the test suite must catch.

Note what is *not* here any more. There is no provider-name→factory table, no
``model_hints`` substring routing (``"seedance" in model`` picked a provider, which
stops being answerable the moment a model has two), and no ``$MEDIA_PROVIDER``
default. Which binding to call is :mod:`media_ai.core.resolve`'s job, decided from
configuration and explicit flags.
"""

from __future__ import annotations

from importlib import import_module

from .binding import BindingCatalog, ManifestError, builtin_catalog, load_manifest
from .errors import ErrorCategory, MediaError
from .logging import get_logger

__all__ = ["build_adapter", "catalog", "load_adapter_class", "register_manifest", "reset_catalog"]

_CATALOG: BindingCatalog | None = None
_EXTRA: list[tuple[str, str]] = []  # (source, toml text) registered in-process


def register_manifest(text: str, *, source: str = "<registered>") -> None:
    """Add a manifest in-process. Takes effect on the next :func:`catalog` call."""
    load_manifest(text, source=source)  # fail here, not at some later lookup
    _EXTRA.append((source, text))
    reset_catalog()


def unregister_manifest(source: str) -> None:
    global _EXTRA
    _EXTRA = [(s, t) for s, t in _EXTRA if s != source]
    reset_catalog()


def reset_catalog() -> None:
    """Drop the memoized catalog. For tests, and after registering a manifest."""
    global _CATALOG
    _CATALOG = None


def catalog() -> BindingCatalog:
    """Every declared binding: built-in manifests, entry points, then in-process ones."""
    global _CATALOG
    if _CATALOG is None:
        cat = builtin_catalog()
        _load_entry_points(cat)
        for source, text in _EXTRA:
            cat.add(*load_manifest(text, source=source), source=source)
        _CATALOG = cat
    return _CATALOG


def _load_entry_points(cat: BindingCatalog) -> None:
    from importlib.metadata import entry_points

    try:
        eps = entry_points(group="media_ai.bindings")
    except TypeError:  # pragma: no cover - very old importlib API
        eps = entry_points().get("media_ai.bindings", [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            cat.add(*load_manifest(_manifest_text(ep.load()), source=ep.name), source=ep.name)
        except Exception as exc:  # noqa: BLE001 - one broken plugin must not break the CLI
            get_logger().warning("skipping binding plugin %r: %s", getattr(ep, "name", "?"), exc)


def _manifest_text(obj) -> str:
    """An entry point resolves to TOML text, a path to a manifest, or a callable returning either."""
    if callable(obj):
        obj = obj()
    if isinstance(obj, str):
        if obj.lstrip().startswith(("[", "#")):
            return obj
        from pathlib import Path

        return Path(obj).read_text(encoding="utf-8")
    read = getattr(obj, "read_text", None)  # a Path or Traversable
    if callable(read):
        return read(encoding="utf-8")
    raise ManifestError(f"entry point resolved to {type(obj).__name__}, not manifest text or a path")


# --------------------------------------------------------------------------
# adapters
# --------------------------------------------------------------------------


def load_adapter_class(reference: str):
    """Import the ``module:Class`` an adapter reference names.

    Imported on use, never at startup: a manifest for a provider whose adapter lives
    in another package costs nothing until something asks to call it.
    """
    module_name, _, class_name = reference.partition(":")
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise MediaError(
            f"adapter {reference!r} could not be imported: {exc}",
            category=ErrorCategory.NOT_FOUND, code="adapter_not_importable",
        ) from exc
    try:
        return getattr(module, class_name)
    except AttributeError:
        raise MediaError(
            f"adapter {reference!r}: {module_name} has no {class_name}",
            category=ErrorCategory.NOT_FOUND, code="adapter_not_importable",
        ) from None


def build_adapter(rb):
    """Construct the adapter for a :class:`~media_ai.core.resolve.ResolvedBinding`.

    The binding supplies everything the adapter may know about *where* it is calling
    — base URL, per-binding options, and a credential source bound to this binding
    alone. Which model id goes on the wire travels on the request.
    """
    cls = load_adapter_class(rb.provider.adapter)
    config = {k: v for k, v in {"base_url": rb.base_url, **rb.options}.items() if v is not None}
    return cls(credentials=rb.credentials(), config=config)
