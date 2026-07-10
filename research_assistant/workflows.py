from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

import yaml

from .models import PlanStep, RiskLevel, TaskPlan, TaskSpec, ToolCall
from .registry import ToolError, ToolRegistry


class WorkflowError(ValueError):
    pass


def _target_for(task: TaskSpec, tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "web.fetch":
        return f"public web: {urlparse(str(arguments.get('url', ''))).netloc}"
    if tool_name == "web.search":
        return "public web: html.duckduckgo.com"
    if tool_name in {"file.read", "url_list.read", "browser.extract"}:
        return f"explicit input: {Path(str(arguments.get('path') or arguments.get('config_path', ''))).name}"
    if tool_name == "report.summarize":
        return f"configured model provider: {task.provider_name or 'required provider'}"
    return "task workspace artifacts"


def _browser_call_details(config_path: str) -> tuple[RiskLevel, str]:
    """Inspect only an explicitly supplied YAML file to make browser targets visible in the plan."""
    path = Path(config_path)
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ToolError(f"Could not read crawler config for planning: {path}") from exc
    except yaml.YAMLError as exc:
        raise ToolError(f"Crawler config is not valid YAML: {path}") from exc
    if not isinstance(config, dict):
        raise ToolError("Crawler config must be a YAML mapping.")
    if config.get("actions"):
        raise ToolError("Agent browser.extract does not permit YAML actions in V1.")
    llm = config.get("llm") or {}
    if isinstance(llm, dict) and llm.get("api_key"):
        raise ToolError("Agent browser.extract does not permit a plaintext LLM API key.")
    urls = [config.get("start_url"), *(config.get("start_urls") or [])]
    hosts = sorted(
        {
            urlparse(str(url)).netloc
            for url in urls
            if isinstance(url, str) and url.strip() and urlparse(url).netloc
        }
    )
    target = f"public web declared in {path.name}: {', '.join(hosts) or 'no valid URL declared'}"
    if isinstance(llm, dict) and llm.get("enable_repair"):
        provider = str(llm.get("provider", "configured LLM"))
        return RiskLevel.SENSITIVE, f"{target}; configured selector-repair provider: {provider}"
    return RiskLevel.READ, target


def _step(task: TaskSpec, registry: ToolRegistry, index: int, description: str, tool_name: str, arguments: Dict[str, Any]) -> PlanStep:
    tool = registry.validate_call(tool_name, arguments)
    risk = tool.risk
    target = _target_for(task, tool_name, arguments)
    if tool_name == "browser.extract":
        risk, target = _browser_call_details(str(arguments["config_path"]))
    return PlanStep(
        id=f"step-{index:02d}",
        description=description,
        call=ToolCall(
            tool_name=tool_name,
            arguments=arguments,
            risk=risk,
            target=target,
        ),
    )


def _source_steps(task: TaskSpec, registry: ToolRegistry) -> Iterable[PlanStep]:
    index = 1
    for url in task.urls:
        yield _step(task, registry, index, f"读取公开网页：{url}", "web.fetch", {"url": url})
        index += 1
    for path in task.input_files:
        yield _step(task, registry, index, f"读取已明确提供的文件：{Path(path).name}", "file.read", {"path": path})
        index += 1


def _crawler_steps(task: TaskSpec, registry: ToolRegistry) -> Iterable[PlanStep]:
    configs = [Path(path) for path in task.input_files if Path(path).suffix.lower() in {".yaml", ".yml"}]
    if not configs:
        raise WorkflowError("crawler_report requires at least one --input YAML crawler config.")
    for index, path in enumerate(configs, start=1):
        yield _step(
            task,
            registry,
            index,
            f"运行已明确提供的爬虫配置：{path.name}",
            "browser.extract",
            {"config_path": str(path)},
        )


def build_workflow_plan(task: TaskSpec, registry: ToolRegistry) -> TaskPlan:
    """Create a predictable plan without calling a model.

    These named workflows are useful for developers and for offline use. The
    `auto` workflow is handled by the LLM planner in :mod:`planner`.
    """
    workflow = task.workflow
    steps = list(_crawler_steps(task, registry)) if workflow == "crawler_report" else list(_source_steps(task, registry))

    if workflow == "research_report" and not steps:
        steps = [_step(task, registry, 1, "根据任务目标搜索公开网页候选来源", "web.search", {"query": task.goal})]
    elif workflow == "file_report" and not task.input_files:
        raise WorkflowError("file_report requires at least one --input file.")
    elif workflow == "web_to_markdown" and not steps:
        raise WorkflowError("web_to_markdown requires at least one --url or --input file.")
    elif workflow not in {"research_report", "file_report", "web_to_markdown", "crawler_report"}:
        raise WorkflowError(f"Unsupported built-in workflow: {workflow}")

    next_index = len(steps) + 1
    steps.append(_step(task, registry, next_index, "去重并整理已批准来源", "data.normalize", {}))
    next_index += 1
    if workflow == "web_to_markdown":
        steps.append(_step(task, registry, next_index, "将整理后的数据转换为 Markdown 表格", "data.to_markdown", {}))
        next_index += 1
    if task.provider_name:
        steps.append(_step(task, registry, next_index, "将已批准数据发送给已配置模型生成摘要", "report.summarize", {}))
        next_index += 1
    steps.append(
        _step(
            task,
            registry,
            next_index,
            "生成带来源清单的 Markdown 报告",
            "report.compose",
            {},
        )
    )
    return TaskPlan(task_id=task.id, summary=f"Built-in {workflow} workflow for: {task.goal}", steps=steps)


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
