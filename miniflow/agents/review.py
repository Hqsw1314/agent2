# -*- coding: utf-8 -*-
"""审查Agent：项目1 (Code Review Agent) 的"工作流节点化"。

项目1中它是一个独立交互式 Agent（读文件 -> LLM 审查 -> 终端输出）；
本项目中它成为流程里的一个节点：接收需求+代码，输出结构化审查结论
（passed/score/issues），由引擎根据结论走"交付"或"修复"分支。
"""
from ..agent import BaseAgent
from ..utils import to_json_str


class ReviewAgent(BaseAgent):
    name = "审查Agent"
    role = "review"
    persona = ("你是一名严格的代码评审员（继承自项目1的Code Review Agent职责）:"
               "检查代码是否满足需求、找出Bug与边界问题、评估可维护性。"
               "评分客观，通过标准是 score>=80 且无 major 问题。")
    downstream = "修复Agent"

    def run(self, ctx, inputs: dict) -> dict:
        requirements = inputs.get("requirements")
        code = inputs.get("code")
        fix_round = inputs.get("fix_round") or 0

        if not isinstance(code, dict):
            raise ValueError("审查Agent需要上游产物 code（dict），实际: {}".format(
                type(code).__name__))

        # 收件箱：查看是否有修复Agent发来的消息（体现"消息驱动协作"）
        inbox = ctx.messages_for(self.name)
        inbox_note = ""
        if inbox:
            latest = inbox[-1]
            inbox_note = "\n（收件箱: {} 来自 {}）".format(latest.content, latest.sender)

        prompt = """## 任务
请对照需求文档审查下面的代码，输出审查结论。

- 修复轮次: {round}{inbox}

## 输入: 需求文档
{requirements}

## 输入: 待审查代码
{code}

## 输出JSON格式
{{
  "passed": true或false（score>=80且无major问题才算通过）,
  "score": 0-100整数,
  "issues": [{{"severity": "major或minor", "description": "问题描述", "suggestion": "修复建议"}}],
  "comment": "总体评审意见"
}}""".format(round=fix_round, inbox=inbox_note,
             requirements=to_json_str(requirements), code=to_json_str(code))

        result = self.ask(prompt)

        if result.get("passed"):
            self.handover(ctx, "交付Agent",
                          "审查通过（得分{}），可以交付".format(result.get("score")))
        else:
            self.handover(ctx, self.downstream,
                          "审查不通过（得分{}），{}个问题待修复，请修复后重新提交审查".format(
                              result.get("score"), len(result.get("issues", []))),
                          mtype="control")
        return result
