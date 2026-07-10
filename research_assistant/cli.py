from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

from .models import TaskSpec
from .planner import AgentPlanner, PlanningError
from .plugins import PluginError, load_plugins
from .providers import ProviderError, create_provider
from .registry import ToolError
from .runner import ApprovalError, TaskRunner, approve_step
from .secrets import KeyringSecretStore, SecretStoreError
from .settings import AgentSettings, ProviderConfig, SettingsError, default_config_path, load_settings, save_settings
from .tools import build_default_registry
from .workflows import WorkflowError, build_workflow_plan
from .workspace import TaskWorkspace, WorkspaceError, default_workspace_root


def _config_path(value: Optional[str]) -> Path:
    return Path(value).expanduser() if value else default_config_path()


def _workspace_root(value: Optional[str]) -> Path:
    return Path(value).expanduser() if value else default_workspace_root()


def _load_registry(settings: AgentSettings):
    registry = build_default_registry()
    load_plugins(registry, settings.plugins)
    return registry


def _format_arguments(arguments: dict) -> str:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _output_hint(tool_name: str) -> str:
    return {
        "web.fetch": "artifacts/web-*.json",
        "web.search": "artifacts/search-*.json",
        "file.read": "artifacts/file-*.json",
        "url_list.read": "artifacts/url-list-*.json",
        "browser.extract": "artifacts/crawl-*.json",
        "data.normalize": "artifacts/normalized-dataset.json",
        "data.to_markdown": "artifacts/dataset-table.md",
        "report.summarize": "artifacts/model-summary.md",
        "report.compose": "artifacts/report.md and artifacts/sources.jsonl",
    }.get(tool_name, "task workspace artifacts")


def _data_scope_hint(tool_name: str) -> str:
    if tool_name == "report.summarize":
        return "Approved normalized data only: up to 10 records, each text value clipped to 2,000 characters."
    return ""


def print_plan(workspace: TaskWorkspace) -> None:
    task = workspace.load_task()
    plan = workspace.load_plan()
    print(f"Task: {task.id}")
    print(f"Goal: {task.goal}")
    print(f"Status: {task.status.value}")
    print(f"Plan: {plan.summary}")
    print()
    for step in plan.steps:
        print(f"{step.id} [{step.status.value}] {step.call.tool_name} ({step.call.risk.value})")
        print(f"  Description: {step.description}")
        print(f"  Input: {_format_arguments(step.call.arguments)}")
        print(f"  Target: {step.call.target}")
        print(f"  Output: {_output_hint(step.call.tool_name)}")
        if scope := _data_scope_hint(step.call.tool_name):
            print(f"  Data sent: {scope}")
        if step.error:
            print(f"  Error: {step.error}")
    print()
    print(f"Task workspace: {workspace.root}")


def _get_provider(settings: AgentSettings, provider_name: Optional[str]):
    name = provider_name or settings.default_provider
    if not name:
        return None, None
    if name not in settings.providers:
        raise SettingsError(f"Provider is not configured: {name}")
    provider = create_provider(settings.providers[name], KeyringSecretStore())
    return name, provider


def _confirm_planning(provider_name: str) -> bool:
    prompt = (
        f"This sends only the goal, explicit public URLs, input file names, and registered tool metadata "
        f"to provider '{provider_name}' to create a plan. Continue? [y/N]: "
    )
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


