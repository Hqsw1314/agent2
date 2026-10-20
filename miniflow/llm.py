# -*- coding: utf-8 -*-
"""LLM 接入层（复用项目1的接入方式，做了两点增强）。

项目1 (Code Review Agent) 通过 OpenAI 兼容接口调用阿里云 DashScope 的
qwen-plus，API Key 来自 DASHSCOPE_API_KEY。本项目保持同一接入方式，
并增加:

  1) MockLLM 离线演示模式：无 API Key 时流程照样端到端跑通，
     便于评审/演示（不依赖网络与额度），输出为确定性的模拟产物。
  2) provider=auto：检测到 Key 就用真实 LLM，否则自动降级 Mock。

任何 OpenAI 兼容服务均可（DashScope / 智谱 / DeepSeek / OpenAI 官方...），
只需改 config.yaml 的 base_url / model。
"""
import json
import os
import re
import time

from .errors import AgentError

try:
    from openai import OpenAI
    _HAS_OPENAI = True
except ImportError:  # 允许在未安装 openai 的环境下使用离线模式
    _HAS_OPENAI = False


class LLMError(Exception):
    """LLM 调用层错误。"""


class BaseLLM:
    name = "base"

    def chat(self, system: str, user: str) -> str:
        raise NotImplementedError


class OpenAICompatLLM(BaseLLM):
    """OpenAI 兼容 Chat Completions 客户端（与项目1相同的调用方式）。"""

    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float = 0.4, timeout: int = 60):
        if not _HAS_OPENAI:
            raise LLMError("未安装 openai 库，请执行: pip install openai")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat(self, system: str, user: str) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            raise LLMError("LLM API 调用失败: {}".format(e))


# ---------------------------------------------------------------------------
# MockLLM：离线演示模式
# ---------------------------------------------------------------------------

def _topic_of(user_prompt: str) -> str:
    m = re.search(r"任务主题[:：]\s*(.+)", user_prompt)
    return m.group(1).strip() if m else "未指定主题"


def _requirement_payload(user_prompt: str) -> dict:
    topic = _topic_of(user_prompt)
    return {
        "summary": "{}：一个面向日常使用的小型软件工具".format(topic),
        "functional_requirements": [
            "F1 用户可以创建新的待办事项（标题+优先级）",
            "F2 用户可以查看全部待办列表并按优先级排序",
            "F3 用户可以标记事项完成并删除事项",
            "F4 数据本地持久化，重启后不丢失",
        ],
        "non_functional_requirements": [
            "N1 命令行响应时间小于1秒",
            "N2 数据文件损坏时可给出友好提示而不是崩溃",
        ],
        "acceptance_criteria": [
            "A1 创建-查看-完成-删除全流程可用",
            "A2 重启程序后数据仍在",
        ],
        "topic": topic,
    }


def _design_payload(user_prompt: str) -> dict:
    return {
        "architecture": "经典三层：入口层(main)负责命令解析，服务层(TodoService)负责业务规则，存储层(JsonStore)负责持久化",
        "tech_stack": ["Python 3.8+", "标准库 json/pathlib（零第三方依赖）"],
        "modules": [
            {"name": "cli", "responsibility": "解析命令行参数，分发到服务层",
             "interface": "main(argv) -> int"},
            {"name": "todo_service", "responsibility": "待办的增/查/完成/删除与排序规则",
             "interface": "add(title, priority) / list_all() / done(id) / remove(id)"},
            {"name": "json_store", "responsibility": "JSON 文件读写与损坏兜底",
             "interface": "load() -> list / save(items) -> None"},
        ],
        "data_flow": "cli 解析命令 -> todo_service 校验并执行业务 -> json_store 读写 todo.json",
    }


def _coding_payload(user_prompt: str) -> dict:
    return {
        "language": "Python",
        "files": [
            {"path": "todo/service.py",
             "content": "# 离线演示产物\nDEFAULT_PRIORITY = 2\n\nclass TodoService:\n    def __init__(self, store):\n        self.store = store\n\n    def add(self, title, priority=DEFAULT_PRIORITY):\n        if not title:\n            raise ValueError('title 不能为空')  # 校验\n        items = self.store.load()\n        items.append({'id': len(items) + 1, 'title': title, 'priority': priority, 'done': False})\n        self.store.save(items)\n        return items[-1]['id']\n"},
            {"path": "todo/store.py",
             "content": "# 离线演示产物\nimport json\nfrom pathlib import Path\n\nclass JsonStore:\n    def __init__(self, path='todo.json'):\n        self.path = Path(path)\n\n    def load(self):\n        try:\n            return json.loads(self.path.read_text(encoding='utf-8'))\n        except FileNotFoundError:\n            return []\n        except json.JSONDecodeError:\n            return []  # 文件损坏时兜底为空列表\n\n    def save(self, items):\n        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')\n"},
        ],
        "run_instructions": "python -m todo.cli add \"买牛奶\" ; python -m todo.cli list",
    }


