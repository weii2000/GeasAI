# Geas

Geas 是一个 Python LLM Agent Runtime，以及建立在它上面的两个独立应用：

- **Blueprint**：把目标转化为经过评审的可执行计划；
- **Wellphone**：Mac 上运行 Agent，iPhone 通过原生 Kit 在后台执行工具。

Runtime 参考 [Pi](https://github.com/earendil-works/pi) 的核心抽象边界，复刻消息、
模型、流式事件、工具调用和 Agent Loop，而不是逐行翻译 TypeScript 源码。

## 架构

~~~text
geas/
├── ai/                  # 模型、消息、Provider、流式事件
├── core/                # Agent 状态、Agent Loop、工具执行
└── integrations/
    └── mcp.py           # 可复用 MCP 适配

apps/
├── blueprint/           # Blueprint 产品、TUI、Skill、Eval
└── wellphone/           # 手机 Agent Server 与唯一 Xcode 工程
~~~

geas.ai 和 geas.core 不知道任何具体产品。两个 App 直接组合 Runtime，
没有额外的 BaseAgent、注册中心或多 Agent 框架。

## Blueprint

要求 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Node.js 22.19+。

~~~bash
uv sync
npm --prefix apps/blueprint/tui ci
cp apps/blueprint/.env.example apps/blueprint/.env
uv run python -m apps.blueprint.main
~~~

首次进入后，使用 /model 配置 PLAN 和 REVIEW 模型，使用 /login 配置
Provider API Key 或登录 PlanWise。常用命令还有 /new、/resume 和 /quit。

PlanWise MCP 是可选集成。Review 通过后仍需人工确认，批准后使用 Session ID
作为幂等键发布；失败会保留待审批状态。

### Skill

Skill 位于 apps/blueprint/skills/，按 base/、plan/、review/ 分组。
模型先看到元数据，需要时再读取完整 SKILL.md。Skill 的 Bash Tool 会直接在
当前环境执行，因此不要在宿主机运行不可信 Skill。

### 测试与 Eval

~~~bash
uv run pytest -q
npm --prefix apps/blueprint/tui run build
uv run python -m apps.blueprint.evals.single_phase
~~~

Eval 结果默认写入 eval-results/blueprint/single-phase/。可用 --provider 和 --model
临时覆盖模型。

### Docker

准备 apps/blueprint/.env 后运行：

~~~bash
docker compose run --rm geas
~~~

Compose 只读挂载 apps/blueprint/skills/，并使用命名卷保存 Session。

## Wellphone

Wellphone 的设计、数据流和安全边界见
[apps/wellphone/README.md](apps/wellphone/README.md)。

## 当前边界

- Runtime 目前只支持 OpenAI-compatible 文本模型；
- 图片输入及 Provider OAuth 尚未实现；
- Session 和 Wellphone 任务均为单机原型；
- Eval 是小型回归基线，不代表生产环境质量保证。
