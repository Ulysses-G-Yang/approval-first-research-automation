from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .models import TaskPlan, TaskSpec
from .providers import ModelProvider, ProviderError
from .registry import ToolError, ToolRegistry
from .workflows import make_model_step


class PlanningError(RuntimeError):
    pass


PLANNER_SYSTEM_PROMPT = """You are the planning component of a local research assistant.
Return exactly one JSON object. You can only propose calls from the supplied registered tool list.
Never propose shell commands, arbitrary Python, browser JavaScript, login, publishing, private-network access,
or plugins. Every source must either be one of the explicit public URLs or an `input:<index>` reference.
Do not invent URLs. Put source-reading steps before transformation/report steps. The final step should normally
be report.compose. Use a concise Chinese description for each step.

Required JSON schema:
{
  "summary": "short Chinese explanation",
  "steps": [
    {"tool_name": "registered.tool", "arguments": {}, "description": "what the approved step does"}
  ]
}
"""


class AgentPlanner:
    def __init__(self, provider: ModelProvider, registry: ToolRegistry):
        self.provider = provider
        self.registry = registry

    async def create_plan(self, task: TaskSpec) -> TaskPlan:
        inputs = [
            {"ref": f"input:{index}", "name": Path(path).name, "suffix": Path(path).suffix.lower()}
            for index, path in enumerate(task.input_files)
        ]
        user_payload = {
            "goal": task.goal,
            "explicit_public_urls": task.urls,
            "explicit_input_files": inputs,
            "has_configured_provider": bool(task.provider_name),
            "registered_tools": self.registry.describe(),
            "constraints": {
                "must_not_fetch_unlisted_urls": True,
                "must_not_read_unlisted_files": True,
                "all_steps_need_individual_approval": True,
            },
        }
        try:
            response = await self.provider.complete_json(
                PLANNER_SYSTEM_PROMPT,
                json.dumps(user_payload, ensure_ascii=False),
            )
        except ProviderError as exc:
            raise PlanningError(f"Model planning failed: {exc}") from exc
        except Exception as exc:
            raise PlanningError(f"Model planning failed unexpectedly: {exc}") from exc

        raw_steps = response.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PlanningError("Model plan did not include any executable steps.")
        if len(raw_steps) > 20:
            raise PlanningError("Model plan exceeded the 20-step V1 safety limit.")

        steps = []
        for index, raw in enumerate(raw_steps, start=1):
            if not isinstance(raw, dict):
                raise PlanningError("Model plan steps must be objects.")
            tool_name = str(raw.get("tool_name", "")).strip()
            description = str(raw.get("description", "")).strip() or f"Run {tool_name}"
            arguments = raw.get("arguments")
            try:
                step = make_model_step(task, self.registry, index, description, tool_name, arguments)
            except ToolError as exc:
                raise PlanningError(f"Unsafe or invalid model step {index}: {exc}") from exc
            steps.append(step)

        self._validate_sequence(steps)

        summary = str(response.get("summary", "")).strip() or f"Model-generated plan for: {task.goal}"
        return TaskPlan(task_id=task.id, summary=summary, steps=steps)

    @staticmethod
    def _validate_sequence(steps) -> None:
        source_tools = {"web.fetch", "web.search", "file.read", "url_list.read", "browser.extract"}
        transformed = False
        has_source = False
        has_dataset = False
        for index, step in enumerate(steps):
            name = step.call.tool_name
            if name in source_tools:
                if transformed:
                    raise PlanningError("Model plan cannot add new sources after data transformation begins.")
                has_source = True
                continue
            if name == "data.normalize":
                if not has_source or has_dataset:
                    raise PlanningError("data.normalize must appear once after at least one source-reading step.")
                has_dataset = True
                transformed = True
                continue
            if name in {"data.to_markdown", "report.summarize"}:
                if not has_dataset:
                    raise PlanningError(f"{name} requires a preceding data.normalize step.")
                transformed = True
                continue
            if name == "report.compose":
                if not has_dataset or index != len(steps) - 1:
                    raise PlanningError("report.compose must be the final step after data.normalize.")
                transformed = True
