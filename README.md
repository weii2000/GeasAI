# Geas

Geas 是一个使用 Python 实现的模块化 LLM Agent Runtime，以及构建在其上的
Plan/Review 双 Agent 规划系统。项目参考 [Pi](https://github.com/earendil-works/pi) 中
`pi-ai` 和 `pi-agent-core` 的核心抽象边界，复刻数据契约与运行流程，而非逐行
翻译 TypeScript 源码；TypeScript TUI 仅作为交互入口。

项目当前包含：

- 统一的消息、模型、Provider 和流式事件协议；
- 支持工具循环、取消和错误传播的 Agent Core；
- 相互隔离的 Plan Agent 与 Review Agent；
- Skill 渐进式披露和 MCP 远程工具调用；
- JSON Session 持久化与恢复；
- Review 通过后进入 HITL 人工审批，用户批准后确定性发布到 PlanWise；
- MCP 调用失败时保留待审批状态，并使用 Session ID 幂等重试；
- 单阶段 Eval、延迟、Token 和成本记录。

## 架构

```mermaid
flowchart TD
    TUI[TypeScript TUI] <-->|JSONL RPC| RPC[Python RPC]
    RPC --> SESSION[PlanSession]
    SESSION --> PLAN[Plan Agent]
    SESSION --> REVIEW[Review Agent]
    PLAN --> CORE[Geas Core<br/>Agent Loop + Tools]
    REVIEW --> CORE
    CORE --> AI[Geas AI<br/>Models + Streaming]
    AI --> PROVIDERS[OpenAI-compatible Providers]
    SKILLS[Base / Plan / Review Skills] --> SESSION
    SESSION --> STORE[JSON Session Store]
    SESSION -->|Review 通过| HUMAN[HITL 人工确认]
    HUMAN -->|反馈| REVIEW
    HUMAN -->|批准| ACTION[publish_plan Action]
    ACTION --> MCP[MCPRegistry]
    MCP --> PLANWISE[PlanWise]
```

| 模块 | 职责 |
| --- | --- |
| `geas/ai` | 统一模型、消息、内容块、流式事件和 Provider 调用 |
| `geas/core` | Agent 状态、Agent Loop、工具执行和事件流 |
| `geas/plan_agent` | Plan/Review 状态机、Profile、Skill 和 Session |
| `geas/actions` | 由程序流程确定性触发的动作 |
| `geas/mcp.py` | MCP 连接、认证和通用远程工具调用 |
| `geas/rpc.py` | 组装各层，并通过 JSONL 与 TUI 通信 |
| `tui` | 基于 `pi-tui` 的终端交互界面 |

## 本地运行

要求：

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22.19+

安装依赖：

```bash
uv sync
npm --prefix tui ci
```

启动：

```bash
uv run python main.py
```

首次进入后：

1. 使用 `/model` 分别选择 PLAN 和 REVIEW 模型；
2. 使用 `/login` 配置 Provider API Key；如需连接 PlanWise，也在此登录；
3. 输入目标，开始规划。

常用命令：

| 命令 | 作用 |
| --- | --- |
| `/model` | 配置 Plan/Review 模型 |
| `/login` | 配置 Provider API Key，或登录 PlanWise 并连接 MCP |
| `/new` | 创建新 Session |
| `/resume` | 恢复当前项目的历史 Session |
| `/quit` | 退出 |

## 环境变量

也可以复制示例配置后直接编辑：

```bash
cp .env.example .env
```

PLAN 和 REVIEW 必须分别配置 Provider 与 Model；没有配置时 Geas 会明确报错，
不会使用隐式默认值。

PlanWise MCP 是可选集成。配置 URL 后，可以使用 `/login` 选择 PlanWise
并输入账号密码。密码不会保存；Runtime 在内存中维护 Access / Refresh Token，
并在发布前按需刷新 Access Token。

Review 通过后，计划仍需用户输入 `y` 确认。确认后 Geas 使用 Session ID
作为幂等键调用 `create_plan`；调用失败时保留 `PENDING_APPROVAL` 状态，
可以使用相同幂等键重试。

```dotenv
GEAS_MCP_PLANWISE_URL=http://127.0.0.1:8000/mcp

# 可选：也可以直接提供静态 Access Token
# GEAS_MCP_PLANWISE_TOKEN=
```

## Skill

Skill 按适用阶段放置：

```text
skills/
├── base/
├── plan/
└── review/
```

Geas 首先只向模型提供 Skill 的名称、描述和位置；模型需要时再通过
`read_skill` 读取完整 `SKILL.md`。

Skill 启用的 Bash Tool 会在 Geas 当前运行环境中执行命令。不要直接运行
不可信 Skill；需要隔离时使用容器，并严格控制挂载目录和凭据。

## 测试与 Eval

```bash
uv run pytest -q
npm --prefix tui run build
```

运行真实模型的单阶段 Eval：

```bash
uv run python -m evals.single_phase
```

临时覆盖 Eval 模型：

```bash
uv run python -m evals.single_phase \
  --provider deepseek \
  --model deepseek-v4-flash
```

结果默认保存到 `eval-results/single-phase/`，包括每个 Case 的检查结果、
延迟、Token、成本、失败案例和汇总通过率。使用 `--no-save` 可以只打印结果。

## Docker

准备 `.env` 后运行：

```bash
docker compose run --rm geas
```

Compose 会只读挂载 `skills/`，并使用命名卷保存 Session。Docker 提供基础隔离，
但不是针对恶意代码的完整安全沙盒。

## 当前边界

- 目前只实现 OpenAI-compatible 文本模型；
- 图片输入以及模型 Provider 的 OAuth 登录与 Token 自动刷新尚未实现；
- PlanWise JWT Access Token 刷新已经支持；
- Session 只支持单进程写入；
- Eval 是小型基线，不代表生产环境质量保证。
