# -*- coding: utf-8 -*-
"""WorkflowEngine —— 极简工作流引擎。

编排能力（对应作业"推荐实现"项）:
  - 顺序执行: 步骤按 next 指针串联
  - 条件分支: step.branches = [{when: "review.passed == true", next: "deliver"}]
  - 循环:     分支指回前面的步骤即成环（review <-> fix），由 max_visits / max_steps 双重保护
  - 并行:     step.type = parallel，多个子步骤并发执行后汇合
  - 执行日志: 全部事件实时打印并落盘 events.json
  - 状态追踪: 每步完成后写快照 state.json（含下一步指针）
  - 错误恢复: 步骤失败自动重试；最终失败写 state_failed.json 现场快照，
              修复后可用 --resume 从断点继续，不重复已完成步骤

工作流由 YAML 声明（见 workflow.yaml）：新增/调整流程只需改 YAML，不必改引擎代码。
"""
import importlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .context import WorkflowContext
from .errors import WorkflowError
from .utils import to_json_str

_FAILED_FILE = "state_failed.json"
_STATE_FILE = "state.json"


# ---------------------------------------------------------------------------
# 条件表达式求值（支持 "路径 操作符 值"，操作符: == != >= <= > <）
# ---------------------------------------------------------------------------

def _literal_or_path(token: str, ctx: WorkflowContext):
    token = token.strip()
    if token in ("true", "True"):
        return True
    if token in ("false", "False"):
        return False
    if token in ("null", "None"):
        return None
    if re.match(r"^-?\d+(\.\d+)?$", token):
        return float(token) if "." in token else int(token)
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return ctx.resolve(token)  # 当作上下文路径


