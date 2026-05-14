import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";

import { MarkdownBody } from "./MarkdownBody";

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

type LongTermMemoryDto = {
  personality: string[];
  style_preferences: string[];
  learning_goals: string[];
  stable_facts: string[];
  updated_at: string;
};

type LearningIndexEntryDto = {
  content_id: string;
  title: string;
  summary: string;
  keywords: string[];
  updated_at: string;
  has_learning_plan?: boolean;
  mastery_status?: string;
  plan_confirmed_at?: string;
  aggregate_plan_status_cn?: string;
  plan_phases?: PlanPhaseDto[];
};

type PlanPhaseDto = { title: string; status: string; progress_pct?: string | number };

type LearningContentDetailDto = {
  content_id: string;
  title: string;
  summary: string;
  keywords: string[];
  key_points: string[];
  updated_at: string;
  learning_plan?: string;
  mastery_status?: string;
  progress_summary?: string;
  plan_confirmed_at?: string;
  plan_phases?: PlanPhaseDto[];
};

type ChatSnapshot = {
  history: ChatMessage[];
  input: string;
  draftAssistant: string;
  debugLog: string;
};

const TOKEN_KEY = "multi_coach_access_token";
const USER_KEY = "multi_coach_user_id";

function threadStorageKey(userId: string) {
  return `multi_coach_thread_v1_${userId}`;
}

function snapshotStorageKey(userId: string) {
  return `multi_coach_chat_snapshot_v1_${userId}`;
}

function getOrCreateThreadId(userId: string): string {
  const key = threadStorageKey(userId);
  let v = sessionStorage.getItem(key);
  if (!v) {
    v = crypto.randomUUID();
    sessionStorage.setItem(key, v);
  }
  return v;
}

function assistantBubbleText(m: ChatMessage): string {
  const c = String(m.content ?? "").trim();
  if (c) return String(m.content ?? "");
  if (m.tool_calls && m.tool_calls.length > 0) {
    return "（本轮含工具调用，完整记录已写入会话）";
  }
  return "";
}

function useAuthedWs(
  accessToken: string | null,
  inboundRef: MutableRefObject<(data: ServerEvent) => void>,
  onCloseRef: MutableRefObject<() => void>,
) {
  const wsRef = useRef<WebSocket | null>(null);

  const url = useMemo(() => {
    if (!accessToken) return "";
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const host = window.location.host;
    const q = `token=${encodeURIComponent(accessToken)}`;
    return `${proto}://${host}/api/ws?${q}`;
  }, [accessToken]);

  const connect = useCallback((): WebSocket | null => {
    if (!url) return null;
    wsRef.current?.close();
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(String(ev.data)) as ServerEvent;
        inboundRef.current(data);
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => onCloseRef.current();
    return ws;
  }, [url, inboundRef, onCloseRef]);

  return { wsRef, connect };
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

async function readJson<T>(r: Response): Promise<T> {
  return (await r.json()) as T;
}

function phaseStatusCn(status: string | undefined): string {
  const s = (status || "").trim();
  if (s === "completed") return "已完成";
  if (s === "in_progress") return "进行中";
  return "未完成";
}

function phaseProgressPct(p: PlanPhaseDto): number {
  const raw = p.progress_pct;
  if (raw !== undefined && raw !== null && String(raw).trim() !== "") {
    const n = typeof raw === "number" ? raw : parseInt(String(raw), 10);
    if (!Number.isNaN(n)) return Math.max(0, Math.min(100, Math.round(n)));
  }
  const s = (p.status || "").trim();
  if (s === "completed") return 100;
  if (s === "not_started") return 0;
  return 50;
}

type RagUploadJobApi = {
  status: "queued" | "processing" | "done" | "error";
  progress: number;
  message: string;
  result?: { doc_id: string; chunk_count: number; filename?: string; pdf_ocr_used?: boolean } | null;
  error?: string | null;
};

function postRagUploadForJob(
  accessToken: string,
  file: File,
  onUploadBytesProgress: (pct0to100: number) => void,
): Promise<{ job_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/me/rag/upload");
    xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);
    xhr.responseType = "json";
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable && ev.total > 0) {
        onUploadBytesProgress(Math.round((100 * ev.loaded) / ev.total));
      }
    };
    xhr.onload = () => {
      if (xhr.status === 401) {
        reject(Object.assign(new Error("401"), { code: "401" }));
        return;
      }
      if (xhr.status === 202) {
        const body = xhr.response as { job_id?: string };
        if (body?.job_id) {
          resolve({ job_id: body.job_id });
          return;
        }
        reject(new Error("服务器未返回 job_id"));
        return;
      }
      const resp = xhr.response as { detail?: unknown } | null;
      const detail =
        resp && typeof resp === "object" && "detail" in resp
          ? typeof resp.detail === "string"
            ? resp.detail
            : JSON.stringify(resp.detail)
          : xhr.statusText;
      reject(new Error(detail || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    const fd = new FormData();
    fd.append("file", file);
    xhr.send(fd);
  });
}

