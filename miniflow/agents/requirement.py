# -*- coding: utf-8 -*-
"""需求Agent：把一句主题加工成结构化、可验证的需求文档。"""
from ..agent import BaseAgent


class RequirementAgent(BaseAgent):
    name = "需求Agent"
    role = "requirement"
    persona = ("你是一名资深需求分析师，善于把模糊的想法整理成清晰、"
               "可验证、可测试的软件需求，输出始终为简体中文。")
    downstream = "设计Agent"

    def run(self, ctx, inputs: dict) -> dict:
        topic = str(inputs.get("topic") or "未指定主题")

        prompt = """## 任务
请为下面的软件主题编写一份简明需求文档。

- 任务主题: {topic}

## 输出JSON格式
{{
  "summary": "一句话概述系统做什么",
  "functional_requirements": ["F1 ...", "F2 ...", "F3 ..."],
  "non_functional_requirements": ["N1 ..."],
  "acceptance_criteria": ["A1 ..."]
}}""".format(topic=topic)

        result = self.ask(prompt)
        result.setdefault("topic", topic)

        self.handover(ctx, self.downstream,
                      "需求文档已就绪: {}（功能需求{}条, 验收标准{}条）".format(
                          str(result.get("summary", ""))[:40],
                          len(result.get("functional_requirements", [])),
                          len(result.get("acceptance_criteria", []))))
        return result