async def _run_command(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    settings = load_settings(config_path)
    registry = _load_registry(settings)
    input_files = [str(Path(path).expanduser().resolve()) for path in args.input]
    missing = [path for path in input_files if not Path(path).is_file()]
    if missing:
        raise WorkspaceError(f"Input file does not exist: {missing[0]}")

    provider_name = args.provider or settings.default_provider
    provider = None
    if args.workflow == "auto":
        provider_name, provider = _get_provider(settings, provider_name)
    elif provider_name:
        try:
            provider_name, provider = _get_provider(settings, provider_name)
        except (SettingsError, SecretStoreError, ProviderError):
            print("Warning: configured provider is unavailable; this built-in workflow will use local report fallback.")
    if args.workflow == "auto" and provider is None:
        raise SettingsError("agent run --workflow auto requires a configured provider.")
    if args.workflow == "auto" and not args.approve_planning and not _confirm_planning(provider_name or "default"):
        print("Planning cancelled. No task workspace was created.")
        return 1

    task = TaskSpec.create(
        goal=args.goal,
        workflow=args.workflow,
        provider_name=provider_name,
        urls=list(args.url),
        input_files=input_files,
    )
    workspace = TaskWorkspace.create(_workspace_root(args.workspace_root), task)
    try:
        if args.workflow == "auto":
            workspace.append_approval(
                {
                    "action": "planning_call_approved",
                    "provider": provider_name,
                    "sent": "goal, explicit URLs, input file names, registered tool metadata",
                }
            )
            plan = await AgentPlanner(provider, registry).create_plan(task)  # type: ignore[arg-type]
        else:
            plan = build_workflow_plan(task, registry)
    except Exception as exc:
        workspace.append_log("plan_failed", {"error": str(exc)})
        print(f"Plan creation failed. Task workspace retained for audit: {workspace.root}", file=sys.stderr)
        raise

    task.summary = plan.summary
    from .models import TaskStatus

    task.status = TaskStatus.WAITING_APPROVAL
    workspace.save_task(task)
    workspace.save_plan(plan)
    workspace.append_log("plan_created", {"step_count": len(plan.steps), "summary": plan.summary})
    print_plan(workspace)
    print(f"Next: agent approve {task.id} step-01")
    return 0


def _command_configure_provider(args: argparse.Namespace) -> int:
    config_path = _config_path(args.config)
    settings = load_settings(config_path)
    if args.kind == "openai_compatible" and not args.base_url:
        raise SettingsError("--base-url is required for openai_compatible providers.")
    if args.timeout <= 0:
        raise SettingsError("--timeout must be greater than zero.")
    secret_ref = args.secret_ref or f"provider:{args.name}"
    api_key = getpass.getpass("API key (stored in Windows Credential Manager; it will not be written to YAML): ").strip()
    if not api_key:
        raise SettingsError("API key cannot be empty.")
    store = KeyringSecretStore()
    store.set(secret_ref, api_key)
    settings.providers[args.name] = ProviderConfig(
        name=args.name,
        kind=args.kind,
        model=args.model,
        secret_ref=secret_ref,
        base_url=args.base_url or "",
        endpoint=args.endpoint or "",
        timeout_seconds=args.timeout,
    )
    if args.make_default or not settings.default_provider:
        settings.default_provider = args.name
    save_settings(settings, config_path)
    print(f"Configured provider '{args.name}' in {config_path}.")
    print(f"API key stored under secret_ref '{secret_ref}' in the system credential store.")
    return 0


def _command_approve(args: argparse.Namespace) -> int:
    workspace = TaskWorkspace.open(_workspace_root(args.workspace_root), args.task_id)
    step = approve_step(workspace, args.step_id)
    print(f"Approved {step.id}: {step.call.tool_name} ({step.call.risk.value})")
    print(f"Next: agent resume {args.task_id}")
    return 0


async def _resume_command(args: argparse.Namespace) -> int:
    settings = load_settings(_config_path(args.config))
    registry = _load_registry(settings)
    workspace = TaskWorkspace.open(_workspace_root(args.workspace_root), args.task_id)
    task = workspace.load_task()
    provider = None
    if task.provider_name:
        try:
            _, provider = _get_provider(settings, task.provider_name)
        except (SettingsError, SecretStoreError, ProviderError) as exc:
            workspace.append_log("provider_unavailable", {"provider": task.provider_name, "error": str(exc)})
            print("Warning: configured provider is unavailable; model-summary steps will use the local fallback.")
    result = await TaskRunner(registry, provider=provider).resume(workspace)
    print(f"Task status: {result.task_status.value}")
    if result.executed_step_id:
        print(f"Executed: {result.executed_step_id}")
    print(result.message)
    if result.task_status.value == "waiting_approval":
        plan = workspace.load_plan()
        next_step = next((step for step in plan.steps if step.status.value == "planned"), None)
        if next_step:
            print(f"Next: agent approve {task.id} {next_step.id}")
    return 0 if result.task_status.value != "failed" else 1


def _command_status(args: argparse.Namespace) -> int:
    workspace = TaskWorkspace.open(_workspace_root(args.workspace_root), args.task_id)
    print_plan(workspace)
    artifacts = workspace.list_artifacts()
    if artifacts:
        print("Artifacts:")
        for artifact in artifacts:
            print(f"- {artifact.id} {artifact.kind}: {artifact.path}")
    return 0


def _command_export(args: argparse.Namespace) -> int:
    workspace = TaskWorkspace.open(_workspace_root(args.workspace_root), args.task_id)
    default_destination = Path.home() / "GenericCrawler" / "exports" / f"{args.task_id}.zip"
    archive = workspace.export_archive(Path(args.output).expanduser() if args.output else default_destination)
    print(f"Exported task archive: {archive}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Local, approval-gated research assistant for public sources and common data files.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="Configure a model provider without writing a key to YAML.")
    configure_sub = configure.add_subparsers(dest="configure_command", required=True)
    provider = configure_sub.add_parser("provider", help="Save one provider configuration and API key reference.")
    provider.add_argument("--name", default="default")
    provider.add_argument("--kind", required=True, choices=["openai_compatible", "gemini", "qwen"])
    provider.add_argument("--model", required=True)
    provider.add_argument("--base-url", help="Required for openai_compatible providers.")
    provider.add_argument("--endpoint", help="Optional Gemini-compatible custom endpoint.")
    provider.add_argument("--timeout", type=float, default=30.0)
    provider.add_argument("--secret-ref", help="Optional credential-store reference; defaults to provider:<name>.")
    provider.add_argument("--make-default", action="store_true")
    provider.add_argument("--config", help="Optional path for the non-secret YAML settings file.")
    provider.set_defaults(handler=_command_configure_provider)

    run = subparsers.add_parser("run", help="Create an approval-gated task plan.")
    run.add_argument("goal")
    run.add_argument("--workflow", default="auto", choices=["auto", "research_report", "file_report", "web_to_markdown", "crawler_report"])
    run.add_argument("--url", action="append", default=[], help="Explicit public source URL; may be repeated.")
    run.add_argument("--input", action="append", default=[], help="Explicit local input file; may be repeated.")
    run.add_argument("--provider", help="Configured provider name; defaults to default_provider.")
    run.add_argument("--approve-planning", action="store_true", help="Acknowledge the model-planning request without an interactive prompt.")
    run.add_argument("--workspace-root", help="Override the default ~/GenericCrawler/tasks location.")
    run.add_argument("--config", help="Optional path for non-secret agent settings.")
    run.set_defaults(async_handler=_run_command)

    approve = subparsers.add_parser("approve", help="Approve only the next planned step of a task.")
    approve.add_argument("task_id")
    approve.add_argument("step_id")
    approve.add_argument("--workspace-root", help="Override the default task workspace location.")
    approve.set_defaults(handler=_command_approve)

    resume = subparsers.add_parser("resume", help="Execute the one approved next step of a task.")
    resume.add_argument("task_id")
    resume.add_argument("--workspace-root", help="Override the default task workspace location.")
    resume.add_argument("--config", help="Optional path for non-secret agent settings.")
    resume.set_defaults(async_handler=_resume_command)

    status = subparsers.add_parser("status", help="Show task plan, approvals, and artifacts.")
    status.add_argument("task_id")
    status.add_argument("--workspace-root", help="Override the default task workspace location.")
    status.set_defaults(handler=_command_status)

    export = subparsers.add_parser("export", help="Create a zip archive of a task workspace.")
    export.add_argument("task_id")
    export.add_argument("--output", help="Destination .zip path.")
    export.add_argument("--workspace-root", help="Override the default task workspace location.")
    export.set_defaults(handler=_command_export)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if hasattr(args, "async_handler"):
            return asyncio.run(args.async_handler(args))
        return args.handler(args)
    except (
        SettingsError,
        SecretStoreError,
        ProviderError,
        PlanningError,
        WorkflowError,
        WorkspaceError,
        ApprovalError,
        ToolError,
        PluginError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. No unapproved step was executed.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
