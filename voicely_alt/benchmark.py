from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .installer import ensure_model, find_whisper_executable, model_path
from .recorder import AudioRecorder


BENCHMARK_MODELS = ("tiny", "base", "small")


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    model: str
    elapsed_seconds: float
    ok: bool
    output_preview: str


class BenchmarkError(RuntimeError):
    pass


def record_sample(config: AppConfig, seconds: int, output: Path) -> Path:
    recorder = AudioRecorder(config)
    recorder.start()
    time.sleep(seconds)
    recorded = recorder.stop()
    output.parent.mkdir(parents=True, exist_ok=True)
    recorded.replace(output)
    return output


def benchmark_models(
    config: AppConfig,
    sample_path: Path,
    models: tuple[str, ...] = BENCHMARK_MODELS,
) -> tuple[str, list[BenchmarkResult]]:
    if not sample_path.exists():
        raise BenchmarkError(f"Benchmark sample does not exist: {sample_path}")

    cli = find_whisper_executable("cli")
    if cli is None:
        raise BenchmarkError("whisper-cli was not found. Run setup first.")

    results: list[BenchmarkResult] = []
    for model in models:
        ensure_model(model)
        started_at = time.monotonic()
        completed = subprocess.run(
            [
                str(cli),
                "-m",
                str(model_path(model)),
                "-l",
                config.language,
                "-t",
                str(config.resolved_threads()),
                "-nt",
                "-f",
                str(sample_path),
            ],
            cwd=cli.parent,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        elapsed = time.monotonic() - started_at
        combined_output = (completed.stdout + "\n" + completed.stderr).strip()
        results.append(
            BenchmarkResult(
                model=model,
                elapsed_seconds=elapsed,
                ok=completed.returncode == 0,
                output_preview=combined_output[:500],
            )
        )

    usable = [result for result in results if result.ok]
    if not usable:
        raise BenchmarkError("No benchmark model completed successfully.")

    # Fastest successful model wins. Quality is controlled by the candidate list.
    selected = min(usable, key=lambda result: result.elapsed_seconds).model
    config.selected_model = selected
    config.save()
    return selected, results

