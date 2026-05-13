# multi_coach_agent 后端实现说明（面试版）

本文用于快速讲清楚：这个后端是如何实现 **LangGraph 多智能体调度 + 阻塞/非阻塞 Worker + RAG + 记忆系统 + WebSocket 流式** 的。

---

## 1. 一句话架构

后端采用 **Scheduler（主图）+ 单一 Worker 子图模板** 的分离架构：

- 主图是静态 LangGraph，只做编排与决策。
- Worker 是统一模板，运行时只靠传入 prompt 区分任务。
- 非阻塞任务结果通过全局通知队列回流到主图（同请求自动续跑 + idle 空闲消费）。
- 会话按用户+线程存本地 md，RAG 与记忆按用户隔离。

---

## 2. 目录职责（核心）

- `app/graphs/runtime.py`：启动时编译主图/Worker（合并 MCP 与本地工具）
- `app/tools/mcp_bundle.py`：langchain-mcp-adapters 加载 MCP，与本地工具合并并按角色过滤
- `app/tools/scheduler_tools.py`：主图可用工具（阻塞/非阻塞 worker、RAG）
- `app/tools/worker_tools.py`：Worker 受限工具
- `app/services/scheduler_runner.py`：统一跑图与流式事件转发
- `app/services/worker_runner.py`：阻塞/非阻塞 worker 执行器
- `app/task_notify/*`：全局通知队列与 XML 编解码
- `app/rag/*`：分块、FTS5、向量检索、RRF 融合
- `app/memory/*`：长期记忆存储与短期上下文构建
- `app/session/md_session_store.py`：会话 md 持久化（含 tool_calls）
- `app/api/ws.py`：WebSocket chat 主入口
- `app/api/session_routes.py`：会话读取接口

---

## 3. 主链路：一次 chat 请求发生了什么

以 `WS /ws/{user_id}` 收到 `{"type":"chat","thread_id":"...","messages":[...新增句...]}` 为例：

1. **读取历史会话**
   - 从 `DATA_DIR/<user_id>/sessions/<thread_id>.md` 读取全量消息。
   - 与本轮新增消息合并。

2. **构建运行时上下文（短期+长期）**
   - 注入主调度系统提示（scheduler bootstrap）。
   - 读取 `DATA_DIR/<user_id>/memory/long_term.json`，生成长期记忆 `SystemMessage`。
   - 对消息做短期窗口截断（`SHORT_TERM_MAX_MESSAGES`）。

3. **运行 Scheduler 图**
   - Scheduler 可能调用工具触发 Worker。
   - 阻塞 worker：当前轮 `await`，结果立即进入 `tool_message`。
   - 非阻塞 worker：后台执行，结束后写 `<task-notification>` 到通知队列。

4. **自动续跑（不依赖用户再发一句）**
   - 主图一轮结束后若队列还有通知，服务端自动再开下一轮（受 `MAX_SCHEDULER_AUTO_ROUNDS` 限制）。
   - 在同一大轮的后续步骤里，agent 节点也会继续吸收队列。

5. **图内反思上限**
   - 单次主图 / Worker 子图运行受 `SCHEDULER_RECURSION_LIMIT` / `WORKER_RECURSION_LIMIT`（LangGraph `recursion_limit`）约束，与第 4 步的「队列续跑轮次」是不同维度。

6. **持久化**
   - 把本次新增 delta 合并回全量消息。
   - 更新长期记忆（规则抽取）。
   - 写回 `sessions/<thread>.md`（保留 tool / tool_calls）。

7. **流式返回**
   - 通过 WS 推 token、tool_start、tool_end、run_start/run_end。

---

## 4. 为什么是“单一 Worker 模板”

设计点：Worker 没有多类型定义，只有一套图和一套受限工具，差异由主图传入：

- `worker_prompt`：定义角色、边界、输出风格
- `task_instruction`：定义本次具体任务

优点：

- 主图拓扑稳定，不会被动态改图破坏。
- 便于治理（权限、审计、观测统一）。
- 容易扩展（只是 prompt 和工具策略变化）。

---

## 5. 阻塞 / 非阻塞语义（面试高频）

