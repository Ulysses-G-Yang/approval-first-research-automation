from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .models import Artifact, RiskLevel
from .registry import ToolContext, ToolError, ToolResult


MAX_DOCUMENT_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_DOCUMENT_ASSETS = 100
SUPPORTED_DOCUMENT_SUFFIXES = {".docx", ".pdf", ".md", ".markdown", ".txt"}
IMAGE_REFERENCE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _slug(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()
    return clean[:48] or "document"


def _bundle_name(path: Path) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:10]
    return f"{_slug(path.stem)}-{digest}"


def _safe_extension(value: str, default: str = "bin") -> str:
    clean = value.lower().lstrip(".")
    return clean if re.fullmatch(r"[a-z0-9]{1,10}", clean or "") else default


def _markdown_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return fallback


def _write_asset(
    context: ToolContext,
    bundle: str,
    index: int,
    extension: str,
    content: bytes,
    *,
    source: str,
) -> tuple[Artifact, str]:
    filename = f"documents/{bundle}/assets/image-{index:03d}.{_safe_extension(extension)}"
    artifact = context.workspace.write_bytes_artifact(
        filename,
        content,
        kind="document_asset",
        description=f"Extracted document image from {source}",
        metadata={"bundle": bundle, "source": source},
    )
    return artifact, Path(artifact.path).name if "/assets/" not in artifact.path else f"assets/{Path(artifact.path).name}"


