# -*- coding: utf-8 -*-
"""WorkflowContext —— 工作流的"状态中心 + 消息中心 + 日志中心"。

Agent 之间不直接互相调用，全部协作都通过 Context 完成：

  1) 状态共享（State Sharing）
     每个步骤把产物写入 ctx.data[key]，下游步骤通过 inputs 路径声明读取。
     例如编码 Agent 读 ctx.data["design"]，审查 Agent 读 ctx.data["code"]。

  2) 消息传递（Message Passing）
     Agent 通过 ctx.send() 显式发送 Message（谁 -> 谁，类型，摘要），
     接收方可用 ctx.messages_for(name) 查看自己的收件箱。
     消息记录完整保留，构成可审查的"通信链路"。

  3) 事件追踪（Event Tracing）
     步骤开始/结束/耗时/错误等全部记录为 Event，支持实时监听（控制台输出）
     和落盘（events.json），用于执行日志与状态追踪。

  4) 快照（Snapshot）
     ctx.data + visits 可随时序列化，用于断点恢复。
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Message:
    """Agent 间消息：由发送方写入，接收方按需消费。"""
    sender: str
    receiver: str
    type: str            # artifact=产物交接 / control=控制指令 / error=错误通报
    content: str         # 摘要文本（完整产物内容在 ctx.data 中）
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"sender": self.sender, "receiver": self.receiver,
                "type": self.type, "content": self.content, "ts": self.ts}


@dataclass
class Event:
    """执行事件：工作流日志的最小单元。"""
    ts: float
    type: str                    # step_start/step_end/message/error/snapshot/finish
    step: str
    detail: str
    duration_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"ts": self.ts, "type": self.type, "step": self.step,
                "detail": self.detail, "duration_ms": self.duration_ms}


class WorkflowContext:
    """跨 Agent 共享的运行时上下文（线程安全）。"""

    def __init__(self, topic: str = ""):
        self.topic = topic
        self.data: Dict[str, Any] = {}      # 共享状态: output key -> 产物
        self.messages: List[Message] = []   # 全部消息（通信链路记录）
        self.events: List[Event] = []       # 全部事件（执行日志）
        self.visits: Dict[str, int] = {}    # 步骤执行计数（循环保护用）
        self.listeners: List[Callable[[Event], None]] = []
        self._lock = threading.RLock()

    # ---------- 状态共享 ----------
    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.data.get(key, default)

    def resolve(self, path: str) -> Any:
        """按点号路径取共享状态：'review.passed' -> data['review']['passed']。

        路径不存在时返回 None（不抛错，由分支条件/Agent 自行兜底）。
        """
        with self._lock:
            cur: Any = self.data
        for part in str(path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur

    def bump_visit(self, step_id: str) -> int:
        """步骤访问计数 +1（并行步骤下线程安全），返回最新计数。"""
        with self._lock:
            self.visits[step_id] = self.visits.get(step_id, 0) + 1
            return self.visits[step_id]

    # ---------- 消息传递 ----------
    def send(self, sender: str, receiver: str, mtype: str, content: str) -> Message:
        """发送一条消息并记录为事件。"""
        msg = Message(sender=sender, receiver=receiver, type=mtype, content=content)
        with self._lock:
            self.messages.append(msg)
        self.log("message", sender,
                 "{} -> {} [{}]: {}".format(sender, receiver, mtype, content))
        return msg

    def messages_for(self, agent_name: str) -> List[Message]:
        """读取某 Agent 的"收件箱"。"""
        with self._lock:
            return [m for m in self.messages if m.receiver == agent_name]

    # ---------- 事件日志 ----------
    def log(self, etype: str, step: str, detail: str,
            duration_ms: Optional[int] = None) -> None:
        ev = Event(ts=time.time(), type=etype, step=step, detail=detail,
                   duration_ms=duration_ms)
        with self._lock:
            self.events.append(ev)
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:
                pass  # 监听器异常不影响主流程

    # ---------- 快照（断点恢复） ----------
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {"topic": self.topic, "data": self.data,
                    "visits": dict(self.visits)}

    @classmethod
    def from_snapshot(cls, state: Dict[str, Any]) -> "WorkflowContext":
        ctx = cls(topic=state.get("topic", ""))
        ctx.data = state.get("data", {}) or {}
        ctx.visits = dict(state.get("visits", {}) or {})
        return ctx
