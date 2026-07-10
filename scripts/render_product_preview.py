from __future__ import annotations

"""Render the checked-in social preview from local HTML with Playwright."""

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "assets" / "product-preview.png"
STAGES = ("all", "collect", "verify", "compose")


def page_html(active_stage: str) -> str:
    stages = [
        ("collect", "01", "Collect", "Public web pages\nLocal files", "URLs, DOCX, PDF, CSV"),
        ("verify", "02", "Verify", "Plan and approve\neach action", "Sources, risk, audit log"),
        ("compose", "03", "Compose", "Markdown, datasets\nand draft packages", "Artifacts you can review"),
    ]
    cards = [
        f"""
        <section class=\"stage {'active' if active_stage in {'all', slug} else ''}\">
          <div class=\"stage-number\">{number}</div>
          <h2>{title}</h2>
          <p>{body.replace(chr(10), '<br>')}</p>
          <span>{caption}</span>
        </section>
        """
        for slug, number, title, body, caption in stages
    ]
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; margin: 0; }}
    body {{
      background: #10201d;
      color: #f2f8f4;
      font-family: Arial, Helvetica, sans-serif;
    }}
    main {{
      width: 1280px;
      height: 640px;
      padding: 62px 72px 52px;
      display: grid;
      grid-template-rows: auto 1fr auto;
    }}
    .eyebrow {{
      color: #74d8a8;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 1.3px;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 10px 0 10px;
      max-width: 860px;
      color: #fffdf7;
      font-size: 50px;
      line-height: 1.05;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin: 0;
      color: #c9dbd2;
      font-size: 21px;
      line-height: 1.42;
    }}
    .flow {{
      align-self: center;
      display: grid;
      grid-template-columns: 1fr 62px 1fr 62px 1fr;
      gap: 0;
      align-items: center;
      margin-top: 26px;
    }}
    .stage {{
      min-height: 220px;
      padding: 25px 27px 22px;
      border: 1px solid #3c5a52;
      border-radius: 8px;
      background: #16302b;
      opacity: 0.48;
    }}
    .stage.active {{
      border-color: #74d8a8;
      background: #1a3c34;
      opacity: 1;
    }}
    .stage-number {{
      color: #efbd55;
      font-size: 17px;
      font-weight: 700;
    }}
    h2 {{
      margin: 17px 0 10px;
      font-size: 30px;
      letter-spacing: 0;
    }}
    .stage p {{
      min-height: 58px;
      margin: 0;
      color: #e2ece6;
      font-size: 18px;
      line-height: 1.42;
    }}
    .stage span {{
      display: block;
      margin-top: 20px;
      color: #9fc4b4;
      font-size: 14px;
    }}
    .arrow {{
      position: relative;
      height: 2px;
      background: #74d8a8;
    }}
    .arrow::after {{
      content: \"\";
      position: absolute;
      top: -5px;
      right: 0;
      width: 10px;
      height: 10px;
      border-top: 2px solid #74d8a8;
      border-right: 2px solid #74d8a8;
      transform: rotate(45deg);
    }}
    footer {{
      display: flex;
      justify-content: space-between;
      color: #9fc4b4;
      font-size: 15px;
    }}
    footer strong {{ color: #fffdf7; }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class=\"eyebrow\">Approval-first AI research automation</div>
      <h1>Web and files become evidence you can review.</h1>
      <p class=\"subtitle\">The model proposes the plan. You approve every action.</p>
    </header>
    <div class=\"flow\">
      {cards[0]}
      <div class=\"arrow\"></div>
      {cards[1]}
      <div class=\"arrow\"></div>
      {cards[2]}
    </div>
    <footer><span>Public sources and explicitly supplied local files</span><strong>Collect -> Verify -> Compose</strong></footer>
  </main>
</body>
</html>"""


def render(output: Path, active_stage: str) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Playwright is required to render the product preview.") from exc
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 640}, device_scale_factor=1)
            page.set_content(page_html(active_stage), wait_until="load")
            page.screenshot(path=str(output), type="png")
        finally:
            browser.close()
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the local product preview image.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stage", choices=STAGES, default="all")
    args = parser.parse_args(argv)
    print(render(args.output, args.stage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
