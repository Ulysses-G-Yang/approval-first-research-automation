from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .document_tools import IMAGE_REFERENCE_PATTERN, _markdown_title
from .models import Artifact, RiskLevel
from .registry import ToolContext, ToolError, ToolResult


SUPPORTED_DRAFT_PLATFORMS = {"juejin", "zhihu", "csdn"}


def _safe_asset_name(index: int, original: Path) -> str:
    suffix = original.suffix.lower() or ".bin"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", original.stem).strip("-") or f"image-{index:03d}"
    return f"{index:03d}-{stem}{suffix}"


@dataclass
class ContentPrepareDraftTool:
    """Build an offline platform draft package; it never opens a browser or publishes."""

    name: str = "content.prepare_draft"
    description: str = "Prepare a local, platform-specific Markdown draft package from validated document artifacts."
    risk: RiskLevel = RiskLevel.WRITE
    required_arguments: tuple[str, ...] = ("platform",)
    allowed_arguments: tuple[str, ...] = ("platform",)

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        platform = str(arguments["platform"]).strip().lower()
        if platform not in SUPPORTED_DRAFT_PLATFORMS:
            raise ToolError(f"Unsupported draft platform: {platform}")
        validations = context.artifacts("markdown_validation")
        if not validations:
            raise ToolError("content.prepare_draft requires a completed markdown.validate step.")
        validation = context.workspace.read_artifact_json(validations[-1])
        if not isinstance(validation, dict) or not validation.get("valid"):
            raise ToolError("content.prepare_draft requires a successful Markdown validation result.")
        documents = context.artifacts("markdown_document")
        if not documents:
            raise ToolError("content.prepare_draft requires at least one converted Markdown document.")

        artifacts: List[Artifact] = []
        manifest_documents: List[Dict[str, Any]] = []
        for document in documents:
            source_path = context.workspace.artifact_path(document)
            source_markdown = context.workspace.read_artifact_text(document)
            bundle = str(document.metadata.get("bundle") or source_path.parent.name)
            copied_assets: List[Artifact] = []
            asset_replacements: Dict[str, str] = {}

            def replace(match: re.Match[str]) -> str:
                alt_text, reference = match.group(1), match.group(2)
                if reference.startswith(("http://", "https://", "data:")):
                    return match.group(0)
                if reference in asset_replacements:
                    return f"![{alt_text}]({asset_replacements[reference]})"
                candidate = (source_path.parent / reference).resolve()
                if (
                    candidate == context.workspace.root
                    or context.workspace.root not in candidate.parents
                    or not candidate.is_file()
                ):
                    raise ToolError(f"Validated image reference became unavailable: {reference}")
                filename = _safe_asset_name(len(copied_assets) + 1, candidate)
                artifact = context.workspace.write_bytes_artifact(
                    f"drafts/{platform}/{bundle}/assets/{filename}",
                    candidate.read_bytes(),
                    kind="draft_asset",
                    description=f"Offline {platform} draft asset copied from {document.path}",
                    metadata={"platform": platform, "source_document_artifact_id": document.id},
                )
                copied_assets.append(artifact)
                relative = f"assets/{filename}"
                asset_replacements[reference] = relative
                return f"![{alt_text}]({relative})"

            draft_markdown = IMAGE_REFERENCE_PATTERN.sub(replace, source_markdown)
            draft_artifact = context.workspace.write_text_artifact(
                f"drafts/{platform}/{bundle}/article.md",
                draft_markdown,
                kind="draft_markdown",
                description=f"Offline {platform} Markdown draft package; not uploaded or published",
                metadata={"platform": platform, "source_document_artifact_id": document.id},
            )
            artifacts.extend([*copied_assets, draft_artifact])
            manifest_documents.append(
                {
                    "source_document_artifact_id": document.id,
                    "title": _markdown_title(draft_markdown, bundle),
                    "draft_markdown_artifact_id": draft_artifact.id,
                    "draft_asset_ids": [artifact.id for artifact in copied_assets],
                }
            )
        manifest = context.workspace.write_json_artifact(
            f"drafts/{platform}/draft-manifest.json",
            {
                "platform": platform,
                "state": "prepared_offline",
                "network_access": False,
                "published": False,
                "documents": manifest_documents,
                "next_action": "Use a reviewed platform adapter to save a draft; formal publishing requires a separate approval.",
            },
            kind="draft_manifest",
            description=f"Offline {platform} draft manifest; no login, upload, or publication occurred",
            metadata={"platform": platform, "document_count": len(manifest_documents)},
        )
        artifacts.append(manifest)
        return ToolResult(
            summary=f"Prepared {len(manifest_documents)} offline {platform} draft package(s); nothing was uploaded or published.",
            artifacts=artifacts,
            details={"platform": platform, "published": False, "network_access": False},
        )
