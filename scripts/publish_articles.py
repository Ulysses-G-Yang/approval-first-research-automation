#!/usr/bin/env python3
"""Create one article draft at a time, with an explicit opt-in publish mode."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional, Sequence, Tuple

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright


ROOT = Path(__file__).resolve().parents[1]
ARTICLE_PATH = ROOT / "docs" / "articles" / "self-healing-generic-crawler.md"
LEGACY_PROFILE_ROOTS = (
    Path(os.environ.get("TEMP", Path.home() / "AppData" / "Local" / "Temp"))
    / "generic_crawler_publish_states",
    Path(__file__).resolve().parent / "publish_states",
)
DEFAULT_PROFILE_ROOT = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "GenericCrawler"
    / "publish_states"
)


@dataclass(frozen=True)
class PlatformSpec:
    name: str
    editor_url: str
    public_url_prefixes: Tuple[str, ...]
    title_activate_selectors: Tuple[str, ...]
    title_selectors: Tuple[str, ...]
    body_selectors: Tuple[str, ...]
    draft_selectors: Tuple[str, ...]
    publish_selectors: Tuple[str, ...]


PLATFORMS = {
    "juejin": PlatformSpec(
        name="掘金",
        editor_url="https://juejin.cn/editor/drafts/new",
        public_url_prefixes=("https://juejin.cn/post/",),
        title_activate_selectors=(),
        title_selectors=(
            "input[placeholder*='标题']",
            "input[aria-label*='标题']",
            "input[name='title']",
        ),
        body_selectors=(
            ".CodeMirror textarea",
            "textarea.byte-input__textarea",
            ".cm-content[contenteditable='true']",
            ".ProseMirror[contenteditable='true']",
        ),
        draft_selectors=("button:has-text('保存草稿')",),
        publish_selectors=("button:has-text('发布文章')", "button:has-text('发布')"),
    ),
    "zhihu": PlatformSpec(
        name="知乎",
        editor_url="https://zhuanlan.zhihu.com/write",
        public_url_prefixes=("https://zhuanlan.zhihu.com/p/",),
        title_activate_selectors=(),
        title_selectors=(
            "input[placeholder*='标题']",
            "textarea[placeholder*='标题']",
            "input[aria-label*='标题']",
        ),
        body_selectors=(
            ".public-DraftEditor-content[contenteditable='true']",
            "div[contenteditable='true'][role='textbox']",
        ),
        draft_selectors=(),
        publish_selectors=("button:has-text('发布文章')", "button:has-text('发布')"),
    ),
    "csdn": PlatformSpec(
        name="CSDN",
        editor_url="https://editor.csdn.net/md/",
        public_url_prefixes=("https://blog.csdn.net/",),
        title_activate_selectors=(),
        title_selectors=(),
        body_selectors=("pre.editor__inner[contenteditable='true']",),
        draft_selectors=("button.btn-save",),
        publish_selectors=("button.btn-publish",),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="单平台技术文章草稿/发布工具")
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument(
        "--mode",
        choices=("draft", "publish"),
        default="draft",
        help="draft 仅填写并保存草稿；publish 才尝试正式发布。",
    )
    parser.add_argument("--profile-root", type=Path, default=DEFAULT_PROFILE_ROOT)
    parser.add_argument("--login-wait-seconds", type=int, default=0)
    parser.add_argument(
        "--keep-open-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="失败时保留当前单个平台窗口，便于人工处理。",
    )
    return parser.parse_args()


def load_article() -> Tuple[str, str]:
    lines = ARTICLE_PATH.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"文章缺少一级标题: {ARTICLE_PATH}")
    return lines[0][2:].strip(), "\n".join(lines[1:]).lstrip()


def chrome_executable() -> Path:
    candidates = (
        os.environ.get("CHROME_PATH"),
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise RuntimeError("未找到 Chrome。请设置 CHROME_PATH 指向 chrome.exe。")


def profile_dir(profile_root: Path, platform: str) -> Path:
    target = profile_root.expanduser().resolve() / platform
    if any((legacy_root / platform).resolve() == target for legacy_root in LEGACY_PROFILE_ROOTS):
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    migration_marker = target.parent / f".{platform}.legacy-migrated"
    if not migration_marker.exists():
        for legacy_root in LEGACY_PROFILE_ROOTS:
            legacy = legacy_root / platform
            if not legacy.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(legacy, target, dirs_exist_ok=True)
            migration_marker.write_text(str(legacy), encoding="utf-8")
            print(f"已将 {platform} 的本地登录状态迁移到: {target}")
            break
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


async def first_visible(page: Page, selectors: Iterable[str]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def fill_title(
    page: Page,
    selectors: Sequence[str],
    title: str,
    activate_selectors: Sequence[str] = (),
) -> None:
    await click_optional(page, activate_selectors)
    target = await first_visible(page, selectors)
    if target is None:
        raise RuntimeError("未找到文章标题输入框；可能未登录或编辑器页面已改版。")
    try:
        await target.click(timeout=5_000)
    except PlaywrightTimeoutError:
        await target.focus()
    try:
        await target.fill(title)
    except Exception:
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.insert_text(title)


async def insert_body(page: Page, selectors: Sequence[str], content: str) -> None:
    target = await first_visible(page, selectors)
    if target is None:
        raise RuntimeError("未找到文章正文编辑器；可能未登录、未处于 Markdown 模式或页面已改版。")

    try:
        await target.click(timeout=5_000)
    except PlaywrightTimeoutError:
        await target.focus()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    # insert_text dispatches one input operation for the full article and avoids per-character timeouts.
    await page.keyboard.insert_text(content)
    await page.wait_for_timeout(750)


async def click_optional(page: Page, selectors: Sequence[str]) -> bool:
    target = await first_visible(page, selectors)
    if target is None:
        return False
    try:
        await target.click(timeout=5_000)
    except PlaywrightTimeoutError:
        await target.click(force=True)
    return True


def is_editor_url(page: Page, spec: PlatformSpec) -> bool:
    url = page.url.lower()
    return spec.editor_url.split("//", 1)[1].split("/", 1)[0] in url and "search" not in url


async def wait_for_editor(page: Page, spec: PlatformSpec, login_wait_seconds: int) -> None:
    await page.goto(spec.editor_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1_500)
    await click_optional(page, spec.title_activate_selectors)
    has_title = not spec.title_selectors or await first_visible(page, spec.title_selectors)
    if is_editor_url(page, spec) and has_title and await first_visible(page, spec.body_selectors):
        return

    if login_wait_seconds > 0:
        print(f"请在已打开的 {spec.name} 窗口完成登录，最多等待 {login_wait_seconds} 秒。")
        deadline = asyncio.get_running_loop().time() + login_wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            await click_optional(page, spec.title_activate_selectors)
            has_title = not spec.title_selectors or await first_visible(page, spec.title_selectors)
            if is_editor_url(page, spec) and has_title and await first_visible(page, spec.body_selectors):
                return
            await page.wait_for_timeout(1_000)

    raise RuntimeError(f"未进入 {spec.name} 文章编辑页。请确认登录状态后重试。当前地址: {page.url}")


async def wait_for_public_url(page: Page, prefixes: Sequence[str], timeout_ms: int = 20_000) -> Optional[str]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        if any(page.url.startswith(prefix) for prefix in prefixes):
            return page.url
        await page.wait_for_timeout(500)
    return None


async def run_platform(page: Page, spec: PlatformSpec, title: str, content: str, mode: str, login_wait: int) -> Tuple[bool, str, str]:
    await wait_for_editor(page, spec, login_wait)
    if spec.title_selectors:
        await fill_title(page, spec.title_selectors, title, spec.title_activate_selectors)
    else:
        content = f"# {title}\n\n{content}"
    await insert_body(page, spec.body_selectors, content)

    if spec.name == "CSDN":
        await click_optional(page, ("button:has-text('我知道了')",))

    if mode == "draft":
        await click_optional(page, spec.draft_selectors)
        await page.wait_for_timeout(1_000)
        return True, "已填写并保存草稿（平台若支持自动保存则由编辑器保存）", page.url

    if not await click_optional(page, spec.publish_selectors):
        raise RuntimeError("未找到发布按钮，未执行正式发布。")

    public_url = await wait_for_public_url(page, spec.public_url_prefixes)
    if public_url is None:
        raise RuntimeError("未获得公开文章 URL；发布可能仍在确认窗口中，未标记为成功。")
    return True, "发布成功", public_url


async def keep_window_open(page: Page, platform_name: str) -> None:
    print(f"{platform_name} 窗口已保留以便人工处理。完成后请关闭窗口或按 Ctrl+C 结束脚本。")
    while not page.is_closed():
        await page.wait_for_timeout(1_000)


async def main() -> int:
    args = parse_args()
    spec = PLATFORMS[args.platform]
    title, content = load_article()
    user_data_dir = profile_dir(args.profile_root, args.platform)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            executable_path=str(chrome_executable()),
            headless=False,
            viewport=None,
            locale="zh-CN",
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        exit_code = 0
        try:
            ok, message, url = await run_platform(
                page, spec, title, content, args.mode, args.login_wait_seconds
            )
            print(f"[OK] {spec.name}: {message}\n[URL] {url}")
        except PlaywrightTimeoutError as exc:
            print(f"[FAIL] {spec.name}: Playwright 超时: {exc}")
            exit_code = 1
        except Exception as exc:
            print(f"[FAIL] {spec.name}: {exc}")
            exit_code = 1

        if exit_code and args.keep_open_on_failure:
            await keep_window_open(page, spec.name)
        await context.close()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
