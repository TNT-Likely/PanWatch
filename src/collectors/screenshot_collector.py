"""K线图截图采集器 - 基于 Playwright"""
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.modules.market.cn_symbol import get_cn_prefix

logger = logging.getLogger(__name__)

# 截图保存目录
SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "panwatch_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# 默认配置
DEFAULT_CONFIG = {
    "viewport": {"width": 1280, "height": 900},
    "wait_selector": ".quote_title",  # 等待页面主体加载
    "extra_wait_ms": 3000,  # 等待图表渲染
}


@dataclass
class ChartScreenshot:
    """K线图截图"""
    symbol: str
    name: str
    market: str
    filepath: str
    period: str = "daily"  # daily/weekly/monthly
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def exists(self) -> bool:
        return os.path.exists(self.filepath)


class ScreenshotCollector:
    """
    K线图截图采集器

    使用 Playwright 截取东方财富 K 线图
    URL 格式:
    - A股: https://quote.eastmoney.com/{sh|sz}{symbol}.html
    - 港股: https://quote.eastmoney.com/hk/{symbol}.html
    """

    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self):
        """懒加载初始化 Playwright（带反检测设置）"""
        if self._browser is not None:
            return

        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()

            # 使用反检测设置启动浏览器
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )
            logger.info("Playwright 浏览器已启动")
        except ImportError:
            raise RuntimeError("请先安装 playwright: pip install playwright && playwright install chromium")
        except Exception as e:
            logger.error(f"Playwright 启动失败: {e}")
            raise

    def _get_url(self, symbol: str, market: str, provider: str = "xueqiu") -> str:
        """生成 K 线图页面 URL"""
        if provider == "sina":
            return self._get_sina_url(symbol, market)
        elif provider == "xueqiu":
            return self._get_xueqiu_url(symbol, market)
        else:
            return self._get_eastmoney_url(symbol, market)

    def _get_sina_url(self, symbol: str, market: str) -> str:
        """新浪财经 URL"""
        if market.upper() == "HK":
            return f"https://stock.finance.sina.com.cn/hkstock/quotes/{symbol}.html"
        # A股
        prefix = get_cn_prefix(symbol)
        return f"https://finance.sina.com.cn/realstock/company/{prefix}{symbol}/nc.shtml"

    def _get_eastmoney_url(self, symbol: str, market: str) -> str:
        """东方财富 URL"""
        if market.upper() == "HK":
            return f"https://quote.eastmoney.com/hk/{symbol}.html"
        # A股
        prefix = get_cn_prefix(symbol)
        return f"https://quote.eastmoney.com/{prefix}{symbol}.html"

    def _get_xueqiu_url(self, symbol: str, market: str) -> str:
        """雪球 URL"""
        if market.upper() == "HK":
            return f"https://xueqiu.com/S/{symbol}"
        # A股
        prefix = get_cn_prefix(symbol, upper=True)
        return f"https://xueqiu.com/S/{prefix}{symbol}"

    async def capture(
        self,
        symbol: str,
        name: str,
        market: str = "CN",
        period: str = "daily",
        provider: str = "xueqiu",
    ) -> ChartScreenshot | None:
        """
        截取单只股票的 K 线图

        Args:
            symbol: 股票代码
            name: 股票名称
            market: 市场 (CN/HK)
            period: K线周期 (daily/weekly/monthly)
            provider: 数据源 (xueqiu/eastmoney)

        Returns:
            ChartScreenshot 或 None（失败时）
        """
        await self._ensure_browser()

        url = self._get_url(symbol, market, provider)
        filepath = str(SCREENSHOT_DIR / f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")

        try:
            # 使用真实的 User-Agent 和反检测设置
            context = await self._browser.new_context(
                viewport=self.config["viewport"],
                locale="zh-CN",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                java_script_enabled=True,
                bypass_csp=True,
            )
            page = await context.new_page()

            # 注入反检测脚本
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
                window.chrome = { runtime: {} };
            """)

            logger.debug(f"正在加载 {name}({symbol}) K线图: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 等待页面主体加载
            try:
                await page.wait_for_selector(
                    self.config["wait_selector"],
                    timeout=15000,
                    state="visible",
                )
            except Exception:
                # 备选：等待任意内容加载
                await page.wait_for_load_state("networkidle", timeout=10000)

            # 额外等待渲染
            await page.wait_for_timeout(self.config["extra_wait_ms"])

            # 根据数据源执行不同的截图逻辑
            if provider == "xueqiu":
                await self._capture_xueqiu(page, filepath, period)
            elif provider == "sina":
                await self._capture_sina(page, filepath, period)
            else:
                await self._capture_eastmoney(page, filepath, period)

            await context.close()

            logger.info(f"截图成功: {name}({symbol}) -> {filepath}")
            return ChartScreenshot(
                symbol=symbol,
                name=name,
                market=market,
                filepath=filepath,
                period=period,
            )

        except Exception as e:
            logger.error(f"截图失败 {name}({symbol}): {e}")
            return None

    async def _capture_xueqiu(self, page, filepath: str, period: str):
        """雪球截图逻辑"""
        # 等待页面加载
        await page.wait_for_timeout(1000)

        # 关闭所有可能的弹窗
        await self._close_xueqiu_popups(page)

        # 等待图表加载
        try:
            await page.wait_for_selector(".stock-chart", timeout=10000)
        except Exception:
            pass

        # 切换到日K（默认是分时图）
        await self._switch_to_daily_kline(page)

        # 如果需要其他周期再切换
        if period == "weekly":
            await self._switch_period_xueqiu(page, "weekly")
        elif period == "monthly":
            await self._switch_period_xueqiu(page, "monthly")

        # 直接截取固定区域（K线图区域）
        await page.screenshot(
            path=filepath,
            clip={"x": 250, "y": 80, "width": 660, "height": 720}
        )
        logger.debug("雪球 K 线图截图完成")

    async def _capture_sina(self, page, filepath: str, period: str):
        """新浪财经截图逻辑"""
        # 等待页面加载
        try:
            await page.wait_for_selector("#kline_container", timeout=10000)
        except Exception:
            pass

        # 截取 K 线图区域
        try:
            chart = await page.query_selector("#kline_container")
            if chart:
                await chart.screenshot(path=filepath)
                return
        except Exception:
            pass

        await page.screenshot(path=filepath, full_page=False)

    async def _capture_eastmoney(self, page, filepath: str, period: str):
        """东方财富截图逻辑"""
        # 滚动到 K 线图区域
        try:
            kline_area = await page.query_selector("#app > div > div > div.quote_title.self_clearfix")
            if kline_area:
                await kline_area.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
        except Exception:
            pass

        # 尝试切换周期
        if period != "daily":
            await self._switch_period(page, period)

        # 截图 K 线图区域
        try:
            kline_container = await page.query_selector("#kline_div")
            if kline_container:
                await kline_container.screenshot(path=filepath)
                return
        except Exception:
            pass

        await page.screenshot(path=filepath, full_page=False)

    async def _close_xueqiu_popups(self, page):
        """关闭雪球所有弹窗"""
        # 多次尝试关闭各种弹窗
        for _ in range(5):
            closed = False

            # 1. 关闭登录弹窗（点击"跳过"）
            try:
                skip_btn = await page.query_selector('text="跳过"')
                if skip_btn and await skip_btn.is_visible():
                    await skip_btn.click()
                    await page.wait_for_timeout(500)
                    logger.debug("已关闭登录弹窗")
                    closed = True
            except Exception:
                pass

            # 2. 关闭邀请加群弹窗（点击 X 按钮）
            try:
                # 弹窗右上角的关闭按钮
                close_btns = await page.query_selector_all('svg, .close, [class*="close"], [class*="Close"]')
                for btn in close_btns:
                    try:
                        if await btn.is_visible():
                            box = await btn.bounding_box()
                            # 只点击在弹窗区域内的关闭按钮
                            if box and box["x"] > 200 and box["y"] < 500:
                                await btn.click()
                                await page.wait_for_timeout(500)
                                logger.debug("已关闭弹窗")
                                closed = True
                                break
                    except Exception:
                        continue
            except Exception:
                pass

            # 3. 按 ESC 键
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
            except Exception:
                pass

            # 4. 点击遮罩层关闭
            try:
                mask = await page.query_selector('.modal-mask, .overlay, [class*="mask"]')
                if mask and await mask.is_visible():
                    await mask.click()
                    await page.wait_for_timeout(500)
                    closed = True
            except Exception:
                pass

            if not closed:
                break
            await page.wait_for_timeout(300)

    async def _switch_to_daily_kline(self, page):
        """雪球切换到日K线图"""
        try:
            # 点击"日K"按钮
            daily_btn = await page.query_selector('text="日K"')
            if daily_btn and await daily_btn.is_visible():
                await daily_btn.click()
                await page.wait_for_timeout(1500)
                logger.debug("已切换到日K线图")
        except Exception as e:
            logger.debug(f"切换日K失败: {e}")

    async def _switch_period_xueqiu(self, page, period: str):
        """雪球切换K线周期"""
        period_text = {"weekly": "周K", "monthly": "月K"}.get(period)
        if not period_text:
            return
        try:
            btn = await page.query_selector(f'text="{period_text}"')
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1500)
                logger.debug(f"已切换到{period_text}")
        except Exception:
            pass

    async def _close_popups(self, page):
        """关闭弹窗广告"""
        # 常见的关闭按钮选择器
        close_selectors = [
            'text="关闭"',
            'text="×"',
            'text="X"',
            '.close-btn',
            '.modal-close',
            '[class*="close"]',
            'button:has-text("关闭")',
            'a:has-text("关闭")',
            '.layui-layer-close',
            '.popup-close',
        ]

        for selector in close_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(500)
                    logger.debug(f"关闭弹窗: {selector}")
            except Exception:
                continue

        # 按 ESC 键关闭可能的弹窗
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass

    async def _switch_period(self, page, period: str):
        """切换K线周期"""
        period_map = {
            "weekly": ["周K", "周线", "week"],
            "monthly": ["月K", "月线", "month"],
        }
        keywords = period_map.get(period, [])

        for keyword in keywords:
            try:
                btn = await page.query_selector(f'text="{keyword}"')
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    return
            except Exception:
                continue

    async def capture_batch(
        self,
        stocks: list[dict],
        period: str = "daily",
        provider: str = "xueqiu",
    ) -> list[ChartScreenshot]:
        """
        批量截取K线图

        Args:
            stocks: 股票列表，每项包含 symbol, name, market
            period: K线周期
            provider: 数据源 (xueqiu/eastmoney)

        Returns:
            ChartScreenshot 列表
        """
        results = []
        for stock in stocks:
            screenshot = await self.capture(
                symbol=stock.get("symbol", ""),
                name=stock.get("name", ""),
                market=stock.get("market", "CN"),
                period=period,
                provider=provider,
            )
            if screenshot:
                results.append(screenshot)

        return results

    def cleanup_old_screenshots(self, max_age_hours: int = 24):
        """清理过期截图"""
        cutoff = datetime.now().timestamp() - max_age_hours * 3600
        cleaned = 0

        for filepath in SCREENSHOT_DIR.glob("*.png"):
            if filepath.stat().st_mtime < cutoff:
                try:
                    filepath.unlink()
                    cleaned += 1
                except Exception as e:
                    logger.debug(f"清理截图失败 {filepath}: {e}")

        if cleaned:
            logger.info(f"清理了 {cleaned} 张过期截图")

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
