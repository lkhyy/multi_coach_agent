import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Role = "user" | "assistant" | "system" | "tool";

type ToolCallLite = { id?: string; name?: string; args?: Record<string, unknown> };

type ChatMessage = {
  role: Role;
  content: string;
  name?: string;
  tool_call_id?: string;
  tool_calls?: ToolCallLite[];
};

type ServerEvent =
  | { type: "token"; delta: string }
  | { type: "tool_start"; name?: string; input?: unknown }
  | { type: "tool_end"; name?: string; output?: string }
  | { type: "run_start"; round?: number; auto?: boolean }
  | { type: "run_end" }
  | { type: "warning"; message?: string }
  | { type: "error"; message?: string }
  | { type: "pong" };

type SessionResponse = {
  messages: ChatMessage[];
  exists?: boolean;
  storage?: string;
};

function useSchedulerWs(userId: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);

  const url = useMemo(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const host = import.meta.env.DEV ? "127.0.0.1:8000" : window.location.host;
    return `${proto}://${host}/ws/${encodeURIComponent(userId)}`;
  }, [userId]);

  const connect = useCallback(() => {
    wsRef.current?.close();
    const ws = new WebSocket(url);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    wsRef.current = ws;
    return ws;
  }, [url]);

  return { wsRef, connected, connect };
}

export default function App() {
  const [userId, setUserId] = useState("demo-user");
  const [threadId, setThreadId] = useState("default");
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [streamLog, setStreamLog] = useState("");
  const { wsRef, connected, connect } = useSchedulerWs(userId);

  const loadHistory = useCallback(async () => {
    setHistoryError(null);
    const url = `/sessions/${encodeURIComponent(userId)}/${encodeURIComponent(threadId)}`;
    try {
      const r = await fetch(url);
      if (!r.ok) {
        setHistoryError(`HTTP ${r.status}`);
        return;
      }
      const data = (await r.json()) as SessionResponse;
      setHistory(Array.isArray(data.messages) ? data.messages : []);
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : String(e));
    }
  }, [userId, threadId]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const appendLog = (line: string) => {
    setStreamLog((s) => s + line);
  };

  const sendChat = async () => {
    const text = input.trim();
    if (!text) return;
    const userMsg: ChatMessage = { role: "user", content: text };
    setHistory((h) => [...h, userMsg]);
    setInput("");
    setStreamLog("");

    let ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      ws = connect();
    }
    if (ws.readyState !== WebSocket.OPEN) {
      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error("ws connect failed"));
      });
    }

    const payload = {
      type: "chat",
      thread_id: threadId,
      messages: [{ role: "user" as const, content: text }],
    };

    ws.onmessage = (ev) => {
      const data = JSON.parse(String(ev.data)) as ServerEvent;
      if (data.type === "run_start") {
        if (data.round) {
          appendLog(
            `\n--- run_start round=${data.round}${data.auto ? " (auto_followup)" : ""} ---\n`,
          );
        }
        return;
      }
      if (data.type === "token") {
        appendLog(data.delta);
        return;
      }
      if (data.type === "tool_start") {
        appendLog(`\n[tool_start] ${data.name ?? ""} ${JSON.stringify(data.input ?? {})}\n`);
        return;
      }
      if (data.type === "tool_end") {
        appendLog(`\n[tool_end] ${data.name ?? ""} -> ${data.output ?? ""}\n`);
        return;
      }
      if (data.type === "run_end") {
        void loadHistory();
        return;
      }
      if (data.type === "warning") {
        appendLog(`\n[warning] ${data.message ?? ""}\n`);
        return;
      }
      if (data.type === "error") {
        appendLog(`\n[error] ${data.message ?? ""}\n`);
      }
    };

    ws.send(JSON.stringify(payload));
  };

  return (
    <div className="layout">
      <h2>学习助手（WebSocket 流式）</h2>
      <div className="row">
        <label>
          user_id：
          <input value={userId} onChange={(e) => setUserId(e.target.value)} />
        </label>
        <label>
          thread_id（会话 md 文件名）：
          <input value={threadId} onChange={(e) => setThreadId(e.target.value)} />
        </label>
        <button type="button" onClick={connect}>
          {connected ? "已连接" : "连接 WS"}
        </button>
        <button type="button" onClick={() => void loadHistory()}>
          刷新历史
        </button>
      </div>
      <p style={{ fontSize: 14, opacity: 0.85 }}>
        每条消息只发送<strong>本轮新用户句</strong>；打开页面或切换 thread 时通过{" "}
        <code>GET /sessions/…</code> 拉取 md 中的完整历史（含 tool_calls）。
      </p>
      {historyError ? <p style={{ color: "crimson" }}>历史加载失败：{historyError}</p> : null}
      <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="输入你的问题..." />
      <div className="row">
        <button type="button" onClick={sendChat} disabled={!input.trim()}>
          发送
        </button>
      </div>
      <div>
        <div style={{ fontWeight: 600 }}>会话历史（来自 md）</div>
        <div className="log">
          {history.map((m, i) => (
            <div key={i} style={{ marginBottom: 10 }}>
              <div>
                <strong>{m.role}</strong>
                {m.name ? ` (${m.name})` : ""}
                {m.tool_call_id ? ` [${m.tool_call_id}]` : ""}：
              </div>
              <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
              {m.tool_calls && m.tool_calls.length > 0 ? (
                <pre style={{ fontSize: 12, overflow: "auto" }}>
                  {JSON.stringify(m.tool_calls, null, 2)}
                </pre>
              ) : null}
            </div>
          ))}
        </div>
      </div>
      <div>
        <div style={{ fontWeight: 600 }}>流式事件 / token</div>
        <div className="log">{streamLog}</div>
      </div>
    </div>
  );
}