def _review_payload(user_prompt: str) -> dict:
    """首次审查不通过（演示"分支+循环"），修复后复审通过。"""
    m = re.search(r"修复轮次[:：]\s*(\d+)", user_prompt)
    rnd = int(m.group(1)) if m else 0
    if rnd >= 1:
        return {
            "passed": True, "score": 88, "issues": [],
            "comment": "第{}轮修复已确认：输入校验与异常兜底均已补齐，复审通过。".format(rnd),
        }
    return {
        "passed": False, "score": 62,
        "issues": [
            {"severity": "major", "description": "todo_service.add 未校验 priority 取值范围",
             "suggestion": "对 priority 增加 0-3 的范围校验"},
            {"severity": "minor", "description": "store.load 对损坏 JSON 静默返回空列表，可能覆盖旧数据",
             "suggestion": "损坏时先备份原文件再返回空列表"},
        ],
        "comment": "存在主要问题，需要修复后提交复审。",
    }


def _fix_payload(user_prompt: str) -> dict:
    payload = _coding_payload(user_prompt)
    payload["files"][0]["content"] = (
        "# 离线演示产物（第2版：已按审查意见修复）\n"
        "DEFAULT_PRIORITY = 2\nVALID_PRIORITIES = (0, 1, 2, 3)\n\n"
        "class TodoService:\n    def __init__(self, store):\n        self.store = store\n\n"
        "    def add(self, title, priority=DEFAULT_PRIORITY):\n"
        "        if not title:\n            raise ValueError('title 不能为空')\n"
        "        if priority not in VALID_PRIORITIES:  # 修复：范围校验\n"
        "            raise ValueError('priority 取值必须是 0-3')\n"
        "        items = self.store.load()\n"
        "        items.append({'id': len(items) + 1, 'title': title, 'priority': priority, 'done': False})\n"
        "        self.store.save(items)\n        return items[-1]['id']\n"
    )
    payload["files"][1]["content"] = payload["files"][1]["content"].replace(
        "            return []  # 文件损坏时兜底为空列表",
        "            backup = self.path.with_suffix('.broken')  # 修复：损坏文件先备份\n"
        "            self.path.replace(backup)\n            return []",
    )
    payload["fix_notes"] = "已按审查意见修复：priority 范围校验；损坏 JSON 先备份再兜底。"
    return payload


MOCK_HANDLERS = {
    "requirement": _requirement_payload,
    "design": _design_payload,
    "coding": _coding_payload,
    "review": _review_payload,
    "fix": _fix_payload,
    "deliver": lambda p: {
        "summary": "Mini SE 流程完成：需求/设计/代码/审查/修复产物已生成（离线演示数据）",
        "report_markdown": None,  # 由 DeliverAgent 用真实上下文数据渲染
    },
}


class MockLLM(BaseLLM):
    """离线演示模型：按 [ROLE:xxx] 标记返回确定性的模拟 JSON 产物。

    用途：无 API Key / 无网络时保证工作流可端到端演示，
    并确定性地演示"审查不通过 -> 修复 -> 复审通过"的循环分支。
    """

    name = "mock"

    def chat(self, system: str, user: str) -> str:
        m = re.search(r"\[ROLE:(\w+)\]", system or "")
        role = m.group(1) if m else "deliver"
        handler = MOCK_HANDLERS.get(role, MOCK_HANDLERS["deliver"])
        payload = handler(user)
        return "```json\n{}\n```".format(json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------------

def create_llm(cfg: dict) -> BaseLLM:
    """根据配置创建 LLM 后端。

    cfg 来自 config.yaml 的 llm 段:
      provider: auto(默认) / openai_compat / mock
      api_key_env: 从哪个环境变量读 Key（支持 .env, 与项目1一致）
    """
    cfg = cfg or {}
    provider = str(cfg.get("provider", "auto")).lower()

    if provider == "mock":
        return MockLLM()

    api_key = ""
    env_name = cfg.get("api_key_env", "DASHSCOPE_API_KEY")
    if env_name:
        api_key = os.environ.get(str(env_name), "")
    api_key = api_key or cfg.get("api_key", "")

    if provider == "auto" and not api_key:
        return MockLLM()
    if not api_key:
        raise LLMError(
            "未配置 API Key：请在 .env 中设置 {}，或将 provider 设为 mock 使用离线模式".format(env_name))

    return OpenAICompatLLM(
        base_url=cfg.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        api_key=api_key,
        model=cfg.get("model", "qwen-plus"),
        temperature=float(cfg.get("temperature", 0.4)),
        timeout=int(cfg.get("timeout", 60)),
    )
