from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.task_notify.broker import notification_broker
from app.task_notify.notify_messages import notification_system_message
from app.services.llm import get_scheduler_llm
from app.state.schemas import SchedulerState
from app.tools.scheduler_tools import build_scheduler_tools


def build_scheduler_graph(tools: list | None = None):
    """tools 为 None 时使用仅本地工具；启动后由 runtime 注入合并 MCP 后的列表。"""
    merged = list(build_scheduler_tools()) if tools is None else list(tools)
    llm = get_scheduler_llm().bind_tools(merged)
    tool_node = ToolNode(merged)

    async def agent(state: SchedulerState, config: RunnableConfig):
        user_id = str(config["configurable"].get("user_id") or "")
        drained = await notification_broker.drain_all(user_id)
        notice_msgs = [notification_system_message(xml) for xml in drained]
        msgs = list(state["messages"]) + notice_msgs
        resp = await llm.ainvoke(msgs)
        return {"messages": notice_msgs + [resp], "pending_notifications": drained}

    def route_tools(s: SchedulerState):
        last = s["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    g = StateGraph(SchedulerState)
    g.add_node("agent", agent)
    g.add_node("tools", tool_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_tools, {"tools": "tools", END: END}) # 根据最后一条消息是否包含工具调用决定路由
    g.add_edge("tools", "agent")
    return g.compile()
