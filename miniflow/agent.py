# -*- coding: utf-8 -*-
"""BaseAgent —— 所有工作流节点 Agent 的基类。

约定（见 DESIGN.md 3.2 节）:
  - 子类实现 run(ctx, inputs) -> dict，返回值由引擎写入步骤的 output 键；
    需要写多个侧数据时可在 run 内用 ctx.set()（如 fix_round / fix_history）。
  - Agent 与 Agent 之间不直接调用：产物写入共享状态，交接用 ctx.send() 发消息。
  - ask() 封装"拼提示词 -> 调 LLM -> 解析 JSON"，MockLLM 通过 system 里的
    [ROLE:xxx] 标记识别当前角色并返回模拟产物。
"""
from .errors import AgentError
from .utils import extract_json, to_json_str


class BaseAgent:
    name = "BaseAgent"        # 展示名（中文，出现在日志/消息里）
    role = "base"             # 角色标识（MockLLM 与提示词标记用）
    persona = "你是一个通用软件工程 Agent。"   # system prompt 人设
    downstream = ""           # 默认下游接收人（用于发交接消息）

    def __init__(self, llm):
        self.llm = llm

    # ---- 子类必须实现 ----
    def run(self, ctx, inputs: dict) -> dict:
        """执行本节点：从 inputs 取上游产物，返回本节点产物(dict)。"""
        raise NotImplementedError

    # ---- 通用 LLM 交互 ----
    def ask(self, task_prompt: str) -> dict:
        """调用 LLM 并强制解析为 JSON 对象。"""
        system = "[ROLE:{}] {}".format(self.role, self.persona)
        user = task_prompt.rstrip() + "\n\n## 输出要求\n严格只输出一个JSON对象，不要输出任何解释文字。"
        raw = self.llm.chat(system, user)
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise AgentError(
                "{}: LLM 输出无法解析为 JSON 对象，原始输出前120字: {}".format(
                    self.name, to_json_str(raw, 120)))
        return data

    # ---- 通用消息 ----
    def handover(self, ctx, receiver: str, content: str, mtype: str = "artifact"):
        """向下游 Agent 发送交接消息。"""
        ctx.send(self.name, receiver, mtype, content)

    # ---- 通用输入兜底 ----
    @staticmethod
    def dump(inputs_value, limit=3000) -> str:
        """把上游产物渲染进提示词。"""
        return to_json_str(inputs_value, limit)
