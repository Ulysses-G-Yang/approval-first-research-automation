from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse

import yaml

from .models import PlanStep, RiskLevel, TaskPlan, TaskSpec, ToolCall
from .registry import ToolError, ToolRegistry


class WorkflowError(ValueError):
    pass


WORKFLOW_DIRECTORY = Path(__file__).resolve().parents[1] / "workflows"
DOCUMENT_SUFFIXES = {".docx", ".pdf", ".md", ".markdown", ".txt"}
YAML_SUFFIXES = {".yaml", ".yml"}
WORKFLOW_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def available_workflows(directory: Optional[Path] = None) -> List[str]:
    root = (directory or WORKFLOW_DIRECTORY).expanduser().resolve()
    if not root.is_dir():
        return []
    return sorted(path.stem for path in root.glob("*.yaml") if WORKFLOW_NAME_PATTERN.fullmatch(path.stem))


def workflow_catalog(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return safe, human-readable metadata for bundled declarative workflows."""
    catalog: List[Dict[str, Any]] = []
    for name in available_workflows(directory):
        _, definition = load_workflow_definition(name, directory)
        steps = definition.get("steps") or []
        tools = [
            str(step.get("tool", "")).strip()
            for step in steps
            if isinstance(step, dict) and str(step.get("tool", "")).strip()
        ]
        catalog.append(
            {
                "name": name,
                "summary": str(definition.get("summary", "")).strip(),
                "requires": [str(value) for value in definition.get("requires") or []],
                "tools": tools,
            }
        )
    return catalog


def _definition_path(name: str, directory: Optional[Path] = None) -> Path:
    if not WORKFLOW_NAME_PATTERN.fullmatch(name):
        raise WorkflowError("Workflow names may only contain lowercase letters, digits, hyphens, and underscores.")
    root = (directory or WORKFLOW_DIRECTORY).expanduser().resolve()
    candidate = (root / f"{name}.yaml").resolve()
    if candidate.parent != root:
        raise WorkflowError("Workflow definition must stay inside the configured workflow directory.")
    return candidate


def load_workflow_definition(name: str, directory: Optional[Path] = None) -> tuple[Path, Dict[str, Any]]:
    path = _definition_path(name, directory)
    if not path.is_file():
        raise WorkflowError(f"Workflow definition not found: {path.name}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise WorkflowError(f"Workflow definition is not valid YAML: {path.name}") from exc
    if not isinstance(data, dict):
        raise WorkflowError("Workflow definition must be a YAML mapping.")
    if data.get("version") != 1:
        raise WorkflowError(f"Unsupported workflow version in {path.name}.")
    if data.get("name") != name:
        raise WorkflowError(f"Workflow name in {path.name} does not match its filename.")
    if not isinstance(data.get("steps"), list) or not data["steps"]:
        raise WorkflowError(f"Workflow {name} requires at least one step.")
    return path, data


def _input_groups(task: TaskSpec) -> Dict[str, List[str]]:
    inputs = list(task.input_files)
    return {
        "urls": list(task.urls),
        "input_files": inputs,
        "yaml_input_files": [path for path in inputs if Path(path).suffix.lower() in YAML_SUFFIXES],
        "document_input_files": [path for path in inputs if Path(path).suffix.lower() in DOCUMENT_SUFFIXES],
        "markdown_input_files": [
            path for path in inputs if Path(path).suffix.lower() in {".md", ".markdown", ".txt"}
        ],
    }


def _condition_matches(condition: Optional[str], task: TaskSpec, groups: Mapping[str, List[str]]) -> bool:
    if not condition or condition == "always":
        return True
    conditions = {
        "has_urls": bool(groups["urls"]),
        "has_input_files": bool(groups["input_files"]),
        "no_sources": not groups["urls"] and not groups["input_files"],
        "has_yaml_inputs": bool(groups["yaml_input_files"]),
        "has_document_inputs": bool(groups["document_input_files"]),
        "has_markdown_inputs": bool(groups["markdown_input_files"]),
        "provider_configured": bool(task.provider_name),
        "has_platform": bool(task.options.get("platform")),
    }
    if condition not in conditions:
        raise WorkflowError(f"Unsupported declarative workflow condition: {condition}")
    return conditions[condition]


def _validate_requirements(definition: Mapping[str, Any], task: TaskSpec, groups: Mapping[str, List[str]]) -> None:
    checks = {
        "input_files": bool(groups["input_files"]),
        "sources": bool(groups["urls"] or groups["input_files"]),
        "yaml_inputs": bool(groups["yaml_input_files"]),
        "document_inputs": bool(groups["document_input_files"]),
        "markdown_inputs": bool(groups["markdown_input_files"]),
        "platform": bool(task.options.get("platform")),
    }
    for requirement in definition.get("requires") or []:
        if requirement not in checks:
            raise WorkflowError(f"Unsupported workflow requirement: {requirement}")
        if not checks[requirement]:
            raise WorkflowError(f"Workflow {task.workflow} requires {requirement}.")


def _resolve_template(value: Any, task: TaskSpec, item: Optional[str] = None) -> Any:
    tokens = {
        "$item": item,
        "$task.goal": task.goal,
        "$task.options.platform": task.options.get("platform", ""),
    }
    if isinstance(value, str):
        if value in tokens:
            return tokens[value]
        rendered = value
        for token, replacement in tokens.items():
            rendered = rendered.replace(token, str(replacement or ""))
        return rendered
    if isinstance(value, list):
        return [_resolve_template(item_value, task, item) for item_value in value]
    if isinstance(value, dict):
        return {str(key): _resolve_template(item_value, task, item) for key, item_value in value.items()}
    return value


def _target_for(task: TaskSpec, tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "web.fetch":
        return f"public web: {urlparse(str(arguments.get('url', ''))).netloc}"
    if tool_name == "web.search":
        return "public web: html.duckduckgo.com"
    if tool_name in {
        "file.read",
        "url_list.read",
        "browser.extract",
        "document.inspect",
        "document.convert",
    }:
        return f"explicit input: {Path(str(arguments.get('path') or arguments.get('config_path', ''))).name}"
    if tool_name == "report.summarize":
        return f"configured model provider: {task.provider_name or 'required provider'}"
    if tool_name == "content.prepare_draft":
        return f"task workspace draft package: {arguments.get('platform', 'unspecified platform')}"
    return "task workspace artifacts"


def _browser_call_details(config_path: str) -> tuple[RiskLevel, str]:
    """Inspect only an explicitly supplied YAML file to make browser targets visible in the plan."""
    from .tools import ApprovedCrawlerSpec, MAX_CRAWLER_CONFIG_BYTES

    path = Path(config_path)
    try:
        if path.stat().st_size > MAX_CRAWLER_CONFIG_BYTES:
            raise ToolError(f"Crawler config exceeded the {MAX_CRAWLER_CONFIG_BYTES} byte safety limit.")
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ToolError(f"Could not read crawler config for planning: {path}") from exc
    except yaml.YAMLError as exc:
        raise ToolError(f"Crawler config is not valid YAML: {path}") from exc
    spec = ApprovedCrawlerSpec.from_mapping(config)
    hosts = sorted(spec.approved_hosts)
    target = f"approved public web hosts declared in {path.name}: {', '.join(hosts)}"
    return RiskLevel.READ, target


def _step(
    task: TaskSpec,
    registry: ToolRegistry,
    index: int,
    description: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> PlanStep:
    tool = registry.validate_call(tool_name, arguments)
    risk = tool.risk
    target = _target_for(task, tool_name, arguments)
    if tool_name == "browser.extract":
        risk, target = _browser_call_details(str(arguments["config_path"]))
    return PlanStep(
        id=f"step-{index:02d}",
        description=description,
        call=ToolCall(tool_name=tool_name, arguments=arguments, risk=risk, target=target),
    )


def build_workflow_plan(
    task: TaskSpec,
    registry: ToolRegistry,
    directory: Optional[Path] = None,
) -> TaskPlan:
    """Compile a restricted, versioned YAML workflow into an approval-gated task plan."""
    path, definition = load_workflow_definition(task.workflow, directory)
    groups = _input_groups(task)
    _validate_requirements(definition, task, groups)
    steps: List[PlanStep] = []

    for raw_step in definition["steps"]:
        if not isinstance(raw_step, dict):
            raise WorkflowError(f"Workflow {task.workflow} contains a non-mapping step.")
        condition = raw_step.get("when")
        if condition is not None and not isinstance(condition, str):
            raise WorkflowError("Workflow step 'when' must be a string.")
        if not _condition_matches(condition, task, groups):
            continue
        tool_name = str(raw_step.get("tool", "")).strip()
        description_template = str(raw_step.get("description", "")).strip() or f"Run {tool_name}"
        arguments_template = raw_step.get("arguments") or {}
        if not isinstance(arguments_template, dict):
            raise WorkflowError("Workflow step arguments must be a mapping.")
        collection = raw_step.get("for_each")
        if collection is not None:
            collection_name = str(collection)
            if collection_name not in groups:
                raise WorkflowError(f"Unsupported workflow collection: {collection_name}")
            values: Iterable[Optional[str]] = groups[collection_name]
        else:
            values = (None,)

        for item in values:
            arguments = _resolve_template(arguments_template, task, item)
            description = str(_resolve_template(description_template, task, item))
            try:
                steps.append(_step(task, registry, len(steps) + 1, description, tool_name, arguments))
            except ToolError as exc:
                raise WorkflowError(f"Workflow {task.workflow} has an invalid step: {exc}") from exc

    if not steps:
        raise WorkflowError(f"Workflow {task.workflow} produced no executable steps for the supplied inputs.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    summary = str(_resolve_template(definition.get("summary", f"Workflow {task.workflow}"), task))
    return TaskPlan(
        task_id=task.id,
        summary=summary,
        steps=steps,
        metadata={
            "workflow_name": task.workflow,
            "definition_path": str(path),
            "definition_sha256": digest,
            "definition_version": definition["version"],
        },
    )


def make_model_step(
    task: TaskSpec,
    registry: ToolRegistry,
    index: int,
    description: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> PlanStep:
    """Validate and normalize an LLM-proposed step before persisting it."""
    if not isinstance(arguments, dict):
        raise ToolError(f"Arguments for {tool_name} must be a JSON object.")
    normalized = dict(arguments)
    if tool_name == "web.fetch":
        requested_url = str(normalized.get("url", ""))
        if requested_url not in task.urls:
            raise ToolError("Model may only fetch a URL explicitly supplied with --url.")
    if tool_name == "web.search" and str(normalized.get("query", "")) != task.goal:
        raise ToolError("Model may only search using the task goal in V1.")
    if tool_name == "report.summarize" and not task.provider_name:
        raise ToolError("Model summary requires a configured provider for this task.")
    if tool_name == "content.prepare_draft":
        platform = str(normalized.get("platform", ""))
        if platform != str(task.options.get("platform", "")):
            raise ToolError("Model may only prepare a draft for the platform explicitly supplied with --platform.")
    for key in ("path", "config_path"):
        if key not in normalized:
            continue
        value = str(normalized[key])
        if not value.startswith("input:"):
            raise ToolError("Model may only refer to supplied files as input:<index>.")
        try:
            position = int(value.split(":", 1)[1])
            normalized[key] = task.input_files[position]
        except (ValueError, IndexError) as exc:
            raise ToolError(f"Unknown input file reference: {value}") from exc
    return _step(task, registry, index, description, tool_name, normalized)
