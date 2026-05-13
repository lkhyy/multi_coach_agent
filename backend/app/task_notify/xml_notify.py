from __future__ import annotations

import xml.etree.ElementTree as ET
from uuid import uuid4


def build_task_notification(
    *,
    summary: str,
    worker_thread_id: str,
    status: str = "completed",
    extra: dict[str, str] | None = None,
) -> str:
    """Build canonical task-notification XML for the global command queue."""
    nid = str(uuid4())
    root = ET.Element("task-notification")
    root.set("id", nid)
    root.set("status", status)
    ET.SubElement(root, "summary").text = summary
    ET.SubElement(root, "worker_thread_id").text = worker_thread_id
    if extra:
        meta = ET.SubElement(root, "meta")
        for k, v in extra.items():
            e = ET.SubElement(meta, "item")
            e.set("key", k)
            e.text = v
    return ET.tostring(root, encoding="unicode")


def parse_task_notification(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    if root.tag != "task-notification":
        raise ValueError("not a task-notification root")
    summary_el = root.find("summary")
    wid_el = root.find("worker_thread_id")
    return {
        "id": root.get("id", ""),
        "status": root.get("status", ""),
        "summary": (summary_el.text or "").strip() if summary_el is not None else "",
        "worker_thread_id": (wid_el.text or "").strip() if wid_el is not None else "",
    }
