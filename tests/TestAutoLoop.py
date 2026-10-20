"""用独立游戏状态与虚拟时钟复现自主循环的延迟启动和长战斗。"""

import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.tasks.daily.daily_public_mixin import DailyPublicMixin
from src.tasks.DailyTaskRunner import DailyTaskRunner


class LoopScreen(DailyPublicMixin):
    def __init__(self, *, duration=900, start_delay=3.5, confirm=False, config=None, blocked=None, hide_start=False):
        """设置模拟页面、虚拟时钟，以及启动延迟、战斗时长和点击丢失条件。"""
        self.config = {"公共区/调度室": True, "自主循环": True, "购买免费礼包": True, **(config or {})}
        self.box = SimpleNamespace(
            _claim="claim", left="left", top="top", center="center", bottom="bottom", bottom_left="bottom_left"
        )
        self.now = 0.0
        self.duration = duration
        self.start_delay = start_delay
        self.confirm = confirm
        self.hide_start = hide_start
        self.blocked = blocked
        self.page = "public"
        self.start_at = None
        self.started_at = None
        self.infos = {}
        self.screenshots = []
        self.shop_visits = 0
        self.start_clicks = 0
        self.home_during_loop = 0

    def sleep(self, seconds):
        """推进虚拟时间并同步游戏状态，无需实际等待。"""
        self.now += seconds
        self._update()

    def next_frame(self):
        """模拟下一帧经过的时间，让生产轮询逻辑驱动状态变化。"""
        self.sleep(0.1)

    def _update(self):
        """按预定时刻进入战斗或结束循环，独立于脚本判断更新页面。"""
        if self.start_at is not None and self.now >= self.start_at and self.started_at is None:
            self.started_at = self.start_at
            self.page = "running"
        if self.started_at is not None and self.page == "running" and self.now >= self.started_at + self.duration:
            self.page = "ended"

    def ocr(self, *, match, box=None, **kwargs):
        """按当前页面和识别区域返回匹配文本，兼容字符串与正则。"""
        self._update()
        labels = {
            "public": ["委托", "自主循环"],
            "menu": ["开始循环"],
            "confirm": ["开始循环", "确认"],
            "running": ["循环进行中—"],
            "ended": ["循环结束"],
            "settlement": ["确认"],
        }.get(self.page, [])
        # 确认按钮位于中心，不能用底部框误读。
        if self.page == "confirm" and box == "bottom_left":
            labels = ["开始循环"]
        patterns = match if isinstance(match, list) else [match]
        return [
            SimpleNamespace(name=label)
            for label in labels
            if any(
                pattern.search(label) if isinstance(pattern, re.Pattern) else pattern == label for pattern in patterns
            )
        ]

    def wait_ocr(self, *, match, time_out=5, box=None, **kwargs):
        """在虚拟截止时间内轮询文本，未命中则返回空结果。"""
        deadline = self.now + time_out
        while True:
            if found := self.ocr(match=match, box=box):
                return found
            if self.now >= deadline:
                return None
            self.sleep(min(0.5, deadline - self.now))

    def wait_click_ocr(self, *, match, time_out=5, box=None, after_sleep=0, **kwargs):
        """等待并点击文本，模拟确认、延迟启动、结算和点击丢失。"""
        if not (found := self.wait_ocr(match=match, time_out=time_out, box=box)):
            return None
        label = found[0].name
        if label != self.blocked:
            if label == "自主循环":
                self.page = "menu"
            elif label == "开始循环":
                self.start_clicks += 1
                if self.confirm:
                    self.page = "confirm"
                else:
                    self.start_at = self.now + self.start_delay
                    if self.hide_start:
                        self.page = "loading"
            elif label == "确认" and self.page == "confirm":
                self.page = "loading"
                self.start_at = self.now + self.start_delay
            elif label == "循环结束":
                self.page = "settlement"
            elif label == "确认" and self.page == "settlement":
                self.page = "public"
        self.sleep(after_sleep)
        return found

    def find_feature(self, **kwargs):
        """提供公共区的两个委托按钮，使测试进入真实自主循环流程。"""
        return [object(), object()]

    def click_box_by_match_position(self, *args, **kwargs):
        """省略与循环状态无关的委托入口点击。"""
        pass

    def click(self, *args, **kwargs):
        """省略委托按钮点击，循环相关点击由 OCR 点击方法处理。"""
        pass

    def wait_pop_up(self, **kwargs):
        """省略委托奖励弹窗，避免给循环模型引入无关状态。"""
        pass

    def back(self, **kwargs):
        """省略委托奖励界面的返回操作。"""
        pass

    def info_set(self, key, value):
        """保存任务信息，供编排与异常归属断言使用。"""
        self.infos[key] = value

    def log_info(self, *args, **kwargs):
        """屏蔽设备日志，保持测试输出只包含验证结果。"""
        pass

    def tr(self, text):
        """原样返回中文模板，避免测试依赖全局语言配置。"""
        return text

    def screenshot(self, name):
        """记录截图名称，模拟编排器保存异常现场。"""
        self.screenshots.append(name)

    def ensure_main(self, **kwargs):
        """拒绝从未完成的循环返回主页，并记录错误导航次数。"""
        if self.page in {"running", "loading", "menu", "confirm", "settlement", "ended"}:
            self.home_during_loop += 1
            raise RuntimeError("请从游戏主页进入")

    def shopping(self):
        """记录后续商城访问，检查脚本是否过早进入下一任务。"""
        self.shop_visits += 1


