# 仓库协作约定（multi_coach_agent）

## 架构摘要

- **主图（Scheduler）**：静态 LangGraph，负责收任务、调用工具创建/触发 Worker、在用户可结束时下结论。
- **Worker**：与主图分离的**单一** LangGraph 定义；仅通过主智能体传入的提示词与工具绑定区分实例。
- **阻塞 Worker**：主调度在当前轮 `await` 子图完成；结果写入本轮 `tool_message`，立即参与后续推理。
- **非阻塞 Worker**：`asyncio` 后台执行；完成后向**全局通知队列**写入 `<task-notification>...</task-notification>` XML，主图下一轮吸收进状态。
- **RAG**：jieba 分词 + 空格拼接 → SQLite FTS5（BM25）；BGE-M3 向量 → Chroma；RRF 融合；**按 user_id 隔离**数据目录与集合。

## 开发约束

- 不随意改动主图静态拓扑；Worker 不得运行时修改主图节点/边。
- 多用户：禁止跨用户读写 RAG 存储与通知队列（队列键含 `user_id`）。
- 新增依赖写入 `backend/pyproject.toml`，本地开发优先 `uv sync` 或 `pip install -e backend`。

## 环境变量

见 `backend/.env.example`。未配置 `OPENAI_API_KEY` 时仍可通过占位密钥完成**导入/启动**，但模型调用会失败；正式使用请填写密钥与可选 `OPENAI_BASE_URL`。

**反思轮次**：`SCHEDULER_RECURSION_LIMIT` / `WORKER_RECURSION_LIMIT` 对应 LangGraph 单次图内的 agent–tool 循环上限；与 `MAX_SCHEDULER_AUTO_ROUNDS`（队列通知触发的**主图**自动续跑次数）不同。**MCP**：`MCP_SERVERS_JSON` + `SCHEDULER_MCP_TOOL_NAMES` / `WORKER_MCP_TOOL_NAMES`（见 `app/tools/mcp_bundle.py`）；MCP 工具与本地工具合并后按角色白名单过滤；若 MCP 需用户态鉴权，请在服务器配置里使用 `headers` 等（适配器支持），勿跨用户共享带用户上下文的连接。

## 本地运行

后端（在 `backend` 目录）：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

前端（在 `frontend` 目录）：

```bash
npm install
npm run dev
```

WebSocket：`/ws/{user_id}`。`chat` 消息体示例：

```json
{
  "type": "chat",
  "thread_id": "default",
  "messages": [{ "role": "user", "content": "本轮只发新增的一句/一段" }]
}
```

服务端从 `DATA_DIR/<user_id>/sessions/<thread_id>.md` 读取历史，与 `messages` 合并后再跑主图；结束后把**完整**消息链（含 `tool_calls` 与 `tool`）写回该 md。`thread_id` 省略时为 `default`。

HTTP：`GET /sessions/{user_id}/{thread_id}` 返回 `{ "messages": [...], "exists": bool, "storage": "相对 DATA_DIR 的路径" }`，供前端初始化展示。

服务端会在每条 `chat` 消息前自动注入主调度系统提示（含 `worker_prompt` 撰写模板）。**非阻塞**子任务若在主图运行结束后才入队，服务端会在**同一次请求内**自动续跑主图（无需用户再发一句），上限见环境变量 `MAX_SCHEDULER_AUTO_ROUNDS`。

当用户**没有正在进行的 WS 调度**（未占用该 user 的互斥锁）时，进程内 **`idle_queue_processor`** 会按 `IDLE_QUEUE_POLL_INTERVAL` 轮询队列：若有 `task-notification`，会基于该用户最近一次会话快照（由上次 WS 成功跑完后写入）自动跑主图；若无快照则使用一条系统占位用户消息启动。可用 `IDLE_QUEUE_PROCESSOR_ENABLED=false` 关闭。

记忆：系统维护每用户长期记忆（`DATA_DIR/<user_id>/memory/long_term.json`，包括 personality/style/goals/facts），每轮调度前注入系统消息；短期记忆是运行时上下文窗口（来自会话消息截断，默认 `SHORT_TERM_MAX_MESSAGES`），仅影响喂给模型的内容，不会删掉 `sessions/*.md` 全量历史。
