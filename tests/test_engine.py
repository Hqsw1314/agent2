# -*- coding: utf-8 -*-
"""工作流引擎单元测试：顺序/分支/循环/并行/重试/断点恢复/循环保护。

全部使用注入的哑 Agent 实例（不依赖 LLM），完全离线、确定性。
运行: .venv/Scripts/python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniflow.agent import BaseAgent
from miniflow.context import WorkflowContext
from miniflow.engine import WorkflowEngine, eval_when
from miniflow.errors import WorkflowError


# ---------------- 测试用哑 Agent ----------------

class EchoAgent(BaseAgent):
    """原样透传输入并打上自己的标记。"""
    name = "回声Agent"
    role = "echo"

    def __init__(self, tag="A"):
        super().__init__(None)
        self.tag = tag
        self.run_count = 0

    def run(self, ctx, inputs):
        self.run_count += 1
        x = inputs.get("x", "")
        if isinstance(x, dict):  # $引用拿到上游完整产物时, 取其 value 字段
            x = x.get("value", "")
        return {"value": "{}<{}>".format(x, self.tag)}


class ReviewLikeAgent(BaseAgent):
    """前 fail_times 次不通过，之后通过（模拟审查-修复循环）。"""
    name = "审查模拟Agent"
    role = "review"

    def __init__(self, fail_times=1):
        super().__init__(None)
        self.fail_times = fail_times
        self.calls = 0

    def run(self, ctx, inputs):
        self.calls += 1
        passed = self.calls > self.fail_times
        return {"passed": passed, "score": 90 if passed else 50}


class FlakyAgent(BaseAgent):
    """前 fail_times 次抛异常（模拟故障，验证重试与断点恢复）。"""
    name = "易故障Agent"
    role = "flaky"

    def __init__(self, fail_times=0):
        super().__init__(None)
        self.fail_times = fail_times
        self.calls = 0

    def run(self, ctx, inputs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("模拟故障")
        return {"ok": True, "calls": self.calls}


def make_engine(steps, agents, out_dir, start=None, **kw):
    wf = {"name": "test-wf", "steps": steps}
    if start:
        wf["start"] = start
    wf.update(kw)
    return WorkflowEngine(wf, llm=None, agents=agents, out_dir=out_dir)


# ---------------- 测试 ----------------

class TestSequential(unittest.TestCase):
    def test_two_agents_data_flow(self):
        a1, a2 = EchoAgent("A"), EchoAgent("B")
        steps = [
            {"id": "a", "agent": "a1", "inputs": {"x": "S"}, "output": "sa", "next": "b"},
            {"id": "b", "agent": "a2", "inputs": {"x": "$sa"}, "output": "sb"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_engine(steps, {"a1": a1, "a2": a2}, tmp).run(topic="t")
        # 步骤b通过共享状态($sa)读到了步骤a的产物
        self.assertEqual(ctx.get("sb")["value"], "S<A><B>")
        self.assertEqual(a1.run_count, 1)
        self.assertEqual(a2.run_count, 1)


class TestBranchLoop(unittest.TestCase):
    def test_fail_then_pass_loop(self):
        steps = [
            {"id": "review", "agent": "rv", "inputs": {}, "output": "review",
             "next": "fix", "max_visits": 6,
             "branches": [{"when": "review.passed == true", "next": "deliver"}]},
            {"id": "fix", "agent": "fx", "inputs": {}, "output": "fix", "next": "review"},
            {"id": "deliver", "agent": "dp", "inputs": {}, "output": "final"},
        ]
        agents = {"rv": ReviewLikeAgent(fail_times=1), "fx": FlakyAgent(0), "dp": EchoAgent("D")}
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_engine(steps, agents, tmp).run(topic="t")
        self.assertTrue(ctx.get("review")["passed"] is True)
        self.assertEqual(ctx.visits["review"], 2)   # 首查未过 + 修复后复审
        self.assertEqual(ctx.visits["fix"], 1)
        self.assertIn("final", ctx.data)

    def test_loop_guard_raises(self):
        steps = [
            {"id": "review", "agent": "rv", "inputs": {}, "output": "review",
             "next": "fix", "max_visits": 3,
             "branches": [{"when": "review.passed == true", "next": "deliver"}]},
            {"id": "fix", "agent": "fx", "inputs": {}, "output": "fix", "next": "review"},
        ]
        agents = {"rv": ReviewLikeAgent(fail_times=999), "fx": FlakyAgent(0)}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkflowError):
                make_engine(steps, agents, tmp).run(topic="t")
            # 失败现场快照已落盘, 可用于断点恢复
            self.assertTrue(os.path.exists(os.path.join(tmp, "state_failed.json")))


class TestRetryAndRecovery(unittest.TestCase):
    def test_retry_succeeds(self):
        flaky = FlakyAgent(fail_times=1)
        steps = [{"id": "a", "agent": "f", "inputs": {}, "output": "out", "retries": 2}]
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_engine(steps, {"f": flaky}, tmp).run(topic="t")
        self.assertTrue(ctx.get("out")["ok"])
        self.assertEqual(flaky.calls, 2)  # 第1次失败, 第2次成功

    def test_resume_from_failed_snapshot(self):
        a, b = EchoAgent("A"), FlakyAgent(fail_times=1)
        steps = [
            {"id": "a", "agent": "a", "inputs": {"x": "S"}, "output": "sa",
             "next": "b", "retries": 0},
            {"id": "b", "agent": "b", "inputs": {"x": "$sa"}, "output": "sb", "retries": 0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkflowError):
                make_engine(steps, {"a": a, "b": b}, tmp).run(topic="t")
            self.assertEqual(a.run_count, 1)
            self.assertTrue(os.path.exists(os.path.join(tmp, "state_failed.json")))

            b.fail_times = 0  # "排除故障"后从断点恢复
            ctx = make_engine(steps, {"a": a, "b": b}, tmp).run(topic="t", resume=True)
            self.assertEqual(a.run_count, 1)              # 已完成步骤未被重复执行
            self.assertTrue(ctx.get("sb")["ok"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "state_failed.json")))


class TestParallel(unittest.TestCase):
    def test_parallel_join(self):
        steps = [
            {"id": "p", "type": "parallel",
             "parallel": [
                 {"id": "p1", "agent": "a1", "inputs": {"x": "1"}, "output": "o1"},
                 {"id": "p2", "agent": "a2", "inputs": {"x": "2"}, "output": "o2"},
             ], "next": "d"},
            {"id": "d", "agent": "d", "output": "final"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_engine(steps, {"a1": EchoAgent("F"), "a2": EchoAgent("B"),
                                      "d": EchoAgent("D")}, tmp).run(topic="t")
        self.assertEqual(ctx.get("o1")["value"], "1<F>")
        self.assertEqual(ctx.get("o2")["value"], "2<B>")
        self.assertIn("final", ctx.data)


class TestEvalWhen(unittest.TestCase):
    def setUp(self):
        self.ctx = WorkflowContext()
        self.ctx.set("review", {"passed": True, "score": 88})

    def test_cases(self):
        self.assertTrue(eval_when("review.passed == true", self.ctx))
        self.assertTrue(eval_when("review.score >= 80", self.ctx))
        self.assertFalse(eval_when("review.score < 80", self.ctx))
        self.assertTrue(eval_when("review.passed != false", self.ctx))
        with self.assertRaises(WorkflowError):
            eval_when("这不是合法表达式", self.ctx)


class TestMisconfiguration(unittest.TestCase):
    def test_missing_agent_raises(self):
        steps = [{"id": "a", "agent": "ghost", "inputs": {}, "output": "x"}]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkflowError):
                make_engine(steps, {}, tmp).run(topic="t")

    def test_duplicate_step_id_rejected(self):
        steps = [{"id": "a", "agent": "x", "inputs": {}, "output": "o"},
                 {"id": "a", "agent": "x", "inputs": {}, "output": "o2"}]
        with self.assertRaises(WorkflowError):
            make_engine(steps, {}, out_dir=".")


if __name__ == "__main__":
    unittest.main()
