import unittest
from datetime import datetime
from zoneinfo import ZoneInfo


from src.platform.scheduling.schedule_parser import normalize_cron_day_of_week_field, preview_schedule


class TestScheduleParser(unittest.TestCase):
    def test_normalize_weekdays_1_5(self):
        """Cron 星期 — POSIX 1-5 转 APScheduler 0-4"""
        self.assertEqual(normalize_cron_day_of_week_field("1-5"), "0-4")

    def test_normalize_sunday_0(self):
        """Cron 星期 — POSIX 0(周日) 转 APScheduler 6"""
        self.assertEqual(normalize_cron_day_of_week_field("0"), "6")

    def test_keep_alpha_dow(self):
        """Cron 星期 — 英文缩写原样保留"""
        self.assertEqual(normalize_cron_day_of_week_field("mon-fri"), "mon-fri")

    def test_preview_monday_hits_monday(self):
        """调度预览 — 周一 09:00 cron 命中周一"""
        tz = "Asia/Shanghai"
        start = datetime(2026, 2, 9, 8, 0, tzinfo=ZoneInfo(tz))  # 2026-02-09 is Monday
        runs = preview_schedule("0 9 * * 1-5", count=1, timezone=tz, start=start)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].weekday(), 0)  # Monday
        self.assertEqual((runs[0].hour, runs[0].minute), (9, 0))


if __name__ == "__main__":
    unittest.main()
