# -*- coding: utf-8 -*-
"""修复Agent：按审查意见修复代码，推动"审查<->修复"循环收敛。"""
from ..agent import BaseAgent
from ..utils import to_json_str


class FixAgent(BaseAgent):
    name = "修复Agent"
    role = "fix"
    persona = ("你是一名修复问题的开发工程师：逐条对照审查意见修改代码，"
               "只做必要修改并保持其余逻辑不变，同时说明每处修复。")
    downstream = "审查Agent"

    def run(self, ctx, inputs: dict) -> dict:
        code = inputs.get("code")
        review = inputs.get("review")
        if not isinstance(code, dict) or not isinstance(review, dict):
            raise ValueError("修复Agent需要上游产物 code 与 review")

        new_round = int(inputs.get("fix_round") or 0) + 1
        issues = review.get("issues", [])

        prompt = """## 任务
请按照审查意见修复代码，输出修复后的完整代码。

- 修复轮次: {round}
- 待修复问题清单:
{issues}

## 输入: 当前代码
{code}

## 输出JSON格式（与代码产物相同，另加 fix_notes）
{{
  "language": "实现语言",
  "files": [{{"path": "相对路径", "content": "修复后的完整文件内容"}}],
  "run_instructions": "如何运行",
  "fix_notes": "逐条说明如何修复了哪些问题"
}}""".format(round=new_round, issues=to_json_str(issues), code=to_json_str(code))

        result = self.ask(prompt)
        result.setdefault("fix_notes", "已按审查意见修复{}个问题".format(len(issues)))

        # 侧数据: 修复轮次与修复历史（供审查Agent判断/报告展示）
        ctx.set("fix_round", new_round)
        history = ctx.get("fix_history") or []
        history.append({"round": new_round, "issues_fixed": len(issues),
                        "notes": result.get("fix_notes", "")})
        ctx.set("fix_history", history)

        self.handover(ctx, self.downstream,
                      "第{}轮修复完成（处理{}个问题），请重新审查".format(new_round, len(issues)))
        return result
