# -*- coding: utf-8 -*-
"""编码Agent：根据设计方案与需求产出多文件代码。

另提供 Frontend/Backend 两个子类，用于 examples/parallel_example.yaml
演示"并行执行"：两个编码节点并发工作、互不阻塞。
"""
from ..agent import BaseAgent
from ..utils import to_json_str

CODE_SCHEMA = """{{
  "language": "实现语言",
  "files": [
    {{"path": "相对路径", "content": "完整文件内容"}}
  ],
  "run_instructions": "如何运行",
  "notes": "实现说明(可选)"
}}"""


class CodingAgent(BaseAgent):
    name = "编码Agent"
    role = "coding"
    persona = ("你是一名高级开发工程师，代码简洁健壮：必要的输入校验、"
               "异常兜底、清晰命名。产出真实可运行的代码文件。")
    downstream = "审查Agent"

    def build_prompt(self, requirements, design) -> str:
        return """## 任务
请根据需求文档与设计方案编写代码（设计缺失时直接依据需求实现）。

## 输入: 需求文档
{requirements}

## 输入: 设计方案
{design}

## 输出JSON格式
{schema}""".format(requirements=to_json_str(requirements),
                   design=to_json_str(design),
                   schema=CODE_SCHEMA)

    def run(self, ctx, inputs: dict) -> dict:
        requirements = inputs.get("requirements")
        design = inputs.get("design")
        result = self.ask(self.build_prompt(requirements, design))

        files = result.get("files", [])
        self.handover(ctx, self.downstream,
                      "代码已完成: 语言={}, 文件数={}（{}）".format(
                          result.get("language", "?"), len(files),
                          ", ".join(f.get("path", "?") for f in files[:4])))
        return result


class FrontendCodingAgent(CodingAgent):
    """并行演示用：专注界面/交互层。"""
    name = "前端编码Agent"
    role = "coding"

    def build_prompt(self, requirements, design) -> str:
        base = super().build_prompt(requirements, design)
        return base + "\n\n## 约束\n你负责交互层/入口层模块（cli、渲染、命令解析）。"


class BackendCodingAgent(CodingAgent):
    """并行演示用：专注业务/存储层。"""
    name = "后端编码Agent"
    role = "coding"

    def build_prompt(self, requirements, design) -> str:
        base = super().build_prompt(requirements, design)
        return base + "\n\n## 约束\n你负责业务与存储层模块（service、store、数据模型）。"