async function fetchRagJob(accessToken: string, jobId: string, signal?: AbortSignal): Promise<RagUploadJobApi> {
  const r = await fetch(`/api/me/rag/upload/jobs/${encodeURIComponent(jobId)}`, {
    headers: authHeaders(accessToken),
    signal,
  });
  if (r.status === 401) {
    throw Object.assign(new Error("401"), { code: "401" });
  }
  if (!r.ok) {
    const err = (await readJson<{ detail?: unknown }>(r).catch(() => ({}))) as { detail?: unknown };
    const d = err.detail;
    const msg =
      typeof d === "string" ? d : Array.isArray(d) ? JSON.stringify(d) : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return readJson<RagUploadJobApi>(r);
}

export default function App() {
  const [screen, setScreen] = useState<"boot" | "login" | "chat" | "memory">("boot");
  const [accessToken, setAccessToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [userId, setUserId] = useState<string>(() => localStorage.getItem(USER_KEY) || "");
  const [threadId, setThreadId] = useState("");
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [draftAssistant, setDraftAssistant] = useState("");
  const [debugLog, setDebugLog] = useState("");
  const [memory, setMemory] = useState<LongTermMemoryDto | null>(null);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [learningItems, setLearningItems] = useState<LearningIndexEntryDto[] | null>(null);
  const [learningListError, setLearningListError] = useState<string | null>(null);
  const [selectedLearningId, setSelectedLearningId] = useState<string | null>(null);
  const [learningDetail, setLearningDetail] = useState<LearningContentDetailDto | null>(null);
  const [learningDetailError, setLearningDetailError] = useState<string | null>(null);
  const [ragUploading, setRagUploading] = useState(false);
  const [ragUploadHint, setRagUploadHint] = useState<string | null>(null);
  const [ragUploadPercent, setRagUploadPercent] = useState(0);
  const [ragUploadStatusLine, setRagUploadStatusLine] = useState("");
  const [awaitingReply, setAwaitingReply] = useState(false);

  const scrollEndRef = useRef<HTMLDivElement>(null);
  const ragFileRef = useRef<HTMLInputElement>(null);
  const ragUploadGenRef = useRef(0);
  const wsInboundRef = useRef<(data: ServerEvent) => void>(() => {});
  const wsOnCloseRef = useRef<() => void>(() => {});
  const { wsRef, connect } = useAuthedWs(accessToken, wsInboundRef, wsOnCloseRef);

  const learningPhaseSidebarRows = useMemo(() => {
    const items = learningItems ?? [];
    const sorted = [...items].sort((a, b) => {
      const ta = new Date(a.updated_at || 0).getTime();
      const tb = new Date(b.updated_at || 0).getTime();
      return tb - ta;
    });
    type Row = { key: string; phaseLabel: string; pct: number; statusCn: string };
    const rows: Row[] = [];
    for (const it of sorted) {
      const phases = it.plan_phases ?? [];
      const hasSavedPlan = Boolean(it.has_learning_plan) || phases.length > 0;
      if (!hasSavedPlan) continue;
      const theme = it.title?.trim() || "未命名主题";
      if (phases.length === 0) continue;
      phases.forEach((p, i) => {
        rows.push({
          key: `${it.content_id}-p${i}`,
          phaseLabel: `「${theme}」P${i}：${(p.title || "").trim() || "—"}`,
          pct: phaseProgressPct(p),
          statusCn: phaseStatusCn(p.status),
        });
      });
    }
    return rows;
  }, [learningItems]);

  const dialogueMessages = useMemo(
    () => history.filter((m) => m.role === "user" || m.role === "assistant"),
    [history],
  );

  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [dialogueMessages, draftAssistant, historyError, ragUploadHint, ragUploadPercent, ragUploadStatusLine, learningDetail, selectedLearningId]);

  useEffect(() => {
    if (!userId) {
      setThreadId("");
      return;
    }
    setThreadId(getOrCreateThreadId(userId));
  }, [userId]);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setAccessToken(null);
    setUserId("");
    setScreen("login");
    setAwaitingReply(false);
    wsRef.current?.close();
  }, [wsRef]);

  const uploadRagFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !accessToken) return;
    const myGen = ++ragUploadGenRef.current;
    setRagUploading(true);
    setRagUploadHint(null);
    setRagUploadPercent(0);
    setRagUploadStatusLine("正在上传文件…");
    try {
      const { job_id } = await postRagUploadForJob(accessToken, file, (uploadPct) => {
        if (ragUploadGenRef.current !== myGen) return;
        setRagUploadPercent(Math.min(34, Math.round(uploadPct * 0.34)));
      });
      if (ragUploadGenRef.current !== myGen) return;
      setRagUploadPercent(35);
      setRagUploadStatusLine("已提交，后台处理中…");

      for (;;) {
        if (ragUploadGenRef.current !== myGen) return;
        const j = await fetchRagJob(accessToken, job_id);
        if (ragUploadGenRef.current !== myGen) return;
        setRagUploadStatusLine(j.message || "处理中");
        setRagUploadPercent(35 + Math.round((Math.max(0, Math.min(100, j.progress)) / 100) * 65));
        if (j.status === "done") {
          const r = j.result;
          if (r) {
            const ocrNote =
              r.pdf_ocr_used === true ? "（PDF 已用 OCR）" : r.pdf_ocr_used === false ? "（PDF 文本层）" : "";
            setRagUploadHint(
              `已入库「${r.filename ?? "文件"}」：doc_id=${r.doc_id}，${r.chunk_count} 块${ocrNote}，可用助手检索。`,
            );
          } else {
            setRagUploadHint("处理完成");
          }
          break;
        }
        if (j.status === "error") {
          setRagUploadHint(j.error || "入库失败");
          break;
        }
        await new Promise<void>((res) => setTimeout(res, 450));
      }
    } catch (ex) {
      if (ragUploadGenRef.current !== myGen) return;
      if (ex && typeof ex === "object" && "code" in ex && (ex as { code?: string }).code === "401") {
        logout();
        return;
      }
      setRagUploadHint(ex instanceof Error ? ex.message : String(ex));
    } finally {
      if (ragUploadGenRef.current === myGen) {
        setRagUploading(false);
        setRagUploadStatusLine("");
        setRagUploadPercent(0);
      }
    }
  };

  const validateSession = useCallback(async () => {
    const t = localStorage.getItem(TOKEN_KEY);
    if (!t) {
      setAccessToken(null);
      setUserId("");
      setScreen("login");
      return;
    }
    try {
      const r = await fetch("/api/auth/me", { headers: authHeaders(t) });
      if (!r.ok) {
        logout();
        return;
      }
      const data = await readJson<{ user_id: string }>(r);
      setAccessToken(t);
      setUserId(data.user_id);
      setScreen("chat");
    } catch {
      logout();
    }
  }, [logout]);

  useEffect(() => {
    void validateSession();
  }, [validateSession]);

  const loadHistory = useCallback(
    async (opts?: { clearDraftOnSuccess?: boolean }): Promise<boolean> => {
      if (!accessToken || !threadId) return false;
      setHistoryError(null);
      const url = `/api/me/session/${encodeURIComponent(threadId)}`;
      try {
        const r = await fetch(url, { headers: authHeaders(accessToken) });
        if (r.status === 401) {
          logout();
          return false;
        }
        if (!r.ok) {
          setHistoryError(`HTTP ${r.status}`);
          return false;
        }
        const data = (await r.json()) as SessionResponse;
        const msgs = Array.isArray(data.messages) ? data.messages : [];
        setHistory(msgs);
        if (opts?.clearDraftOnSuccess) {
          setDraftAssistant("");
        }
        return true;
      } catch (e) {
        setHistoryError(e instanceof Error ? e.message : String(e));
        return false;
      }
    },
    [accessToken, threadId, logout],
  );

  const loadLearningContents = useCallback(async () => {
    if (!accessToken) return;
    setLearningListError(null);
    try {
      const r = await fetch("/api/me/memory/learning-contents", { headers: authHeaders(accessToken) });
      if (r.status === 401) {
        logout();
        return;
      }
      if (!r.ok) {
        setLearningListError(`HTTP ${r.status}`);
        setLearningItems([]);
        return;
      }
      const data = await readJson<{ items?: LearningIndexEntryDto[] }>(r);
      setLearningItems(Array.isArray(data.items) ? data.items : []);
    } catch (e) {
      setLearningListError(e instanceof Error ? e.message : String(e));
    }
  }, [accessToken, logout]);

  useEffect(() => {
    wsOnCloseRef.current = () => {
      setAwaitingReply(false);
    };
  }, []);

  useEffect(() => {
    wsInboundRef.current = (data: ServerEvent) => {
      if (data.type === "pong") return;
      if (data.type === "run_start") {
        setDraftAssistant("");
        if (data.round) {
          setDebugLog(
            (s) =>
              s +
              `\n--- run_start round=${data.round}${data.auto ? " (auto_followup)" : ""} ---\n`,
          );
        }
        return;
      }
      if (data.type === "token") {
        setDraftAssistant((s) => s + data.delta);
        return;
      }
      if (data.type === "tool_start") {
        setDebugLog(
          (s) => s + `\n[tool_start] ${data.name ?? ""} ${JSON.stringify(data.input ?? {})}\n`,
        );
        return;
      }
      if (data.type === "tool_end") {
        setDebugLog((s) => s + `\n[tool_end] ${data.name ?? ""} -> ${data.output ?? ""}\n`);
        if (
          data.name === "scheduler_save_learning_plan" ||
          data.name === "scheduler_commit_learning_phases"
        ) {
          void loadLearningContents();
        }
        return;
      }
      if (data.type === "run_end") {
        setAwaitingReply(false);
        void loadHistory({ clearDraftOnSuccess: true });
        void loadLearningContents();
        return;
      }
      if (data.type === "warning") {
        setDebugLog((s) => s + `\n[warning] ${data.message ?? ""}\n`);
        return;
      }
      if (data.type === "error") {
        setAwaitingReply(false);
        setDebugLog((s) => s + `\n[error] ${data.message ?? ""}\n`);
      }
    };
  }, [loadHistory, loadLearningContents]);

  useEffect(() => {
    setAwaitingReply(false);
  }, [threadId]);

  useEffect(() => {
    if (screen === "chat" && accessToken && threadId) {
      void loadHistory();
    }
  }, [screen, accessToken, threadId, loadHistory]);

  useEffect(() => {
    if (screen === "chat" && accessToken) {
      void loadLearningContents();
    }
  }, [screen, accessToken, loadLearningContents]);

  const submitLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginError(null);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginUser.trim(), password: loginPass }),
      });
      if (!r.ok) {
        const err = (await readJson<{ detail?: unknown }>(r).catch(() => ({}))) as { detail?: unknown };
        const d = err.detail;
        const msg =
          typeof d === "string"
            ? d
            : Array.isArray(d)
              ? d.map((x) => (typeof x === "object" && x !== null ? JSON.stringify(x) : String(x))).join("；")
              : "登录失败";
        setLoginError(msg);
        return;
      }
      const data = await readJson<{ access_token: string; user_id: string }>(r);
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(USER_KEY, data.user_id);
      setAccessToken(data.access_token);
      setUserId(data.user_id);
      setLoginPass("");
      setScreen("chat");
    } catch (ex) {
      setLoginError(ex instanceof Error ? ex.message : String(ex));
    }
  };

  const startNewConversation = () => {
    if (!userId) return;
    const nid = crypto.randomUUID();
    sessionStorage.setItem(threadStorageKey(userId), nid);
    setThreadId(nid);
    setHistory([]);
    setDraftAssistant("");
    setInput("");
    setDebugLog("");
    setAwaitingReply(false);
  };

  const openLearningContentThread = async (contentId: string) => {
    if (!accessToken) return;
    setLearningListError(null);
    try {
      const r = await fetch(
        `/api/me/memory/learning-contents/${encodeURIComponent(contentId)}/preferred-thread`,
        { headers: authHeaders(accessToken) },
      );
      if (r.status === 401) {
        logout();
        return;
      }
      if (!r.ok) {
        const errBody = (await readJson<{ detail?: unknown }>(r).catch(() => ({}))) as { detail?: unknown };
        const d = errBody.detail;
        const msg =
          typeof d === "string"
            ? d
            : r.status === 404
              ? "该主题尚无已绑定的对话，请先在对应学习对话中打开。"
              : `HTTP ${r.status}`;
        setLearningListError(msg);
        return;
      }
      const data = await readJson<{ thread_id: string }>(r);
      if (!userId) return;
      sessionStorage.setItem(threadStorageKey(userId), data.thread_id);
      setThreadId(data.thread_id);
      void loadLearningContents();
    } catch (e) {
      setLearningListError(e instanceof Error ? e.message : String(e));
    }
  };

  const sendChat = async () => {
    const text = input.trim();
    if (!text || !accessToken || !threadId) return;
    if (awaitingReply) return;
    const userMsg: ChatMessage = { role: "user", content: text };
    setHistory((h) => [...h, userMsg]);
    setInput("");
    setDraftAssistant("");
    setDebugLog("");
    setAwaitingReply(true);

    let ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      ws = connect();
    }
    if (!ws) {
      setAwaitingReply(false);
      return;
    }
    try {
      if (ws.readyState !== WebSocket.OPEN) {
        await new Promise<void>((resolve, reject) => {
          ws.onopen = () => resolve();
          ws.onerror = () => reject(new Error("WebSocket 连接失败"));
        });
      }
    } catch {
      setAwaitingReply(false);
      return;
    }

    const payload = {
      type: "chat",
      thread_id: threadId,
      messages: [{ role: "user" as const, content: text }],
    };

    try {
      ws.send(JSON.stringify(payload));
    } catch {
      setAwaitingReply(false);
    }
  };

  const openMemoryScreen = () => {
    if (!userId || !accessToken) return;
    const snap: ChatSnapshot = { history, input, draftAssistant, debugLog };
    sessionStorage.setItem(snapshotStorageKey(userId), JSON.stringify(snap));
    setMemory(null);
    setMemoryError(null);
    setLearningItems(null);
    setLearningListError(null);
    setSelectedLearningId(null);
    setLearningDetail(null);
    setLearningDetailError(null);
    setScreen("memory");
  };

  const loadLongTermMemory = useCallback(async () => {
    if (!accessToken) return;
    setMemoryError(null);
    try {
      const r = await fetch("/api/me/memory/long-term", { headers: authHeaders(accessToken) });
      if (r.status === 401) {
        logout();
        return;
      }
      if (!r.ok) {
        setMemoryError(`HTTP ${r.status}`);
        return;
      }
      setMemory(await readJson<LongTermMemoryDto>(r));
    } catch (e) {
      setMemoryError(e instanceof Error ? e.message : String(e));
    }
  }, [accessToken, logout]);

  const loadLearningDetail = useCallback(
    async (cid: string) => {
      if (!accessToken) return;
      setLearningDetailError(null);
      setLearningDetail(null);
      try {
        const r = await fetch(`/api/me/memory/learning-contents/${encodeURIComponent(cid)}`, {
          headers: authHeaders(accessToken),
        });
        if (r.status === 401) {
          logout();
          return;
        }
        if (!r.ok) {
          setLearningDetailError(`HTTP ${r.status}`);
          return;
        }
        setLearningDetail(await readJson<LearningContentDetailDto>(r));
      } catch (e) {
        setLearningDetailError(e instanceof Error ? e.message : String(e));
      }
    },
    [accessToken, logout],
  );

  useEffect(() => {
    if (screen === "memory" && accessToken) {
      void loadLongTermMemory();
      void loadLearningContents();
    }
  }, [screen, accessToken, loadLongTermMemory, loadLearningContents]);

  useEffect(() => {
    if (screen === "memory" && accessToken && selectedLearningId) {
      void loadLearningDetail(selectedLearningId);
    }
  }, [screen, accessToken, selectedLearningId, loadLearningDetail]);

  const backFromMemory = () => {
    if (userId) {
      const raw = sessionStorage.getItem(snapshotStorageKey(userId));
      if (raw) {
        try {
          const o = JSON.parse(raw) as Partial<ChatSnapshot> & { streamLog?: string };
          setHistory(Array.isArray(o.history) ? o.history : []);
          setInput(typeof o.input === "string" ? o.input : "");
          setDraftAssistant(typeof o.draftAssistant === "string" ? o.draftAssistant : "");
          setDebugLog(
            typeof o.debugLog === "string"
              ? o.debugLog
              : typeof o.streamLog === "string"
                ? o.streamLog
                : "",
          );
        } catch {
          /* ignore */
        }
      }
    }
    setSelectedLearningId(null);
    setLearningDetail(null);
    setLearningDetailError(null);
    setScreen("chat");
  };

  if (screen === "boot") {
    return (
      <div className="layout">
        <p className="muted">正在校验登录状态…</p>
      </div>
    );
  }

  if (screen === "login") {
    return (
      <div className="layout login-layout">
        <h1 className="title">学习助手</h1>
        <p className="muted">请登录；令牌将保存在本机浏览器并自动用于接口与 WebSocket。</p>
        <form className="login-card" onSubmit={submitLogin}>
          <label className="field">
            <span>用户名</span>
            <input
              autoComplete="username"
              value={loginUser}
              onChange={(e) => setLoginUser(e.target.value)}
              placeholder="例如 demo"
            />
          </label>
          <label className="field">
            <span>密码</span>
            <input
              type="password"
              autoComplete="current-password"
              value={loginPass}
              onChange={(e) => setLoginPass(e.target.value)}
              placeholder="见后端 AUTH_USERS_JSON"
            />
          </label>
          {loginError ? <p className="error-text">{loginError}</p> : null}
          <button type="submit" className="primary" disabled={!loginUser.trim() || !loginPass}>
            登录
          </button>
        </form>
      </div>
    );
  }

  if (screen === "memory") {
    return (
      <div className="layout memory-layout">
        <header className="top-bar">
          <h1 className="title-inline">档案与学习</h1>
          <div className="top-actions">
            <button
              type="button"
              onClick={() => {
                void loadLongTermMemory();
                void loadLearningContents();
                if (selectedLearningId) void loadLearningDetail(selectedLearningId);
              }}
            >
              刷新
            </button>
            <button type="button" className="primary" onClick={backFromMemory}>
              返回对话
            </button>
          </div>
        </header>

        {selectedLearningId ? (
          <>
            <p className="muted">
              <button
                type="button"
                className="text-link"
                onClick={() => {
                  setSelectedLearningId(null);
                  setLearningDetail(null);
                  setLearningDetailError(null);
                }}
              >
                ← 返回在学主题列表
              </button>
            </p>
            <p className="muted small">查看该主题下已保存的摘要、要点与学习计划。</p>
            {learningDetailError ? <p className="error-text">{learningDetailError}</p> : null}
            {!learningDetail && !learningDetailError ? <p className="muted">加载详情…</p> : null}
            {learningDetail ? (
              <div className="memory-sections learning-detail">
                <h2 className="learning-detail-title">{learningDetail.title || "未命名主题"}</h2>
                <p className="muted small">content_id：{learningDetail.content_id}</p>
                <section>
                  <h3>摘要</h3>
                  <p>{learningDetail.summary?.trim() ? learningDetail.summary : "（暂无）"}</p>
                </section>
                <section>
                  <h3>关键词</h3>
                  <p>{learningDetail.keywords?.length ? learningDetail.keywords.join("、") : "（暂无）"}</p>
                </section>
                <section>
                  <h3>要点</h3>
                  {learningDetail.key_points?.length ? (
                    <ul>
                      {learningDetail.key_points.map((t, i) => (
                        <li key={`kp-${i}`}>{t}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">暂无</p>
                  )}
                </section>
                <section>
                  <h3>学习阶段</h3>
                  {learningDetail.plan_phases && learningDetail.plan_phases.length > 0 ? (
                    <table className="memory-phase-table">
                      <thead>
                        <tr>
                          <th>阶段</th>
                          <th>进度</th>
                          <th>完成状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {learningDetail.plan_phases.map((p, i) => (
                          <tr key={`mem-ph-${i}-${p.title}`}>
                            <td>{`P${i}：${(p.title || "").trim() || "—"}`}</td>
                            <td>{phaseProgressPct(p)}%</td>
                            <td>{phaseStatusCn(p.status)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="muted">暂无分阶段条目。</p>
                  )}
                </section>
                <section>
                  <h3>学习计划（已与用户核对）</h3>
                  {learningDetail.learning_plan?.trim() ? (
                    <div className="markdown-in-memory">
                      <MarkdownBody text={learningDetail.learning_plan} />
                    </div>
                  ) : (
                    <p className="muted">尚未写入。请在对话中与助手商定计划后，由助手调用保存工具写入。</p>
                  )}
                </section>
                <section>
                  <h3>掌握状态</h3>
                  <p>{learningDetail.mastery_status?.trim() || "（未标注）"}</p>
                </section>
                <section>
                  <h3>学习进度</h3>
                  {learningDetail.progress_summary?.trim() ? (
                    <div className="markdown-in-memory">
                      <MarkdownBody text={learningDetail.progress_summary} />
                    </div>
                  ) : (
                    <p className="muted">（暂无）</p>
                  )}
                </section>
                <p className="muted small">
                  计划确认时间：{learningDetail.plan_confirmed_at || "—"} · 最近更新：{learningDetail.updated_at || "—"}
                </p>
              </div>
            ) : null}
          </>
        ) : (
          <>
            <p className="muted">
              以下为服务端维护的<strong>用户档案</strong>与<strong>在学主题</strong>。深度档案合并由主助手在对话中按需触发；每轮结束仅有轻量规则补充。
            </p>
            {memoryError ? <p className="error-text">{memoryError}</p> : null}
            {learningListError ? <p className="error-text">{learningListError}</p> : null}
            {memory ? (
              <div className="memory-sections">
                <section>
                  <h2>人格与偏好</h2>
                  <ul>
                    {memory.personality?.length ? (
                      memory.personality.map((t, i) => <li key={`p-${i}`}>{t}</li>)
                    ) : (
                      <li className="muted">暂无</li>
                    )}
                  </ul>
                </section>
                <section>
                  <h2>风格</h2>
                  <ul>
                    {memory.style_preferences?.length ? (
                      memory.style_preferences.map((t, i) => <li key={`s-${i}`}>{t}</li>)
                    ) : (
                      <li className="muted">暂无</li>
                    )}
                  </ul>
                </section>
                <section>
                  <h2>稳定事实</h2>
                  <ul>
                    {memory.stable_facts?.length ? (
                      memory.stable_facts.map((t, i) => <li key={`f-${i}`}>{t}</li>)
                    ) : (
                      <li className="muted">暂无</li>
                    )}
                  </ul>
                </section>
                <p className="muted small">档案最近更新：{memory.updated_at || "—"}</p>
              </div>
            ) : !memoryError ? (
              <p className="muted">加载用户档案…</p>
            ) : null}

            <div className="memory-sections memory-learning-block">
              <h2>在学主题</h2>
              <p className="muted small">
                点击条目查看要点、学习计划与进度。开启新学习后，助手会协助你确认计划并写入本条目。
              </p>
              {learningItems === null && !learningListError ? <p className="muted">加载主题列表…</p> : null}
              {learningItems && learningItems.length === 0 ? (
                <p className="muted">暂无已登记的学习主题。</p>
              ) : null}
              {learningItems && learningItems.length > 0 ? (
                <ul className="learning-index-list">
                  {learningItems.map((it) => (
                    <li key={it.content_id}>
                      <button
                        type="button"
                        className="learning-index-btn"
                        onClick={() => setSelectedLearningId(it.content_id)}
                      >
                        <span className="learning-index-title">{it.title || it.content_id}</span>
                        {it.summary ? (
                          <span className="learning-index-summary muted">
                            {it.summary.length > 140 ? `${it.summary.slice(0, 140)}…` : it.summary}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="chat-app">
      <aside className="chat-sidebar" aria-label="学习内容与学习计划">
        <div className="sidebar-block sidebar-block-learning">
          <h2 className="sidebar-block-title">学习内容</h2>
          <button type="button" className="sidebar-new-chat" onClick={startNewConversation}>
            ＋ 新对话
          </button>
          {learningListError ? <p className="error-text sidebar-note">{learningListError}</p> : null}
          {learningItems === null && !learningListError ? (
            <p className="muted sidebar-note">加载列表…</p>
          ) : (
            <ul className="sidebar-learning-list">
              {[...(learningItems ?? [])]
                .slice()
                .sort((a, b) => {
                  const ta = new Date(a.updated_at || 0).getTime();
                  const tb = new Date(b.updated_at || 0).getTime();
                  return tb - ta;
                })
                .map((it) => (
                  <li key={it.content_id}>
                    <button
                      type="button"
                      className="sidebar-learning-btn"
                      onClick={() => void openLearningContentThread(it.content_id)}
                      title={it.title?.trim() || "未命名主题"}
                    >
                      <span className="sidebar-learning-title">{it.title?.trim() || "未命名主题"}</span>
                    </button>
                  </li>
                ))}
            </ul>
          )}
          {learningItems && learningItems.length === 0 ? (
            <p className="muted sidebar-note">暂无学习内容；在对话中开启学习后将显示于此。</p>
          ) : null}
        </div>
        <div className="sidebar-block">
          <h2 className="sidebar-block-title">学习计划</h2>
          {learningListError ? <p className="error-text sidebar-note">{learningListError}</p> : null}
          {learningItems === null && !learningListError ? (
            <p className="muted sidebar-note">加载计划表…</p>
          ) : (
            <div className="sidebar-table-wrap">
              <table className="learn-plan-table learn-plan-table-phases">
                <thead>
                  <tr>
                    <th>阶段</th>
                    <th>进度</th>
                    <th>完成状态</th>
                  </tr>
                </thead>
                <tbody>
                  {learningPhaseSidebarRows.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="muted learn-plan-empty-row">
                        暂无阶段计划
                      </td>
                    </tr>
                  ) : (
                    learningPhaseSidebarRows.map((row) => (
                      <tr key={row.key}>
                        <td className="learn-col-phase">{row.phaseLabel}</td>
                        <td className="learn-col-pct">{row.pct}%</td>
                        <td>{row.statusCn}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
              {learningItems && learningItems.length === 0 ? (
                <p className="muted sidebar-note">暂无学习主题；在对话中开启学习后将显示于此。</p>
              ) : null}
            </div>
          )}
        </div>
      </aside>

      <div className="chat-shell chat-shell-main">
      <header className="top-bar chat-top">
        <h1 className="title-inline">学习助手</h1>
        <div className="top-actions">
          <span className="badge user-badge">已登录：{userId}</span>
          <button type="button" onClick={() => void loadHistory()}>
            刷新会话
          </button>
          <input
            ref={ragFileRef}
            type="file"
            className="visually-hidden"
            accept=".txt,.md,.markdown,.json,.csv,.log,.py,.ts,.tsx,.js,.jsx,.html,.htm,.xml,.yaml,.yml,.css,.sql,.sh,.env,.c,.h,.cpp,.go,.rs,.java,.pdf,application/pdf"
            aria-hidden
            tabIndex={-1}
            onChange={(ev) => void uploadRagFile(ev)}
          />
          <button
            type="button"
            disabled={ragUploading}
            onClick={() => ragFileRef.current?.click()}
            title="文本或 PDF（文字可复制型）切块写入本用户 SQLite FTS5 + Chroma；扫描版 PDF 需先 OCR"
          >
            {ragUploading ? "上传中…" : "上传知识库"}
          </button>
          <button type="button" onClick={openMemoryScreen}>
            档案与学习
          </button>
          <button type="button" onClick={logout}>
            退出登录
          </button>
        </div>
      </header>

      {ragUploading ? (
        <div className="rag-upload-progress" role="status" aria-live="polite">
          <div className="rag-upload-progress-track" aria-hidden>
            <div className="rag-upload-progress-fill" style={{ width: `${Math.max(2, ragUploadPercent)}%` }} />
          </div>
          <p className="rag-upload-progress-meta muted">{ragUploadStatusLine || "处理中…"}</p>
        </div>
      ) : null}
      {ragUploadHint ? (
        <p className={ragUploadHint.startsWith("已入库") ? "muted chat-upload-ok" : "error-text chat-error"}>
          {ragUploadHint}
        </p>
      ) : null}
      {historyError ? <p className="error-text chat-error">{historyError}</p> : null}

      <div className="chat-scroll" role="log" aria-live="polite">
        {dialogueMessages.map((m, i) => {
          const isUser = m.role === "user";
          const body = isUser ? String(m.content ?? "") : assistantBubbleText(m);
          if (!body.trim()) return null;
          return (
            <div key={`${i}-${isUser ? "u" : "a"}`} className={`bubble ${isUser ? "user" : "assistant"}`}>
              <div className="bubble-label">{isUser ? "你" : "助手"}</div>
              <div className="bubble-body">
                <MarkdownBody text={body} />
              </div>
            </div>
          );
        })}
        {draftAssistant ? (
          <div className="bubble assistant streaming">
            <div className="bubble-label">助手</div>
            <div className="bubble-body">
              <MarkdownBody text={draftAssistant} />
            </div>
          </div>
        ) : null}
        <div ref={scrollEndRef} />
      </div>

      {debugLog.trim() ? (
        <details className="debug-details">
          <summary>技术细节（工具 / 轮次 / 警告）</summary>
          <pre className="debug-pre">{debugLog}</pre>
        </details>
      ) : null}

      <footer className="chat-composer">
        <form
          className="composer-form"
          onSubmit={(e) => {
            e.preventDefault();
            void sendChat();
          }}
        >
          <textarea
            className="composer-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendChat();
              }
            }}
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            rows={2}
            aria-label="消息输入"
          />
          <button
            type="submit"
            className="primary send-btn"
            disabled={!input.trim() || awaitingReply}
            aria-busy={awaitingReply}
          >
            {awaitingReply ? "等待回复…" : "发送"}
          </button>
        </form>
      </footer>
    </div>
    </div>
  );
}