class AutoLoopTest(unittest.TestCase):
    def run_daily(self, task):
        """用虚拟时钟执行公共区与商城的真实编排器。"""
        runner = DailyTaskRunner(task, [("公共区/调度室", task.gongongqu), ("购买免费礼包", task.shopping)])
        with patch("src.tasks.daily.daily_public_mixin.time.time", side_effect=lambda: task.now):
            runner.run()
        return runner

    def test_delayed_start_is_accepted_without_reclicking_missing_start_button(self):
        """验证延迟启动不会导致重复点击或提前访问商城。"""
        task = LoopScreen(duration=15)
        runner = self.run_daily(task)
        self.assertEqual("完成", runner.final_summary["status"])
        self.assertEqual(1, task.start_clicks)
        self.assertEqual(1, task.shop_visits)
        self.assertEqual(0, task.home_during_loop)

    def test_confirmed_start_waits_for_end_before_next_task(self):
        """验证需要确认的循环先结束战斗，再执行后续商城任务。"""
        task = LoopScreen(duration=15, confirm=True, start_delay=0)
        runner = self.run_daily(task)
        self.assertEqual("完成", runner.final_summary["status"])
        self.assertGreaterEqual(task.now, task.started_at + 15)
        self.assertEqual(1, task.shop_visits)

    def test_start_button_disappearing_waits_for_loading_without_confirmation(self):
        """验证按钮消失后的十秒加载仍可被识别为免确认启动。"""
        task = LoopScreen(duration=15, start_delay=10, hide_start=True)
        self.run_daily(task)
        self.assertEqual(1, task.start_clicks)
        self.assertEqual(1, task.shop_visits)
        self.assertGreaterEqual(task.now, task.started_at + 15)

    def test_long_battle_can_finish_after_ten_minutes(self):
        """验证默认等待允许十五分钟战斗完成，覆盖原十分钟超时问题。"""
        task = LoopScreen(start_delay=0)
        self.run_daily(task)
        self.assertGreaterEqual(task.now, task.started_at + 900)
        self.assertEqual(1, task.shop_visits)

    def test_wait_limit_can_be_extended_per_config(self):
        """验证自定义两小时上限允许超过默认一小时的战斗完成。"""
        task = LoopScreen(duration=4000, start_delay=0, config={"自主循环等待上限（分钟）": 120})
        self.run_daily(task)
        self.assertEqual(1, task.shop_visits)

    def test_loop_ending_during_start_transition_is_still_settled(self):
        """验证启动过渡期间已结束的循环仍执行结算并继续商城。"""
        task = LoopScreen(duration=0, start_delay=0)
        self.run_daily(task)
        self.assertEqual("public", task.page)
        self.assertEqual(1, task.shop_visits)

    def test_disabled_auto_loop_does_not_start_battle(self):
        """验证关闭自主循环后不会发起战斗，仍继续普通日常。"""
        task = LoopScreen(config={"自主循环": False})
        self.run_daily(task)
        self.assertEqual(0, task.start_clicks)
        self.assertEqual(1, task.shop_visits)

    def test_timeout_is_attributed_to_public_area_without_home_navigation(self):
        """验证超时归属公共区、保留战斗现场，并阻止主页导航与商城。"""
        task = LoopScreen(duration=10000, start_delay=0, config={"自主循环等待上限（分钟）": 1})
        runner = DailyTaskRunner(task, [("公共区/调度室", task.gongongqu), ("购买免费礼包", task.shopping)])
        with (
            patch("src.tasks.daily.daily_public_mixin.time.time", side_effect=lambda: task.now),
            self.assertRaisesRegex(TimeoutError, "自主循环"),
        ):
            runner.run()
        self.assertEqual("公共区/调度室", runner.final_summary["current_task"])
        self.assertEqual(0, task.shop_visits)
        self.assertEqual(0, task.home_during_loop)
        self.assertEqual("running", task.page)
        self.assertLess(task.now, 90)

    def test_failed_start_stops_before_shop_and_records_public_area(self):
        """验证开始循环按钮无效时就地报错，不尝试返回主页或商城。"""
        task = LoopScreen(blocked="开始循环")
        with self.assertRaisesRegex(RuntimeError, "自主循环"):
            self.run_daily(task)
        self.assertEqual(0, task.shop_visits)
        self.assertEqual(0, task.home_during_loop)

    def test_failed_entry_stops_before_starting_battle(self):
        """验证循环入口未打开时立即中断，不能发起战斗。"""
        task = LoopScreen(blocked="自主循环")
        with self.assertRaisesRegex(RuntimeError, "自主循环入口"):
            self.run_daily(task)
        self.assertEqual(0, task.start_clicks)
        self.assertEqual(0, task.home_during_loop)

    def test_failed_start_confirmation_stops_before_next_task(self):
        """验证启动确认无效时阻止后续任务，游戏不会被误判为已启动。"""
        task = LoopScreen(confirm=True, blocked="确认")
        with self.assertRaisesRegex(RuntimeError, "自主循环启动确认"):
            self.run_daily(task)
        self.assertIsNone(task.started_at)
        self.assertEqual(0, task.home_during_loop)

    def test_failed_settlement_does_not_advance_to_shop(self):
        """验证结算确认无效时保留结算页面，不进入商城。"""
        task = LoopScreen(duration=15, start_delay=0, blocked="确认")
        with self.assertRaisesRegex(RuntimeError, "自主循环"):
            self.run_daily(task)
        self.assertEqual(0, task.home_during_loop)
        self.assertEqual(0, task.shop_visits)

    def test_invalid_limit_fails_before_starting_loop(self):
        """验证越界或不可解析的等待上限在点击启动前被拒绝。"""
        for value in (0, -1, 241, "invalid", None):
            with self.subTest(value=value):
                task = LoopScreen(config={"自主循环等待上限（分钟）": value})
                with self.assertRaisesRegex(ValueError, "自主循环"):
                    self.run_daily(task)
                self.assertEqual(0, task.start_clicks)
