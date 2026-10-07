# SPDX-License-Identifier: Apache-2.0
"""Generate the homepage's highest enabled profile without changing API defaults."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
from common import DistributionError, no_symlinks
from profile_registry import load_registry

MODEL_RE = r"qwen3\.8-flash-next-lily-q4-(?:64k|128k|262k)"


def render_html(source: str, registry) -> tuple[str, dict]:
    highest = max(registry.enabled_profiles, key=lambda profile: profile.context_tokens)
    label = f"{highest.context_tokens:,}"
    # The one data snapshot drives every language, model display and code tab.
    # HTML fallbacks also show that exact enabled maximum before scripts run.
    if len(re.findall(r'data-context-model="' + MODEL_RE + r'"', source)) != 1:
        raise DistributionError("welcome profile snapshot anchor differs")
    result = re.sub(MODEL_RE, highest.public_alias, source)
    result, count = re.subn(r'data-context-tokens="(?:65536|131072|262144)"', f'data-context-tokens="{highest.context_tokens}"', result)
    if count != 1:
        raise DistributionError("welcome context snapshot anchor differs")
    result, count = re.subn(r'(<span data-profile-context>)(?:65,536|131,072|262,144)(</span>)', lambda match: match[1] + label + match[2], result)
    if count != 1:
        raise DistributionError("welcome visible context anchor differs")
    result, count = re.subn(r'(up to |total context limit of )(?:65,536|131,072|262,144)( tokens(?: of enabled total context)?)', lambda match: match[1] + label + match[2], result)
    if count != 1:
        raise DistributionError("welcome English context fallback anchor differs")
    return result, {"model": highest.public_alias, "total_context_tokens": highest.context_tokens,
                    "api_default_model": registry.default.public_alias,
                    "enabled_models": [profile.public_alias for profile in registry.enabled_profiles]}


def generate(source_root: Path, source_index: Path, output_index: Path):
    registry = load_registry(source_root)
    source_index = no_symlinks(source_index)
    output_index = no_symlinks(output_index)
    result, receipt = render_html(source_index.read_text(), registry)
    output_index.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".context-welcome-", dir=output_index.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(result); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, output_index)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return receipt


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--source-index", type=Path, default=ROOT / "assets/welcome/index.html")
    parser.add_argument("--output-index", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.source_root, args.source_index, args.output_index), indent=2))
