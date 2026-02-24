# RARK 生态调研：定位分析与差异化

> 调研时间：2026-02，基于实际抓取各框架官方文档。

---

# 1. 调研对象

| 框架       | 定位                              | 来源                   |
|------------|-----------------------------------|------------------------|
| OpenClaw   | 个人 AI 助手（接入 IM 通道）      | docs.openclaw.ai       |
| AgentScope | LLM 多 agent 编排（Alibaba）      | doc.agentscope.io      |
| AutoGen    | LLM 多 agent 编排（Microsoft）    | microsoft.github.io    |

---

# 2. OpenClaw 实际设计

## 2.1 定位澄清

OpenClaw（前身 Clawdbot / Moltbot）是**个人 AI 助手框架**，不是机器人运行时。它的设计目标是把 LLM agent 接入 WhatsApp / Telegram / Slack 等通道，在用户机器上自主执行任务。

## 2.2 Agent Loop

```
intake → context assembly → model inference → tool execution → reply → persist
```

默认 timeout 600s，通过 AbortSignal 支持显式取消。

## 2.3 任务状态

```
pending → blocked → claimed → in-progress → completed / failed
```

包含 `blocked`（等待依赖）状态——这是 RARK 当前缺少的。

## 2.4 调度机制

**串行执行通道**（serialized execution lanes），同一 session 的任务排队串行，无优先级抢占，无 PAUSED/resume 语义。

"中断"是消息级别的 steer——在下一次 tool call 检查点注入新消息，**不是任务级抢占**。

## 2.5 Skill 系统

Skills 是可复用模块（mintlify skill、1password skill 等），从 workspace 目录加载。无正式的注册/调度机制文档。

---

# 3. AgentScope 设计

## 3.1 定位

面向 LLM 应用的生产级多 agent 框架，核心抽象是 `ReActAgent`（推理 + 工具执行）。

## 3.2 中断机制（最值得借鉴）

AgentScope 的中断实现了两层语义：

1. **asyncio 取消**：调用 `agent.interrupt()` → cancel 当前 reply asyncio.Task
2. **部分结果保留**：被取消的 Tool 如果已 `yield` 了部分结果，这些结果会被保存（"gracefully preserves all results yielded up to that point"）
3. **无缝恢复**：Distributed Interrupt Service 支持上下文保留后的恢复

> **RARK 当前差距**：`_cancel_running_skill()` 是硬取消，无部分结果保留，resume 后 skill 从头重跑。

## 3.3 任务分解：PlanNotebook

对于复杂多步骤任务，AgentScope 提供 `PlanNotebook`：

- 将复杂目标拆分为有序、可追踪的步骤
- 支持创建、修改、暂停、恢复多个并发计划
- 中断后能从已完成的步骤继续

RARK 目前的任务是原子的（不可分解为子步骤）。

## 3.4 并发执行

```python
# AgentScope 并行 tool 调用
results = await asyncio.gather(*[execute_tool(tc) for tc in tool_calls])
```

RARK 当前是单活跃任务（uniprocessor 模型），不支持并发执行。

## 3.5 可观测性

集成 OpenTelemetry，完整的分布式追踪。RARK 当前只有 `print()`。

---

# 4. AutoGen 0.4 设计

## 4.1 定位

基于 Actor 模型的事件驱动多 agent 框架，面向 LLM 应用编排。

## 4.2 运行时模型

```
Core API: 事件驱动 Actor 运行时（跨语言 .NET/Python）
AgentChat API: 高层封装，提供常见多 agent 模式（双人对话、GroupChat 等）
```

0.4 最大变化：从同步 ConversableAgent 对话模式转向**完全异步 Actor 消息传递**。

## 4.3 调度与抢占

官方文档中**未发现优先级调度或任务级抢占机制**。AutoGen 的消息路由通过中央化组件处理，便于观测，但不是基于任务优先级的抢占调度器。

---

# 5. 三框架 vs RARK 对比

| 维度                   | OpenClaw        | AgentScope       | AutoGen 0.4      | RARK            |
|------------------------|-----------------|------------------|------------------|-----------------|
| **目标域**             | 个人 AI 助手    | LLM 多 agent     | LLM 多 agent     | 机器人任务运行时 |
| **优先级抢占**         | ❌ 串行通道     | ❌               | ❌               | ✅ max-heap     |
| **PAUSED + resume**    | ❌              | ❌（协程保留）   | ❌               | ✅ 一等公民     |
| **中断后部分结果保留** | ❌              | ✅               | ❌               | ❌（待补）      |
| **崩溃恢复**           | ❌              | checkpoint 语义  | ❌               | ✅ SQLite       |
| **任务依赖（BLOCKED）**| ✅              | PlanNotebook     | ❌               | ❌（待补）      |
| **LLM 集成**           | ✅ 核心         | ✅ 核心          | ✅ 核心          | ❌ 有意不含     |
| **多 agent 协调**      | ✅              | ✅               | ✅               | ❌（单内核）    |
| **可观测性**           | 基础            | OpenTelemetry    | 基础             | print()         |
| **嵌入式/机器人适配**  | ❌              | ❌               | ❌               | ✅ 设计目标     |

---

# 6. RARK 不是在重复造轮子

## 6.1 问题域不同

上述三个框架都是 **LLM-centric 的多 agent 编排工具**，解决的是"如何让多个 LLM agent 协作完成复杂任务"。

RARK 解决的是**机器人系统的任务生存问题**：优先级抢占、PAUSED 恢复、崩溃重启后继续——这更接近 RTOS（实时操作系统）调度器的概念，而非 LLM agent 框架。

## 6.2 最接近的横向类比

| 类比对象       | 相似点                        |
|----------------|-------------------------------|
| FreeRTOS       | 优先级任务调度，suspend/resume |
| ROS2 Action    | 可抢占 goal，cancel/feedback  |
| Linux 进程调度 | 多任务抢占，任务持久存在       |

RARK 是在 Python asyncio 层面实现这些概念，专为机器人 agent 场景裁剪。

## 6.3 RARK 独有的组合

**优先级数值抢占 + PAUSED 状态 + asyncio.Task 取消 + SQLite 崩溃恢复** 这个组合，在三个框架中都不存在。

---

# 7. 从调研中发现的有效借鉴点

## 7.1 来自 AgentScope

**中断后保留部分结果**：当前 RARK 硬取消 skill，对于有外部副作用的操作（已发送指令给执行器）可能出现问题。`task.metadata` 可以作为 skill 传递进度的载体。

**PlanNotebook 概念**：对于复杂多步骤机器人任务（去厨房 → 找杯子 → 抓取 → 倒水），需要子任务树支持。

## 7.2 来自 OpenClaw

**BLOCKED 状态**：任务依赖图（任务 B 等任务 A 完成才能开始），RARK 调度器可以扩展支持。

## 7.3 主动排除的方向

| 特性          | 排除理由                                    |
|---------------|---------------------------------------------|
| LLM 推理层    | RARK 是 LLM-agnostic 的调度内核，层次分离   |
| 分布式 Actor  | 机器人嵌入式场景单节点，过度工程化          |
| MCP 工具集成  | 属于 SkillRunner 之上的上层，不是内核责任   |
| 多 agent 协调 | 当前单内核模型是有意约束，适合嵌入式        |
