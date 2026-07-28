"""Configure every binding whose conventional credential variable is actually set.

Used by `.github/workflows/live.yml` to turn a set of CI secrets into a `config.toml`
the live tests can use. The mapping from a binding to the variable it wants is read
from `media-ai bindings available`, which reports it per binding from that binding's
own manifest — so adding a binding needs no edit here or in the workflow, and a
binding whose key is absent is simply never configured (its tests then skip).

Usage: `python configure_live_bindings.py <bindings-available.json>`
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2

    entries = json.loads(Path(argv[1]).read_text(encoding="utf-8")).get("bindings", [])
    configured: list[str] = []
    for entry in entries:
        binding = entry["binding"]
        # First declared variable that holds a value. The order is the manifest's, so
        # a binding's preferred name wins over the aliases it also accepts.
        var = next((v for v in entry.get("env") or [] if os.environ.get(v)), None)
        if var is None:
            continue
        subprocess.run(
            [sys.executable, "-m", "media_ai", "bindings", "add", binding, "--credential", f"env://{var}"],
            check=True, stdout=subprocess.DEVNULL,
        )
        configured.append(f"{binding} (env://{var})")

    print("configured: " + (", ".join(configured) or "nothing — every live test will skip"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
