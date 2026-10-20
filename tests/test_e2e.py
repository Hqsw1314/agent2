# -*- coding: utf-8 -*-
"""端到端集成测试：真实 workflow.yaml + MockLLM 离线跑通全流程。

验证: 流程完整性、审查-修复循环、消息传递、状态共享、产物落盘。
运行: .venv/Scripts/python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from miniflow.engine import WorkflowEngine
from miniflow.llm import MockLLM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_wf(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestMainWorkflowE2E(unittest.TestCase):
    """主流程: 需求 -> 设计 -> 编码 -> 审查 -> (修复循环) -> 交付"""

    @classmethod
    def setUpClass(cls):
        cls.wf = load_wf("workflow.yaml")

    def test_end_to_end_with_review_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = WorkflowEngine(self.wf, MockLLM(), out_dir=tmp)
            ctx = engine.run(topic="测试主题: 五子棋对战程序")

            # 1) 流程完整性: 全部产物生成
            for key in ("requirements", "design", "code", "review", "final_report"):
                self.assertIn(key, ctx.data)

            # 2) 审查-修复循环真实发生: 首查未过(62), 修复后复审通过(88)
            self.assertEqual(ctx.visits.get("review"), 2)
            self.assertEqual(ctx.visits.get("fix"), 1)
            self.assertTrue(ctx.get("review")["passed"] is True)
            self.assertEqual(len(ctx.get("fix_history")), 1)

            # 3) 消息传递: 六个节点的交接消息完整记录
            contents = " | ".join(m.content for m in ctx.messages)
            self.assertIn("需求文档已就绪", contents)
            self.assertIn("审查不通过", contents)
            self.assertIn("审查通过", contents)
            self.assertEqual(len(ctx.messages), 6)

            # 4) 状态共享: 设计Agent引用了需求Agent的概述
            self.assertEqual(ctx.get("design").get("_requirement_summary"),
                             ctx.get("requirements").get("summary"))

            # 5) 产物落盘: report.md / events.json / state.json
            self.assertTrue(os.path.exists(os.path.join(tmp, "report.md")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "events.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "state.json")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "state_failed.json")))

            # 6) 交付报告包含审查结论与修复记录
            with open(os.path.join(tmp, "report.md"), encoding="utf-8") as f:
                report = f.read()
            self.assertIn("审查结论", report)
            self.assertIn("修复循环记录", report)
            self.assertIn("五子棋", report)


class TestParallelExampleE2E(unittest.TestCase):
    """并行示例: 需求 -> (前端编码 ∥ 后端编码) -> 交付"""

    def test_parallel_branches_complete(self):
        wf = load_wf("examples", "parallel_example.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            ctx = WorkflowEngine(wf, MockLLM(), out_dir=tmp).run(topic="并行测试主题")
            self.assertIn("code_frontend", ctx.data)
            self.assertIn("code_backend", ctx.data)
            self.assertIn("final_report", ctx.data)
            self.assertEqual(ctx.visits.get("coding_parallel"), 1)


if __name__ == "__main__":
    unittest.main()
