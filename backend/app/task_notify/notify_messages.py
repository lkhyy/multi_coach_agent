from __future__ import annotations

from langchain_core.messages import SystemMessage


def notification_system_message(xml_payload: str) -> SystemMessage:
    """与主图 agent 节点内文案保持一致，便于模型识别来源。"""
    return SystemMessage(
        content=(
            "【全局命令队列】本轮吸收的 task-notification（XML），"
            "若 status=completed 请汇总进展；若 failed 请制定补救。\n"
            f"{xml_payload}"
        )
    )
