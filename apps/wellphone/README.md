# Wellphone

Wellphone 是基于 [Geas Runtime](../../) 实现的 iOS Capability Agent。用户正常使用手机时，Agent 在后台由 iOS 原生 API 或 Server 端 API/MCP 完成任务；它不模拟触摸，不占用屏幕、焦点或键盘。

例如，Wellphone 可以在不打扰用户的情况下，把本月的美食照片整理到独立相册，同时梳理一份总结整理到 Notion；又或者搜索附近餐厅，起草一封晚餐邀请邮件由用户确认后发送，同时创建一个 Reminder。

## 架构

~~~mermaid
flowchart LR
    subgraph Backend[Agent Server]
        API[FastAPI] --> S[Wellphone Service] --> A[Geas Agent Loop]
        A <--> L[LLM]
        B[Tool Broker]
        M[Server / MCP Tools]
        D[Session / Memory / Trace]
        A <--> B
        A <--> M
        S <--> D
    end
    subgraph Phone[iPhone]
        J[SwiftUI / Job Coordinator] --> E[Tool Executor]
        E --> K[Native iOS Frameworks]
        E --> P[Pending Action / Notification]
    end
    J <-->|Task API + long polling| API
    API <--> B
~~~

Agent Server 负责模型推理、Agent Loop、Session、长期记忆和外部服务；iPhone 负责私有数据与原生能力。iPhone 提交任务并长轮询 Tool Call；Server Tool 直接执行，手机 Tool 经 Broker 交给 Tool Executor，结果返回 Agent Loop 继续规划。邮件和外部 App 跳转会保存为 Pending Action，通知后由用户接管。

## 能力

| 领域 | 已实现能力 | 执行边界 |
| --- | --- | --- |
| 照片 | 日期/属性搜索、OCR、相册整理、收藏/隐藏/时间/位置修改、删除 | PhotoKit / Vision；任务级 ID Scope；高风险写入确认 |
| 位置与健康 | 单次定位、地址解析、附近搜索、活动/睡眠/训练汇总 | CoreLocation / MapKit / HealthKit；后台定位需用户授予 Always，不持续定位，健康只读 |
| 训练与提醒 | 查看/创建/移除 WorkoutKit 计划，创建系统提醒 | 写入前确认；稳定 ID 防止重复创建 |
| 联系人与邮件 | 查找联系人邮箱，生成 HTML/照片附件邮件草稿 | MessageUI；用户在系统 Mail 中最终发送，不能读取收件箱 |
| 外部服务 | YouTube 搜索、Google Maps 与 YouTube 跳转 | 凭据留在 Agent Server；跳转进入 Pending Action |
| MCP | 启动时发现受信任 Server 的 Tools；可选接入 Notion 搜索、读取和页面写入 | Tool allowlist；OAuth；远端写入前手机确认 |

## 关键设计选择

| 问题 | 选择 | 代价 |
| --- | --- | --- |
| 同步 Agent 如何等待异步手机 | Tool Broker + 长轮询 | 依赖网络连接和 iOS 后台调度 |
| 如何处理断网重投递 | 结果确认前重复返回 Tool Call；任务/调用 ID 驱动幂等 | 跨存储写入仍存在崩溃窗口 |
| 如何控制敏感操作 | Tool Scope + 手机确认；外部跳转保存为 Pending Action | 无人确认时超时，不自动执行 |
| 如何扩展外部能力 | MCP 启动时发现、固定挂载，远端 Schema 转 Agent Tool | Catalog 变化后需要重启 |
| 如何管理长期记忆 | 按设备保存可见对话，批量提取 facts/events，Gate 按需检索 | 增加模型调用成本；错误记忆可能影响后续上下文 |

Session 按 `device → session` 隔离并只保存可见对话。长期记忆、OCR、Tool Result 和 MCP 内容均被标记为不可信数据，并通过 Prompt 与 Eval 限制其影响。取消、超时、权限拒绝和 Tool Error 保留原始失败语义，不会被伪装成成功结果。

## Trace 与 Eval

脱敏 JSONL Trace 使用 `task_id` 串联模型、Tool、Memory 与任务终态，不记录业务正文；本地文件位于 `~/.geas/wellphone/logs/wellphone.jsonl`。Eval Suite `0.3` 包含 23 个案例，覆盖 Tool 选择、参数、Prompt Injection、写入边界和虚假完成声明，运行命令为 `uv run python -m apps.wellphone.evals.agent`。

## 部署

Agent Server 只要求 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)，可以运行在任何能被 iPhone 访问的主机上。iOS Client 的构建需要 macOS、Xcode 26 和一台 iOS 26 真机；最简单的演示部署是在同一台 Mac 启动 Server，并让手机连接同一局域网。

```bash
uv sync
cp apps/wellphone/.env.example apps/wellphone/.env
# 编辑 .env，至少填写所选 Provider 的 API Key
uv run python -m apps.wellphone.main
```

随后用 Xcode 打开 `apps/wellphone/ios/Geas Wellphone.xcodeproj`，选择开发团队和真机运行。在 App 设置页填写 Agent Server 地址；本地演示可使用 `http://<Mac 局域网 IP>:8000`。再按任务需要授权通知、照片、位置、健康、联系人或提醒事项权限。

核心环境变量：

| 变量 | 用途 | 必要性 |
| --- | --- | --- |
| `WELLPHONE_PROVIDER` / `WELLPHONE_MODEL` + Provider API Key | Agent 模型与认证 | 必需，模型名有默认值 |
| `YOUTUBE_API_KEY` | YouTube 搜索 | 否 |
| `WELLPHONE_HOST` / `WELLPHONE_PORT` / `WELLPHONE_TOOL_TIMEOUT` | Server 与超时设置 | 否 |
| `WELLPHONE_MEMORY_PROVIDER` / `WELLPHONE_MEMORY_MODEL` | 单独指定 Memory 模型 | 否 |
| `WELLPHONE_MCP_<NAME>_URL/AUTH/TOOLS/APPROVAL_TOOLS` | MCP 地址、认证方式、Tool allowlist 和写操作审批 | 否 |

完整配置示例见 [`.env.example`](.env.example)。OAuth MCP 首次使用前在 Agent Server 所在主机执行 `uv run python -m apps.wellphone.mcp_login <server>`。

## 当前边界

- HTTP 通道没有用户认证，只适用于可信局域网原型；`device_id` 是隔离键，不是身份认证；
- Session 可在 Server 重启后恢复，运行中的 Task 与 Tool Call 不恢复；终态任务暂未做 TTL；
- iOS 后台执行由系统调度，App 被终止后不保证任务继续；
- 原生 Mail 只能生成草稿，不能读取收件箱或自动发送；
- YouTube 不能写入“稍后观看”；地图和视频只准备用户可点击的跳转；
- MCP Catalog 在启动时固定，当前没有热更新。
