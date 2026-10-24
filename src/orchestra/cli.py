"""Command line interface: ``orchestra serve | run | agents | workflows | runs | show``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from orchestra import __version__
from orchestra.api import build_engine
from orchestra.engine import InvalidState, OrchestraError
from orchestra.settings import Settings, get_settings
from orchestra.spec import load_config


def _settings(args: argparse.Namespace) -> Settings:
    settings = get_settings()
    updates: dict[str, object] = {}
    if getattr(args, "config", None):
        updates["config"] = args.config
    if getattr(args, "mock", False):
        updates["backend"] = "mock"
    if getattr(args, "workspace", None):
        updates["workspace"] = args.workspace
    return settings.model_copy(update=updates) if updates else settings


def _print_run(engine, run_id: str, verbose: bool) -> None:
    run = engine.store.get_run(run_id)
    steps = engine.store.list_steps(run_id)
    print(f"run {run.id}  workflow={run.workflow}  status={run.status.value}")
    if run.error:
        print(f"error: {run.error}")
    for s in steps:
        cost = f"  ${s.cost_usd:.4f}" if s.cost_usd else ""
        print(f"  [{s.status.value:9}] {s.idx}. {s.key} ({s.agent}){cost}")
        if verbose and s.output:
            print("\n" + s.output.strip() + "\n")
    total = sum(s.cost_usd or 0 for s in steps)
    if total:
        print(f"total cost: ${total:.4f}")


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    # The app factory reads settings from the environment, so pass CLI overrides that way.
    if args.config:
        os.environ["ORCHESTRA_CONFIG"] = args.config
    if args.mock:
        os.environ["ORCHESTRA_BACKEND"] = "mock"
    get_settings.cache_clear()
    settings = get_settings()
    uvicorn.run(
        "orchestra.api:app_factory",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    engine = build_engine(settings)

    async def go() -> str:
        run = engine.create_run(args.goal, args.workflow, True if args.approval else None)
        run = await engine.execute(run.id)
        if run.status.value == "awaiting_approval":
            print("Plan:")
            for s in engine.store.list_steps(run.id):
                print(f"  {s.idx}. {s.key} ({s.agent}): {s.task[:120]}")
            answer = input("Approve and run? [y/N] ").strip().lower() if sys.stdin.isatty() else "n"
            if answer == "y":
                run = await engine.approve(run.id)
            else:
                engine.reject(run.id, "declined on the command line")
        return run.id

    try:
        run_id = asyncio.run(go())
    except OrchestraError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_run(engine, run_id, verbose=True)
    status = engine.store.get_run(run_id).status.value
    return 0 if status == "completed" else 1


def cmd_agents(args: argparse.Namespace) -> int:
    settings = _settings(args)
    config = load_config(settings.config or None)
    for a in config.agents.values():
        tools = ",".join(a.tools) or "-"
        print(
            f"{a.name:14} model={settings.resolve_model(a.model):20} tools={tools}\n  {a.description}"
        )
    return 0


def cmd_workflows(args: argparse.Namespace) -> int:
    config = load_config(_settings(args).config or None)
    for w in config.workflows.values():
        flag = " (default)" if w.name == config.default_workflow else ""
        approval = " [approval]" if w.require_approval else ""
        route = (
            " -> ".join(s.agent for s in w.steps)
            if w.mode == "pipeline"
            else f"planner:{w.planner}"
        )
        print(f"{w.name}{flag}{approval}: {route}\n  {w.description}")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    engine = build_engine(_settings(args))
    for r in engine.store.list_runs(args.limit):
        print(f"{r.id}  {r.status.value:18} {r.workflow:18} {r.goal[:60]}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    engine = build_engine(_settings(args))
    if engine.store.get_run(args.run_id) is None:
        print("run not found", file=sys.stderr)
        return 2
    _print_run(engine, args.run_id, verbose=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestra", description="Multi-agent orchestrator for the Claude Agent SDK"
    )
    parser.add_argument("--version", action="version", version=f"orchestra {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", help="path to orchestra.yaml (default: built-in)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", parents=[common], help="start the REST API")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--mock", action="store_true", help="offline mock backend (no API key needed)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("run", parents=[common], help="run a goal through a workflow")
    p.add_argument("goal")
    p.add_argument("-w", "--workflow", help="workflow name (default: config default)")
    p.add_argument("--mock", action="store_true", help="offline mock backend (no API key needed)")
    p.add_argument("--workspace", help="directory the agents work in")
    p.add_argument("--approval", action="store_true", help="pause for approval before executing")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("agents", parents=[common], help="list configured agents")
    p.set_defaults(func=cmd_agents)
    p = sub.add_parser("workflows", parents=[common], help="list configured workflows")
    p.set_defaults(func=cmd_workflows)
    p = sub.add_parser("runs", parents=[common], help="list recent runs")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)
    p = sub.add_parser("show", parents=[common], help="show one run with its outputs")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_show)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except InvalidState as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