def eval_when(expr: str, ctx: WorkflowContext) -> bool:
    """求值分支条件，例如 'review.passed == true'、'review.score >= 80'。"""
    m = re.match(r"^\s*([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$", str(expr))
    if not m:
        raise WorkflowError("无法解析分支条件表达式: {}".format(expr))
    left = _literal_or_path(m.group(1), ctx)
    right = _literal_or_path(m.group(3), ctx)
    op = m.group(2)
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == "<":
            return left < right
    except TypeError:
        return False
    return False


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class WorkflowEngine:
    def __init__(self, wf: dict, llm=None, agents: dict = None, out_dir: str = "outputs"):
        if not wf.get("steps"):
            raise WorkflowError("工作流定义中没有步骤(steps)")
        self.wf = wf
        self.name = wf.get("name", "workflow")
        self.max_steps = int(wf.get("max_steps", 50))
        self.default_retries = int(wf.get("retries", 2))
        self.steps = {s["id"]: s for s in wf["steps"]}
        if len(self.steps) != len(wf["steps"]):
            raise WorkflowError("存在重复的步骤 id")
        self.start = wf.get("start") or wf["steps"][0]["id"]
        self.out_dir = out_dir
        self.agents = agents if agents is not None else self._load_agents(wf.get("agents") or {})

    # ---------- Agent 注册表 ----------
    @staticmethod
    def _load_agents(mapping: dict) -> dict:
        """agents 映射: id -> '包.模块.类名' 字符串（也可直接传实例，便于测试）。"""
        agents = {}
        for key, target in mapping.items():
            if isinstance(target, str):
                mod_name, cls_name = target.rsplit(".", 1)
                cls = getattr(importlib.import_module(mod_name), cls_name)
                agents[key] = cls
            else:
                agents[key] = target
        return agents

    # ---------- 主循环 ----------
    def run(self, topic: str = "", resume: bool = False) -> WorkflowContext:
        ctx = WorkflowContext(topic=topic)
        current = self.start

        if resume:
            state = self._read_snapshot()
            if state:
                ctx = WorkflowContext.from_snapshot(state)
                current = state.get("current")
                if current is None:
                    ctx.log("snapshot", "-", "快照显示工作流已完成，无需恢复")
                    return ctx
            else:
                ctx.log("snapshot", "-", "没有找到快照，将从头执行")

        ctx.set("topic", topic or ctx.topic)
        ctx.listeners.append(self._printer)
        ctx.log("snapshot", "-", "工作流[{}]启动, 模式: {}".format(
            self.name, "断点恢复" if resume and state else "全新执行"))

        executed = 0
        while current is not None:
            executed += 1
            if executed > self.max_steps:
                self._fail(ctx, current,
                           "超过最大步数限制({}), 判定为无法收敛的循环".format(self.max_steps))
                raise WorkflowError(
                    "超过最大步数限制({}), 判定为无法收敛的循环".format(self.max_steps))
            step = self.steps.get(current)
            if step is None:
                self._fail(ctx, current, "未找到步骤定义: {}".format(current))
                raise WorkflowError("未找到步骤定义: {}".format(current))
            current = self._run_step(step, ctx)

        # ---- 正常结束: 落盘最终产物 ----
        ctx.log("finish", "-", "工作流完成, 产物: {}".format(", ".join(
            k for k in ctx.data if k != "topic")))
        self._write_outputs(ctx)
        return ctx

    # ---------- 单步执行 ----------
    def _run_step(self, step: dict, ctx: WorkflowContext):
        sid = step["id"]

        max_visits = int(step.get("max_visits", 8))
        if ctx.bump_visit(sid) > max_visits:
            self._fail(ctx, sid, "步骤[{}]执行次数超过上限({}), 循环条件可能永远无法满足".format(sid, max_visits))
            raise WorkflowError("步骤[{}]执行次数超过上限({}), 循环条件可能永远无法满足".format(sid, max_visits))

        # 并行步骤
        if step.get("type") == "parallel":
            self._run_parallel(step, ctx)
        else:
            agent_key = step.get("agent")
            if agent_key is None:  # 纯路由节点
                ctx.log("step_end", sid, "(路由节点, 跳过执行)")
            else:
                agent = self._agent_of(sid, agent_key)
                inputs = {k: ctx.resolve(str(v))
                          for k, v in (step.get("inputs") or {}).items()}
                ctx.log("step_start", sid, "{} 开始执行, 输入: {}".format(
                    agent.name, ", ".join(inputs.keys()) or "(无)"))
                t0 = time.time()
                result = self._execute_with_retry(step, agent, ctx, inputs)
                duration = int((time.time() - t0) * 1000)
                out_key = step.get("output")
                if out_key:
                    ctx.set(out_key, result if result is not None else {})
                ctx.log("step_end", sid, "{} 完成 -> 输出键: {}".format(
                    agent.name, out_key or "(无)"), duration_ms=duration)

        next_id = self._next_of(step, ctx)
        self._snapshot(ctx, next_id)  # 每步后写快照, 记录"下一步"指针用于断点恢复
        ctx.log("snapshot", sid, "状态快照已保存, 下一步: {}".format(next_id or "(结束)"))
        return next_id

    def _agent_of(self, sid: str, agent_key: str):
        agent = self.agents.get(agent_key)
        if agent is None:
            self._fail_contextless(sid, "步骤[{}]的agent '{}'未注册".format(sid, agent_key))
            raise WorkflowError("步骤[{}]的agent '{}'未注册".format(sid, agent_key))
        return agent

    def _execute_with_retry(self, step: dict, agent, ctx: WorkflowContext, inputs: dict):
        retries = int(step.get("retries", self.default_retries))
        last_err = None
        for attempt in range(retries + 1):
            try:
                return agent.run(ctx, inputs)
            except Exception as e:
                last_err = e
                ctx.log("error", step["id"],
                        "{} 第{}/{}次执行失败: {}".format(agent.name, attempt + 1, retries + 1, e))
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 5))  # 指数退避(1s, 2s, 4s...)
        self._fail(ctx, step["id"],
                   "步骤[{}]在重试{}次后仍失败: {}".format(step["id"], retries, last_err))
        raise WorkflowError("步骤[{}]失败: {}".format(step["id"], last_err))

    def _run_parallel(self, step: dict, ctx: WorkflowContext):
        subs = step.get("parallel") or []
        if not subs:
            raise WorkflowError("并行步骤[{}]没有定义 parallel 子步骤".format(step["id"]))
        ctx.log("step_start", step["id"], "并行执行 {} 个子步骤: {}".format(
            len(subs), ", ".join(s["id"] for s in subs)))

        def worker(sub):
            agent = self._agent_of(sub["id"], sub.get("agent"))
            inputs = {k: ctx.resolve(str(v)) for k, v in (sub.get("inputs") or {}).items()}
            ctx.log("step_start", sub["id"], "{} 开始执行(并行)".format(agent.name))
            t0 = time.time()
            result = self._execute_with_retry(sub, agent, ctx, inputs)
            if sub.get("output"):
                ctx.set(sub["output"], result if result is not None else {})
            ctx.log("step_end", sub["id"], "{} 完成 -> 输出键: {}".format(
                agent.name, sub.get("output")), duration_ms=int((time.time() - t0) * 1000))

        max_workers = int(step.get("max_workers", len(subs)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(worker, subs))
        ctx.log("step_end", step["id"], "并行汇合完成")

    # ---------- 分支路由 ----------
    def _next_of(self, step: dict, ctx: WorkflowContext):
        for br in (step.get("branches") or []):
            when = br.get("when")
            if when is None or eval_when(when, ctx):
                return br.get("next")
        return step.get("next")

    # ---------- 失败处理与快照 ----------
    def _fail(self, ctx: WorkflowContext, step_id, detail: str):
        ctx.log("error", str(step_id), detail)
        self._snapshot(ctx, step_id, name=_FAILED_FILE)
        ctx.log("error", str(step_id), "失败现场快照已保存到 {}, 可用 --resume 从断点恢复".format(_FAILED_FILE))

    def _fail_contextless(self, step_id, detail: str):
        # agent 未注册等初始化期错误：没有可用的 ctx，直接抛出
        raise WorkflowError(detail)

    def _snapshot(self, ctx: WorkflowContext, next_id, name: str = _STATE_FILE):
        os.makedirs(self.out_dir, exist_ok=True)
        state = ctx.snapshot()
        state.update({"workflow": self.name, "current": next_id, "ts": time.time()})
        with open(os.path.join(self.out_dir, name), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)

    def _read_snapshot(self):
        for name in (_FAILED_FILE, _STATE_FILE):
            path = os.path.join(self.out_dir, name)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    # ---------- 最终产物落盘 ----------
    def _write_outputs(self, ctx: WorkflowContext):
        os.makedirs(self.out_dir, exist_ok=True)
        self._snapshot(ctx, None)  # current=None 表示已完成
        failed_path = os.path.join(self.out_dir, _FAILED_FILE)
        if os.path.exists(failed_path):
            os.remove(failed_path)  # 成功后清除旧的失败快照

        with open(os.path.join(self.out_dir, "events.json"), "w", encoding="utf-8") as f:
            json.dump({"workflow": self.name, "topic": ctx.topic,
                       "events": [e.to_dict() for e in ctx.events],
                       "messages": [m.to_dict() for m in ctx.messages],
                       "data_keys": [k for k in ctx.data if k != "topic"]},
                      f, ensure_ascii=False, indent=2, default=str)

        with open(os.path.join(self.out_dir, "report.md"), "w", encoding="utf-8") as f:
            f.write(self._compose_report(ctx))

    def _compose_report(self, ctx: WorkflowContext) -> str:
        lines = ["# 工作流执行报告", "",
                 "- 工作流: {}".format(self.name),
                 "- 主题: {}".format(ctx.topic or "-"),
                 "- 完成时间: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")), "",
                 "## 执行统计", "", "| 步骤 | 执行次数 | 累计耗时(ms) |", "|---|---|---|"]
        durations = {}
        for ev in ctx.events:
            if ev.type == "step_end" and ev.duration_ms:
                durations[ev.step] = durations.get(ev.step, 0) + ev.duration_ms
        for sid in sorted(ctx.visits):
            lines.append("| {} | {} | {} |".format(sid, ctx.visits[sid], durations.get(sid, 0)))
        lines += ["", "## Agent 通信记录", ""]
        for m in ctx.messages:
            lines.append("- {} -> {} [{}]: {}".format(m.sender, m.receiver, m.type, m.content))
        lines.append("")

        final = ctx.get("final_report")
        md = final.get("report_markdown") if isinstance(final, dict) else None
        if md:
            lines += ["## 交付产物明细", "", md]
        else:
            for key, value in ctx.data.items():
                if key == "topic":
                    continue
                lines += ["## 产物: {}".format(key), "", "```json",
                          to_json_str(value, 8000), "```", ""]
        return "\n".join(lines)

    # ---------- 控制台实时日志 ----------
    @staticmethod
    def _printer(ev):
        mark = {"step_start": ">>", "step_end": "OK", "message": "MSG",
                "error": "!!", "snapshot": "..", "finish": "== "}.get(ev.type, "--")
        t = time.strftime("%H:%M:%S", time.localtime(ev.ts))
        line = "[{}] {} [{}] {}".format(t, mark, ev.step, ev.detail)
        if ev.duration_ms is not None:
            line += " ({}ms)".format(ev.duration_ms)
        print(line, flush=True)
