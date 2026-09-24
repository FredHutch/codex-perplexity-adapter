"""Command-line entry point for the standalone adapter."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

import uvicorn

from .app import Settings, _parse_route, create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Codex–Perplexity adapter")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument(
        "--model-alias",
        help="expose only this model alias (defaults to gpt-5.6-sol when used alone)",
    )
    parser.add_argument(
        "--upstream-model",
        help="pin requests to this upstream model (defaults to openai/gpt-5.6-sol when used alone)",
    )
    parser.add_argument(
        "--model-route", action="append", default=[], metavar="ALIAS=UPSTREAM",
        help="overlay a built-in model route; repeatable, with the last value winning",
    )
    parser.add_argument(
        "--allow-model-passthrough", action="store_true",
        help="route unknown valid gpt-* names to openai/<name> (Cyber remains blocked)",
    )
    parser.add_argument("--local-token", default=os.getenv("ADAPTER_LOCAL_TOKEN", "local-adapter-token"))
    parser.add_argument("--pid-file", help="write the running server PID to this file")
    parser.add_argument("--prompt-key", action="store_true", help="securely prompt for the Perplexity API key")
    parser.add_argument("--log-level", default="info", choices=("critical", "error", "warning", "info", "debug"))
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.model_route and (args.model_alias is not None or args.upstream_model is not None):
        parser.error("--model-route cannot be combined with --model-alias or --upstream-model")
    try:
        for value in args.model_route:
            _parse_route(value)
        if args.model_alias is not None or args.upstream_model is not None:
            _parse_route(f"{args.model_alias or 'gpt-5.6-sol'}={args.upstream_model or 'openai/gpt-5.6-sol'}")
    except ValueError as exc:
        parser.error(str(exc))
    api_key = os.getenv("PERPLEXITY_API_KEY", "")
    if args.prompt_key or not api_key:
        api_key = getpass.getpass("Perplexity API key: ")
    if not api_key:
        raise SystemExit("PERPLEXITY_API_KEY is required (or use --prompt-key).")

    settings = Settings(
        api_key=api_key,
        model_alias=args.model_alias,
        upstream_model=args.upstream_model,
        model_routes=tuple(args.model_route),
        allow_model_passthrough=args.allow_model_passthrough,
        local_token=args.local_token,
    )
    pid_file = Path(args.pid_file).expanduser() if args.pid_file else None
    if pid_file:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        uvicorn.run(
            create_app(settings),
            host="127.0.0.1",
            port=args.port,
            log_level=args.log_level,
            access_log=False,
        )
    finally:
        if pid_file:
            try:
                if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_file.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
