from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SchedulerState(TypedDict):
    """主调度状态：全局消息 + 本轮从队列吸收的通知副本（便于观测与持久化）。"""

    messages: Annotated[list[AnyMessage], add_messages]
    pending_notifications: Annotated[list[str], operator.add]


class WorkerState(TypedDict):
    """Worker 私有状态：与主图隔离，仅消息轨迹。"""

    messages: Annotated[list[AnyMessage], add_messages]
