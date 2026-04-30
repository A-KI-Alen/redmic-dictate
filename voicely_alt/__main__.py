from __future__ import annotations

import argparse
import json
from pathlib import Path

from .app import run_app
from .benchmark import benchmark_models, record_sample
from .config import AppConfig
from .installer import ensure_model, install_whispercpp
from .llm import pull_ollama_model
from .paths import benchmark_sample_path, config_path
from .tracking import build_diagnostics_report, load_events, write_diagnostics_report
from .whispercpp import WhisperCppTranscriber


def main() -> None:
    parser = argparse.ArgumentParser(prog="redmic-dictate")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the tray dictation app")
    run_parser.add_argument("--no-tray", action="store_true", help="Run without a tray icon")

    setup_parser = subparsers.add_parser("setup", help="Download whisper.cpp and local model")
    setup_parser.add_argument("--model", choices=["tiny", "base", "small"], default="base")
    setup_parser.add_argument("--blas", action="store_true", help="Prefer the BLAS Windows build")

    llm_parser = subparsers.add_parser("setup-llm", help="Pull the local Ollama cleanup model")
    llm_parser.add_argument("--model", default="llama3.2:3b")

    benchmark_parser = subparsers.add_parser("benchmark", help="Benchmark local models")
    benchmark_parser.add_argument("--sample", type=Path, help="Existing WAV file to benchmark")
    benchmark_parser.add_argument(
        "--record-seconds",
        type=int,
        default=0,
        help="Record a fresh microphone sample before benchmarking",
    )

    transcribe_parser = subparsers.add_parser("transcribe", help="Transcribe one WAV file locally")
    transcribe_parser.add_argument("audio", type=Path)

    diagnostics_parser = subparsers.add_parser("diagnostics", help="Summarize local tracking events")
    diagnostics_parser.add_argument("--hours", type=int, default=24, help="Time window to inspect")
    diagnostics_parser.add_argument("--write", action="store_true", help="Write a Markdown report into the log folder")

    subparsers.add_parser("config", help="Print config path and current config")

    args = parser.parse_args()
    command = args.command or "run"
    config = AppConfig.load_or_create()

    if command == "run":
        run_app(no_tray=args.no_tray)
    elif command == "setup":
        executable = install_whispercpp(prefer_blas=args.blas)
        model_file = ensure_model(args.model)
        config.selected_model = args.model
        config.save()
        print(f"whisper.cpp: {executable}")
        print(f"model: {model_file}")
    elif command == "setup-llm":
        pull_ollama_model(config, args.model)
        config.cleanup_model = args.model
        config.transcript_cleanup = "clipboard"
        config.save()
        print(f"cleanup model: {args.model}")
    elif command == "benchmark":
        install_whispercpp()
        sample = args.sample
        if args.record_seconds:
            print(f"Recording benchmark sample for {args.record_seconds} seconds...")
            sample = record_sample(config, args.record_seconds, benchmark_sample_path())
        if sample is None:
            sample = benchmark_sample_path()
        selected, results = benchmark_models(config, sample)
        print(f"Selected model: {selected}")
        for result in results:
            status = "ok" if result.ok else "failed"
            print(f"{result.model}: {result.elapsed_seconds:.2f}s ({status})")
    elif command == "transcribe":
        transcriber = WhisperCppTranscriber(config)
        try:
            transcript = transcriber.transcribe(args.audio)
            print(transcript)
        finally:
            transcriber.close()
    elif command == "diagnostics":
        hours = max(1, int(args.hours))
        events = load_events(hours)
        print(build_diagnostics_report(events, hours=hours))
        if args.write:
            print(f"written: {write_diagnostics_report(config, hours=hours)}")
    elif command == "config":
        print(config_path())
        print(json.dumps(config.__dict__ if hasattr(config, "__dict__") else {
            field: getattr(config, field) for field in config.__dataclass_fields__
        }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