### 阻塞 Worker

- 主图当前轮必须等待子图完成。
- 结果当轮可见，立即影响后续推理和工具链。
- 用于强依赖任务（例如：后续步骤必须使用该结果）。

### 非阻塞 Worker

- 后台并发运行，不阻塞当前调度轮。
- 完成后写 XML 到全局队列。
- 主图后续步骤或自动续跑轮次再消费结果。
- 用于可并行、允许延迟汇总的任务。

---

## 6. RAG 实现（混合召回）

目标：兼顾中文关键词匹配与语义召回。

1. **入库**
   - 文本切 chunk（固定长度+重叠）。
   - `jieba` 分词后空格拼接写入 SQLite FTS5（BM25）。
   - `bge-m3` 生成向量写入 Chroma。

2. **检索**
   - FTS5 BM25 得到一组排序。
   - 向量召回得到另一组排序。
   - 用 RRF（Reciprocal Rank Fusion）融合，返回最终 chunk。

3. **隔离**
   - SQLite 与 Chroma 都按 `user_id` 路径隔离，避免跨用户污染。

---

## 7. 记忆系统实现

### 7.1 长期记忆（持久）

- 文件：`DATA_DIR/<user_id>/memory/long_term.json`
- 字段：
  - `personality`
  - `style_preferences`
  - `learning_goals`
  - `stable_facts`
- 每轮结束后从近期用户话语做规则抽取并更新。
- 下一轮运行前会被注入系统消息给大模型。

### 7.2 短期记忆（上下文窗口）

- 本质就是“本轮送进模型的 message 上下文”。
- 来自会话消息，但会按 `SHORT_TERM_MAX_MESSAGES` 截断。
- 只影响推理输入，不删除 md 全量历史。

> 面试可直接说：  
> “短期记忆是 runtime context，长期记忆是 persistent profile；两者解耦。”

---

## 8. 会话持久化（本地 md）

按用户和线程保存：

- `DATA_DIR/<user_id>/sessions/<thread_id>.md`

特点：

- 可读、可审计、可回放。
- assistant 的 `tool_calls` 以 JSON 代码块保存。
- HTTP `GET /sessions/{user_id}/{thread_id}` 可直接给前端初始化展示。

---

## 9. 空闲队列消费（idle processor）

当用户没有活跃 WS 调度时：

- 后台协程按 `IDLE_QUEUE_POLL_INTERVAL` 轮询队列。
- 如果某用户有通知且互斥锁未被占用，就自动跑主图处理通知。
- 处理完成后更新该用户会话快照与 md。

作用：非阻塞任务不会“悬空”，即使前端暂时不在线也能被系统消化。

---

## 10. 并发与一致性策略

- 用户级互斥锁：同一 `user_id` 下 WS 调度与 idle 消费不并发执行。
- 非阻塞任务通过统一 broker 入队，主图/idle 按用户出队。
- md 写入采用临时文件替换，降低半写风险。

---

## 11. 你可以这样在面试里讲（60 秒版）

> “我把系统拆成 Scheduler 主图和统一 Worker 子图。主图只负责编排，Worker 不改图结构，靠 prompt 区分任务。  
> 对需要立即结果的任务用阻塞 worker，当轮拿回结果；可并行任务用非阻塞 worker，完成后通过 XML 通知队列回流，主图会自动续跑，不需要用户再发一句。  
> 检索层做了中文混合召回：jieba+FTS5 做 BM25，bge-m3+Chroma 做向量，最后 RRF 融合。  
> 记忆上分短期和长期：短期是 runtime context，会截断；长期持久化为每用户 memory profile，并在每轮前注入模型。  
> 会话全量持久化到 md，包含 tool_calls，方便审计和前端恢复。”

---

## 12. 可继续优化点

- 长期记忆从“规则抽取”升级为“LLM 结构化抽取 + 冲突消解”。
- 增加记忆置信度与过期机制。
- 引入 LangGraph checkpointer / 外部存储（Redis/Postgres）实现跨进程恢复。
- 为通知队列与调度链路加 trace_id，便于全链路观测。

