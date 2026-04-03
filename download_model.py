#!/usr/bin/env python3
"""
Download a GGUF model into MODEL_CACHE_DIR (default: ./models).

Presets (when MODEL_REPO_ID is not set):
  llama32   — DEFAULT: Llama-3.2-3B-Instruct Q4_K_M (~2 GB; may need HF token if gated)
  tinyllama — TinyLlama-1.1B chat (~0.6 GB)
  gccl      — GCCL-Medical-LLM-8B Q4_K_M (~4.7 GB)

Usage:
  python download_model.py                  # Llama-3.2-3B-Instruct (default)
  python download_model.py llama32
  python download_model.py tinyllama
  python download_model.py gccl

Env override (wins over preset):
  MODEL_REPO_ID    Hugging Face repo id
  MODEL_FILENAME   file in repo (required for custom repos not listed in presets; if omitted, filename
                   is inferred when MODEL_REPO_ID matches a preset repo, else defaults to Llama 3.2 Q4 name)
  MODEL_CACHE_DIR  root directory for models (default: ./models)

Gated models: accept the license on huggingface.co and set HF_TOKEN or run
`huggingface-cli login` if download fails with 401/403.

Progress: tqdm is enabled on stderr; Docker logs also get **newline** ``[model] … X%`` lines because
``\\r`` updates often look "stuck". Chunk size is reduced (default 1 MiB) so the bar moves sooner on slow links.

Env: ``HF_HUB_DISABLE_PROGRESS_BARS=1`` (quiet), ``HF_HUB_DOWNLOAD_TIMEOUT`` (seconds between stream reads,
default **120** here), ``ZENAI_HF_DOWNLOAD_CHUNK_MB`` (1–10, default **1**).
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

# Safer than hub default (10s) for slow / VPN links when streaming multi‑GB files.
_DEFAULT_HF_READ_TIMEOUT_SEC = "120"
# Smaller than hub default (10 MiB) so tqdm updates more often before first 10 MiB arrives.
_DEFAULT_HF_CHUNK_MB = "1"


class _StderrActsAsTTY:
    """Make tqdm/http_get show a bar in Docker (no TTY) by reporting isatty() True on stderr."""

    __slots__ = ("_real",)

    def __init__(self, real: TextIO) -> None:
        object.__setattr__(self, "_real", real)

    def isatty(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@contextlib.contextmanager
def _cli_download_progress() -> Iterator[None]:
    """
    huggingface_hub disables tqdm when stderr is not a TTY (common in ``docker run`` without ``-t``).
    Also avoids ``disable=True`` when the hub logger level is logging.NOTSET.
    Respect ``HF_HUB_DISABLE_PROGRESS_BARS=1`` to keep output quiet.
    """
    off = os.environ.get("HF_HUB_DISABLE_PROGRESS_BARS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if off:
        yield
        return

    logging.getLogger("huggingface_hub").setLevel(logging.INFO)
    try:
        from huggingface_hub.utils import enable_progress_bars

        enable_progress_bars()
    except (ImportError, AttributeError):
        pass

    real_err = sys.stderr
    sys.stderr = _StderrActsAsTTY(real_err)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.stderr = real_err
        try:
            real_err.flush()
        except OSError:
            pass


# Preset id -> (repo_id, filename)
PRESETS: dict[str, tuple[str, str]] = {
    "llama32": (
        "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    ),
    "tinyllama": (
        "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
        "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
    ),
    "gccl": (
        "mradermacher/GCCL-Medical-LLM-8B-GGUF",
        "GCCL-Medical-LLM-8B.Q4_K_M.gguf",
    ),
}

# Model profiles — higher-level than presets, include benchmark acceptance criteria
# and recommended server parameters for different workloads.
MODEL_PROFILES: dict[str, dict] = {
    "general": {
        "preset": "llama32",
        "description": "General-purpose 3B model — fast, good for scheduling/reporting",
        "size_gb": 2.0,
        "recommended_ctx": 4096,
        "temperature": 0.7,
        "benchmark": {
            "min_tokens_per_sec": 10,
            "max_first_token_ms": 2000,
        },
    },
    "medical": {
        "preset": "gccl",
        "description": "GCCL-Medical-LLM-8B — domain-tuned for clinical/hospital queries",
        "size_gb": 4.7,
        "recommended_ctx": 4096,
        "temperature": 0.3,
        "benchmark": {
            "min_tokens_per_sec": 5,
            "max_first_token_ms": 4000,
        },
    },
    "lightweight": {
        "preset": "tinyllama",
        "description": "TinyLlama 1.1B — minimal resources, demo/testing only",
        "size_gb": 0.6,
        "recommended_ctx": 2048,
        "temperature": 0.7,
        "benchmark": {
            "min_tokens_per_sec": 20,
            "max_first_token_ms": 1000,
        },
    },
}

DEFAULT_PRESET = "llama32"


def _apply_hf_hub_download_tweaks() -> None:
    """Larger read timeout + smaller HTTP chunks → fewer false 'stuck at 0%%' impressions."""
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", _DEFAULT_HF_READ_TIMEOUT_SEC)
    # hf_transfer can confuse progress / stall in some environments; keep vanilla HTTP unless user opts in.
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
    try:
        mb = int(os.environ.get("ZENAI_HF_DOWNLOAD_CHUNK_MB", _DEFAULT_HF_CHUNK_MB), 10)
    except ValueError:
        mb = int(_DEFAULT_HF_CHUNK_MB, 10)
    mb = max(1, min(10, mb))
    chunk = mb * 1024 * 1024
    try:
        import huggingface_hub.constants as hf_constants  # type: ignore[import-untyped]

        hf_constants.DOWNLOAD_CHUNK_SIZE = chunk
    except (ImportError, AttributeError):
        pass


def _install_line_logged_tqdm() -> Any:
    """
    Hugging Face http_get uses tqdm with carriage-return refresh; Docker log drivers often show one frozen line.
    Subclass tqdm and emit newline status to stdout every few percent / MiB.
    """
    try:
        import huggingface_hub.file_download as fd  # type: ignore[import-untyped]
    except ImportError:
        return

    _Base = fd.tqdm

    class _LineLoggedTqdm(_Base):  # type: ignore[misc,valid-type]
        __slots__ = ("_zenai_last_log_pct", "_zenai_last_log_n")

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("mininterval", 0.5)
            kwargs.setdefault("miniters", 1)
            super().__init__(*args, **kwargs)
            self._zenai_last_log_pct = -1.0
            self._zenai_last_log_n = 0

        def update(self, n: int = 1) -> Any:
            r = super().update(n)
            total = self.total
            n_done = int(getattr(self, "n", 0) or 0)
            if total and total > 0:
                pct = 100.0 * n_done / total
                if pct - self._zenai_last_log_pct >= 1.0 or n_done - self._zenai_last_log_n >= 8 * 1024 * 1024:
                    gi_done = n_done / (1024.0**3)
                    gi_tot = total / (1024.0**3)
                    print(
                        f"[model] … download {pct:.1f}% ({gi_done:.2f} / {gi_tot:.2f} GiB)",
                        flush=True,
                    )
                    self._zenai_last_log_pct = pct
                    self._zenai_last_log_n = n_done
            elif n_done > 0 and n_done - self._zenai_last_log_n >= 32 * 1024 * 1024:
                print(f"[model] … downloaded {n_done / (1024**2):.0f} MiB …", flush=True)
                self._zenai_last_log_n = n_done
            return r

    fd.tqdm = _LineLoggedTqdm  # type: ignore[assignment]
    return None


@contextlib.contextmanager
def _download_heartbeat() -> Iterator[threading.Event]:
    """Periodic message so operators know we did not exit (and bytes may still be trickling)."""
    stop = threading.Event()

    def _run() -> None:
        t0 = time.monotonic()
        # First ping a bit sooner — common case: slow first chunk still shows 0%%.
        first = 30.0
        while not stop.wait(first):
            first = 60.0
            elapsed = int(time.monotonic() - t0)
            print(
                f"[model] … still downloading ({elapsed}s elapsed; "
                "if progress stays at 0% for many minutes, check network, VPN, or HF_TOKEN for gated repos)",
                flush=True,
            )

    th = threading.Thread(target=_run, name="model-download-heartbeat", daemon=True)
    th.start()
    try:
        yield stop
    finally:
        stop.set()


def resolve_repo_and_file(args: argparse.Namespace) -> tuple[str, str]:
    """
    Env MODEL_REPO_ID overrides preset.

    If MODEL_FILENAME is set, use it. Otherwise, if MODEL_REPO_ID matches a PRESETS repo id,
    use that preset's filename. Else default filename to the default preset (Llama-3.2-3B-Instruct Q4_K_M);
    set MODEL_FILENAME explicitly for other repos.
    """
    env_repo = os.environ.get("MODEL_REPO_ID", "").strip()
    if env_repo:
        env_file = os.environ.get("MODEL_FILENAME", "").strip()
        if env_file:
            return env_repo, env_file
        for repo_id, filename in PRESETS.values():
            if repo_id == env_repo:
                return env_repo, filename
        return env_repo, PRESETS[DEFAULT_PRESET][1]
    return PRESETS[args.preset]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download a GGUF model for ZenAIos (llama-cpp / chat).",
        epilog=f"Default preset: {DEFAULT_PRESET}. Set MODEL_REPO_ID (+ optional MODEL_FILENAME) to override.",
    )
    parser.add_argument(
        "preset",
        nargs="?",
        default=DEFAULT_PRESET,
        choices=sorted(PRESETS.keys()),
        metavar="PRESET",
        help=(
            f"Model preset when MODEL_REPO_ID is unset (default: {DEFAULT_PRESET}). "
            f"Choices: {', '.join(sorted(PRESETS))}."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=sorted(MODEL_PROFILES.keys()),
        default=None,
        help="Use a named model profile (overrides preset). Profiles: " + ", ".join(sorted(MODEL_PROFILES)),
    )
    args = parser.parse_args()

    # Profile overrides preset
    if args.profile:
        profile = MODEL_PROFILES[args.profile]
        args.preset = profile["preset"]
        print(f"[model] profile: {args.profile} — {profile['description']}")
        print(f"[model]   recommended ctx: {profile['recommended_ctx']}, temp: {profile['temperature']}")
        bm = profile.get("benchmark", {})
        if bm:
            print(
                f"[model]   benchmark: ≥{bm.get('min_tokens_per_sec', '?')} tok/s, "
                f"≤{bm.get('max_first_token_ms', '?')}ms first token"
            )

    _apply_hf_hub_download_tweaks()

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Install huggingface_hub: pip install huggingface-hub", file=sys.stderr)
        return 1

    _install_line_logged_tqdm()

    repo_id, filename = resolve_repo_and_file(args)
    cache_root = Path(os.environ.get("MODEL_CACHE_DIR", "./models")).expanduser().resolve()

    model_stem = Path(filename).stem
    target_dir = cache_root / model_stem
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / filename

    if target_file.is_file():
        print(f"[model] already present: {target_file}")
        return 0

    print(f"[model] repo: {repo_id}")
    print(f"[model] file: {filename}")
    print(f"[model] target: {target_dir}")
    print("[model] downloading (tqdm + periodic [model] … lines below)…", flush=True)

    with _cli_download_progress(), _download_heartbeat():
        local_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(target_dir),
            local_dir_use_symlinks=False,
        )

    print(f"[model] downloaded: {local_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
