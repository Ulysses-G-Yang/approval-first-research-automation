from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _local_link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    target = target.split("#", 1)[0]
    if not target:
        return None
    return (source.parent / target).resolve()


class RepositoryDocumentationTests(unittest.TestCase):
    def test_local_markdown_links_and_assets_exist(self) -> None:
        markdown_files = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "CONTRIBUTING.md"]
        markdown_files.extend((ROOT / "docs").rglob("*.md"))
        markdown_files.extend((ROOT / "examples").rglob("*.md"))
        markdown_files.extend((ROOT / "labs").rglob("*.md"))
        missing: list[str] = []
        for source in markdown_files:
            text = source.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK.findall(text):
                target = _local_link_target(source, raw_target)
                if target is not None and not target.exists():
                    missing.append(f"{source.relative_to(ROOT)} -> {raw_target}")
        self.assertEqual(missing, [])

    def test_social_assets_are_nonempty(self) -> None:
        for name in ("product-preview.png", "workflow-overview.gif"):
            asset = ROOT / "docs" / "assets" / name
            self.assertTrue(asset.is_file(), name)
            self.assertGreater(asset.stat().st_size, 1024, name)
