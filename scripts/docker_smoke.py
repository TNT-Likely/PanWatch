"""Check a built image's imports, PDF rendering, screenshots, and HTTP startup.

Run from the image's /app working directory with the repository mounted read-only:
docker run --rm -v "$PWD:/checks:ro" panwatch:docker-optimized \
    python /checks/scripts/docker_smoke.py
"""

from __future__ import annotations

import asyncio
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path.cwd()))


def check_pdf() -> None:
    from pypdf import PdfReader
    from src.modules.reporting.pdf_export import _render_weasyprint, render_analysis_pdf

    # Test the primary renderer directly so a silent fallback cannot hide missing libraries.
    primary = _render_weasyprint("中文报告", "<p>股票分析与风险提示</p>")
    assert "股票分析" in PdfReader(io.BytesIO(primary)).pages[0].extract_text()
    for title, markdown, language, expected in [
        ("中文报告", "# 广汽集团\n\n**持有**", "zh-CN", "广汽集团"),
        ("Stock report", "# Market analysis\n\n**Hold**", "en-US", "Market analysis"),
    ]:
        pdf = render_analysis_pdf(title, markdown, language=language)
        assert pdf.startswith(b"%PDF")
        assert expected in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    print("PASS: primary PDF renderer and Chinese/English report exports", flush=True)


async def check_screenshot() -> None:
    from src.platform.marketdata.collectors.screenshot_collector import ScreenshotCollector

    collector = ScreenshotCollector()
    try:
        await collector._ensure_browser()
        page = await collector._browser.new_page(viewport={"width": 640, "height": 480})
        await page.set_content("""
            <style>body { font-family: 'Noto Sans CJK SC', sans-serif; }</style>
            <h1>股票行情 Stock chart</h1><canvas id="chart" width="500" height="300"></canvas>
            <script>
              const c = document.getElementById('chart').getContext('2d');
              c.fillStyle = '#16a34a'; c.fillRect(40, 40, 30, 100);
              c.fillStyle = '#dc2626'; c.fillRect(100, 80, 30, 120);
            </script>
        """)
        await page.evaluate("document.fonts.ready")
        png = await page.screenshot()
        from PIL import Image

        image = Image.open(io.BytesIO(png)).convert("RGB")
        assert image.size == (640, 480)
        colors = {color for _, color in image.getcolors(640 * 480)}
        assert (22, 163, 74) in colors and (220, 38, 38) in colors
        assert await page.locator("h1").inner_text() == "股票行情 Stock chart"
    finally:
        await collector.close()
    print("PASS: production screenshot collector and canvas rendering", flush=True)


def check_http_startup() -> None:
    with tempfile.TemporaryDirectory(prefix="panwatch-smoke-") as data_dir:
        with tempfile.TemporaryFile(mode="w+") as log:
            env = {**os.environ, "DATA_DIR": data_dir}
            process = subprocess.Popen([sys.executable, "server.py"], env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as response:
                            assert response.status == 200
                        with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=2) as response:
                            assert b"<html" in response.read().lower()
                        print("PASS: production startup, health endpoint, and frontend", flush=True)
                        return
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(0.5)
                log.seek(0)
                raise AssertionError("HTTP startup failed:\n" + log.read())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> None:
    for name in ("marketdata", "pan_agent", "pan_agent_token_meter", "pan_agent_tool_research", "tradingagents"):
        importlib.import_module(name)
    print("PASS: installed local packages and TradingAgents imports", flush=True)
    check_pdf()
    from server import setup_playwright

    setup_playwright()
    asyncio.run(check_screenshot())
    check_http_startup()


if __name__ == "__main__":
    main()
