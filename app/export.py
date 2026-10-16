"""The report page as a PDF, rendered by the same headless Chromium.

The browser only ever loads this app's own report URL here (with
?print=1), so the snapshot browser's public-address guard does not apply.
"""
from pathlib import Path


def render_pdf(report_url: str, path: Path) -> Path:
    from playwright.sync_api import sync_playwright
    path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1000, 'height': 1400})
            page.goto(report_url, wait_until='networkidle', timeout=30_000)
            page.emulate_media(media='print')
            page.pdf(path=str(path), format='Letter', print_background=True,
                     margin={'top': '0.5in', 'bottom': '0.6in', 'left': '0.5in', 'right': '0.5in'},
                     display_header_footer=True, header_template='<span></span>',
                     footer_template='<div style="font-size:8px;width:100%;text-align:center;color:#888">'
                                     'Claim Check · <span class="pageNumber"></span>/<span class="totalPages"></span></div>')
        finally:
            browser.close()
    return path