def _resolve_local_image(markdown_path: Path, reference: str) -> Path | None:
    if reference.startswith(("http://", "https://", "data:")):
        return None
    candidate = (markdown_path.parent / reference).resolve()
    base = markdown_path.parent.resolve()
    if candidate == base or base not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _inspect_docx(path: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            media = [name for name in archive.namelist() if name.startswith("word/media/")]
    except zipfile.BadZipFile as exc:
        raise ToolError(f"DOCX archive is invalid: {path.name}") from exc
    return {"format": "docx", "embedded_image_count": len(media)}


def _inspect_pdf(path: Path) -> Dict[str, Any]:
    try:
        import fitz  # type: ignore[import]
    except Exception as exc:  # pragma: no cover - dependency guard
        raise ToolError("PyMuPDF is required for PDF inspection. Install requirements.txt first.") from exc
    try:
        document = fitz.open(path)
        page_count = document.page_count
        image_count = sum(len(document[index].get_images(full=True)) for index in range(page_count))
        return {"format": "pdf", "page_count": page_count, "embedded_image_count": image_count}
    except Exception as exc:
        raise ToolError(f"Could not inspect PDF: {path.name}") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass


def inspect_document(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_SUFFIXES:
        if suffix == ".doc":
            raise ToolError("Legacy .doc files must be converted to .docx before import.")
        raise ToolError("Supported document inputs are .docx, .pdf, .md, .markdown, and .txt.")
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ToolError(f"Document exceeded the {MAX_DOCUMENT_BYTES} byte safety limit.")
    details: Dict[str, Any] = {"path": str(path), "filename": path.name, "bytes": path.stat().st_size}
    if suffix == ".docx":
        details.update(_inspect_docx(path))
    elif suffix == ".pdf":
        details.update(_inspect_pdf(path))
    else:
        details.update({"format": suffix.lstrip("."), "embedded_image_count": 0})
    return details


def _docx_blocks(document: Any) -> Iterable[Tuple[str, Any]]:
    try:
        from docx.oxml.ns import qn  # type: ignore[import]
        from docx.table import Table  # type: ignore[import]
        from docx.text.paragraph import Paragraph  # type: ignore[import]
    except Exception as exc:  # pragma: no cover - dependency guard
        raise ToolError("python-docx is required for DOCX conversion. Install requirements.txt first.") from exc
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "paragraph", Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield "table", Table(child, document)


def _run_markdown(run: Any) -> str:
    text = str(run.text or "").replace("\n", " ")
    if not text:
        return ""
    if getattr(run, "bold", False):
        text = f"**{text}**"
    if getattr(run, "italic", False):
        text = f"*{text}*"
    return text


def _paragraph_prefix(paragraph: Any) -> str:
    style_name = str(getattr(getattr(paragraph, "style", None), "name", "") or "").lower()
    heading = re.search(r"heading\s*(\d+)", style_name)
    if heading:
        return "#" * min(max(int(heading.group(1)), 1), 6) + " "
    if "title" in style_name:
        return "# "
    if "list bullet" in style_name or "bullet" in style_name:
        return "- "
    if "list number" in style_name or "number" in style_name:
        return "1. "
    return ""


def _table_markdown(table: Any) -> str:
    rows = []
    for row in table.rows:
        rows.append([re.sub(r"\s+", " ", cell.text).replace("|", "\\|").strip() for cell in row.cells])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(normalized[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


def _paragraph_image_relationships(paragraph: Any) -> List[str]:
    relationships: List[str] = []
    for run in paragraph.runs:
        try:
            relationships.extend(run._element.xpath(".//a:blip/@r:embed"))
        except Exception:
            continue
    return relationships


def _convert_docx(context: ToolContext, path: Path, bundle: str) -> tuple[str, List[Artifact], List[str]]:
    try:
        from docx import Document  # type: ignore[import]
    except Exception as exc:  # pragma: no cover - dependency guard
        raise ToolError("python-docx is required for DOCX conversion. Install requirements.txt first.") from exc
    document = Document(path)
    lines: List[str] = []
    assets: List[Artifact] = []
    warnings: List[str] = []
    relationship_links: Dict[str, str] = {}

    for kind, block in _docx_blocks(document):
        if kind == "table":
            markdown = _table_markdown(block)
            if markdown:
                lines.extend([markdown, ""])
            continue
        text = "".join(_run_markdown(run) for run in block.runs).strip()
        prefix = _paragraph_prefix(block)
        if text:
            lines.extend([f"{prefix}{text}", ""])
        for relationship_id in _paragraph_image_relationships(block):
            if relationship_id not in relationship_links:
                part = document.part.related_parts.get(relationship_id)
                if part is None:
                    warnings.append(f"Missing DOCX image relationship: {relationship_id}")
                    continue
                if len(assets) >= MAX_DOCUMENT_ASSETS:
                    raise ToolError(f"Document exceeded the {MAX_DOCUMENT_ASSETS} image safety limit.")
                extension = Path(str(part.partname)).suffix or ".bin"
                artifact, relative = _write_asset(
                    context,
                    bundle,
                    len(assets) + 1,
                    extension,
                    part.blob,
                    source=path.name,
                )
                assets.append(artifact)
                relationship_links[relationship_id] = relative
            relative = relationship_links.get(relationship_id)
            if relative:
                lines.extend([f"![{Path(relative).stem}]({relative})", ""])
    if not lines:
        warnings.append("No visible DOCX paragraphs or tables were extracted.")
    return "\n".join(lines).strip() + "\n", assets, warnings


def _convert_pdf(context: ToolContext, path: Path, bundle: str) -> tuple[str, List[Artifact], List[str]]:
    try:
        import fitz  # type: ignore[import]
    except Exception as exc:  # pragma: no cover - dependency guard
        raise ToolError("PyMuPDF is required for PDF conversion. Install requirements.txt first.") from exc
    document = fitz.open(path)
    try:
        if document.page_count > MAX_PDF_PAGES:
            raise ToolError(f"PDF exceeded the {MAX_PDF_PAGES} page safety limit.")
        lines: List[str] = []
        assets: List[Artifact] = []
        warnings: List[str] = []
        extracted_xrefs: set[int] = set()
        for page_index in range(document.page_count):
            page = document[page_index]
            text = page.get_text("text").strip()
            lines.extend([f"## Page {page_index + 1}", ""])
            if text:
                lines.extend([text, ""])
            page_asset_count = 0
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                if xref in extracted_xrefs:
                    continue
                if len(assets) >= MAX_DOCUMENT_ASSETS:
                    raise ToolError(f"Document exceeded the {MAX_DOCUMENT_ASSETS} image safety limit.")
                image = document.extract_image(xref)
                artifact, relative = _write_asset(
                    context,
                    bundle,
                    len(assets) + 1,
                    image.get("ext", "bin"),
                    image["image"],
                    source=f"{path.name} page {page_index + 1}",
                )
                extracted_xrefs.add(xref)
                assets.append(artifact)
                lines.extend([f"![{Path(relative).stem}]({relative})", ""])
                page_asset_count += 1
            if not text and page_asset_count == 0:
                if len(assets) >= MAX_DOCUMENT_ASSETS:
                    raise ToolError(f"Document exceeded the {MAX_DOCUMENT_ASSETS} image safety limit.")
                image = page.get_pixmap(alpha=False).tobytes("png")
                artifact, relative = _write_asset(
                    context,
                    bundle,
                    len(assets) + 1,
                    "png",
                    image,
                    source=f"rendered {path.name} page {page_index + 1}",
                )
                assets.append(artifact)
                lines.extend([f"![page-{page_index + 1}]({relative})", ""])
                warnings.append(f"Page {page_index + 1} has no extractable text; OCR was not run.")
        return "\n".join(lines).strip() + "\n", assets, warnings
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Could not convert PDF: {path.name}") from exc
    finally:
        document.close()


def _convert_markdown_or_text(context: ToolContext, path: Path, bundle: str) -> tuple[str, List[Artifact], List[str]]:
    text = path.read_text(encoding="utf-8-sig")
    warnings: List[str] = []
    assets: List[Artifact] = []
    if path.suffix.lower() == ".txt":
        text = f"# {path.stem}\n\n{text.strip()}\n"

    asset_replacements: Dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        alt_text, reference = match.group(1), match.group(2)
        if reference in asset_replacements:
            return f"![{alt_text}]({asset_replacements[reference]})"
        local_asset = _resolve_local_image(path, reference)
        if local_asset is None:
            if not reference.startswith(("http://", "https://", "data:")):
                warnings.append(f"Missing or unsafe local image reference: {reference}")
            return match.group(0)
        if len(assets) >= MAX_DOCUMENT_ASSETS:
            raise ToolError(f"Document exceeded the {MAX_DOCUMENT_ASSETS} image safety limit.")
        artifact, relative = _write_asset(
            context,
            bundle,
            len(assets) + 1,
            local_asset.suffix,
            local_asset.read_bytes(),
            source=f"{path.name}: {reference}",
        )
        assets.append(artifact)
        asset_replacements[reference] = relative
        return f"![{alt_text}]({relative})"

    return IMAGE_REFERENCE_PATTERN.sub(replace, text).strip() + "\n", assets, warnings


@dataclass
class DocumentInspectTool:
    name: str = "document.inspect"
    description: str = "Inspect one explicitly supplied DOCX, PDF, Markdown, or TXT document without changing it."
    risk: RiskLevel = RiskLevel.READ
    required_arguments: tuple[str, ...] = ("path",)
    allowed_arguments: tuple[str, ...] = ("path",)
    recovery_strategy: str = "local_deterministic"

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        path = context.resolve_input(str(arguments["path"]))
        details = inspect_document(path)
        artifact = context.workspace.write_json_artifact(
            f"documents/{_bundle_name(path)}/inspection.json",
            details,
            kind="document_inspection",
            description=f"Inspection metadata for explicitly approved document: {path.name}",
            metadata={"input_path": str(path), "format": details["format"]},
        )
        return ToolResult(summary=f"Inspected {path.name} ({details['format']}).", artifacts=[artifact], details=details)


@dataclass
class DocumentConvertTool:
    name: str = "document.convert"
    description: str = "Convert one explicitly supplied DOCX, PDF, Markdown, or TXT document into Markdown and local image assets."
    risk: RiskLevel = RiskLevel.WRITE
    required_arguments: tuple[str, ...] = ("path",)
    allowed_arguments: tuple[str, ...] = ("path",)
    recovery_strategy: str = "local_deterministic"

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        path = context.resolve_input(str(arguments["path"]))
        inspection = inspect_document(path)
        bundle = _bundle_name(path)
        suffix = path.suffix.lower()
        if suffix == ".docx":
            markdown, assets, warnings = _convert_docx(context, path, bundle)
        elif suffix == ".pdf":
            markdown, assets, warnings = _convert_pdf(context, path, bundle)
        else:
            markdown, assets, warnings = _convert_markdown_or_text(context, path, bundle)
        title = _markdown_title(markdown, path.stem)
        markdown_artifact = context.workspace.write_text_artifact(
            f"documents/{bundle}/article.md",
            markdown,
            kind="markdown_document",
            description=f"Markdown converted from explicitly approved document: {path.name}",
            metadata={
                "bundle": bundle,
                "input_path": str(path),
                "format": inspection["format"],
                "title": title,
                "asset_ids": [artifact.id for artifact in assets],
                "warnings": warnings,
            },
        )
        manifest_artifact = context.workspace.write_json_artifact(
            f"documents/{bundle}/source-manifest.json",
            {
                "source": inspection,
                "title": title,
                "markdown_artifact_id": markdown_artifact.id,
                "asset_ids": [artifact.id for artifact in assets],
                "warnings": warnings,
            },
            kind="document_manifest",
            description=f"Conversion manifest for {path.name}",
            metadata={"bundle": bundle, "asset_count": len(assets)},
        )
        return ToolResult(
            summary=f"Converted {path.name} to Markdown with {len(assets)} local image assets.",
            artifacts=[*assets, markdown_artifact, manifest_artifact],
            details={"title": title, "asset_count": len(assets), "warnings": warnings},
        )


@dataclass
class MarkdownValidateTool:
    name: str = "markdown.validate"
    description: str = "Validate generated Markdown documents and their local image references."
    risk: RiskLevel = RiskLevel.WRITE
    required_arguments: tuple[str, ...] = ()
    allowed_arguments: tuple[str, ...] = ()
    recovery_strategy: str = "local_deterministic"

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        documents = context.artifacts("markdown_document")
        if not documents:
            raise ToolError("markdown.validate requires at least one completed document.convert step.")
        document_results: List[Dict[str, Any]] = []
        missing: List[str] = []
        for artifact in documents:
            markdown_path = context.workspace.artifact_path(artifact)
            markdown = context.workspace.read_artifact_text(artifact)
            references = [match.group(2) for match in IMAGE_REFERENCE_PATTERN.finditer(markdown)]
            local_missing: List[str] = []
            remote_references: List[str] = []
            for reference in references:
                if reference.startswith(("http://", "https://", "data:")):
                    remote_references.append(reference)
                    continue
                candidate = (markdown_path.parent / reference).resolve()
                if (
                    candidate == context.workspace.root
                    or context.workspace.root not in candidate.parents
                    or not candidate.is_file()
                ):
                    local_missing.append(reference)
                    missing.append(f"{artifact.path}: {reference}")
            document_results.append(
                {
                    "markdown_artifact_id": artifact.id,
                    "path": artifact.path,
                    "image_reference_count": len(references),
                    "missing_local_references": local_missing,
                    "remote_references": remote_references,
                }
            )
        validation = {"valid": not missing, "documents": document_results, "missing": missing}
        artifact = context.workspace.write_json_artifact(
            "documents/markdown-validation.json",
            validation,
            kind="markdown_validation",
            description="Validation result for generated Markdown documents and local image references",
            metadata={"valid": not missing, "document_count": len(documents)},
        )
        if missing:
            raise ToolError(f"Markdown validation found {len(missing)} missing local image references.")
        return ToolResult(
            summary=f"Validated {len(documents)} Markdown document bundle(s).",
            artifacts=[artifact],
            details=validation,
        )
