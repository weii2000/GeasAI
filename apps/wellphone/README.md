# Wellphone

Wellphone 是建立在 Geas Runtime 上的 iOS Capability Agent。它不模拟点击或接管
屏幕，而是让 Agent 调用 iOS 原生能力，在用户继续使用手机时处理后台数据任务。

当前实现覆盖照片管理、位置、健康与训练、联系人、系统提醒、邮件起草和受控的外部服务跳转。
模型负责理解意图和规划步骤；Mac 执行 Server Tool 与 MCP Tool；iPhone 通过 Apple
原生 Framework 执行私有设备能力。需要切换 App 的结果会保存为待处理动作，并在任务
完成后通知用户。

## 架构

Wellphone 将“决策”和“执行”分离：

- **Mac Agent Server**：通过 FastAPI 接收任务，复用 geas.ai 和 geas.core 运行 Agent Loop；
- **Session Store**：按 `device → session → run` 隔离对话，并在 Mac 本地原子持久化可见消息；
- **Long-term Memory**：按设备保存可见 raw turn，通过 Gate 按需检索 facts/events，并每六轮批量提取；
- **Tool Broker**：把同步的 Agent Tool Call 转换为手机可轮询的任务，并等待结果；
- **Server Tool**：使用只保存在 Mac 的凭据调用 YouTube Data API；
- **MCP Tool**：启动时从受信任的 MCP Server 发现并挂载，在 Mac 侧直接执行；HTTP Server 可使用静态 Bearer 或 OAuth 2.1；
- **iOS Executor**：校验工具作用域，调用原生 Kit 或构造受限的外部 App 链接；
- **Job Coordinator**：管理任务状态、取消和 iOS 后台执行生命周期；
- **Task Lifecycle**：区分运行、等待手机、完成、失败和取消；取消不是错误；
- **Observability**：以脱敏 JSON Lines 记录模型、记忆、任务与工具生命周期；控制台和本地轮转文件共用同一事件格式；
- **Pending Action**：持久化邮件、YouTube 与地图结果，由本地通知或 App 内卡片交还用户；
- **Agent Eval**：使用固定 Tool 结果评估模型的工具选择、参数、安全边界和最终回答；
- **SwiftUI Client**：提供文字或语音输入、连接配置、进度与最终结果。

~~~mermaid
sequenceDiagram
    participant U as User
    participant I as iOS App
    participant S as Agent Server
    participant A as Geas Agent
    participant L as LLM
    participant T as iOS Tool Executor
    participant K as Native iOS Frameworks

    U->>I: 发送一条消息
    I->>S: device_id + session_id + run UUID
    S->>A: 在对应 Session 中启动 Agent Loop
    A->>L: Prompt + Tool Schema
    L-->>A: Tool Call
    A->>S: Tool Broker 等待手机
    I->>S: 长轮询下一个 Tool Call
    S-->>I: Tool Call
    I->>T: 校验并执行
    T->>K: 查询、OCR 或相册写入
    K-->>T: 本地结果
    T-->>S: Tool Result
    S-->>A: 恢复 Agent Loop
    A->>L: 根据结果继续决策
    L-->>I: 最终回答
~~~

## 能力矩阵

| 领域 | 原生能力 | Framework | 写入边界 |
| --- | --- | --- | --- |
| 照片 | 日期/属性搜索、OCR、相册与属性管理、删除 | PhotoKit / Vision | 任务级 ID Scope；高风险操作再次确认 |
| 位置 | 单次定位、正反向地址解析、附近地点搜索 | CoreLocation / MapKit | 不持续跟踪，不后台采集轨迹 |
| 健康 | 活动汇总、睡眠汇总、运动历史 | HealthKit | 只读；先在设备端聚合 |
| 训练 | 查看、创建、移除简单训练计划 | WorkoutKit | 写操作确认；使用稳定 ID 防止重复创建 |
| 联系人 | 按姓名查找邮箱 | Contacts | 只读取姓名与邮箱 |
| 提醒 | 创建普通或带到期时间的提醒 | EventKit | 手机确认后写入；任务/调用 ID 防止重投递重复创建 |
| 邮件草稿 | 收件人、抄送、HTML、照片附件 | MessageUI | 用户在系统 Mail 中最终发送 |
| 外部服务 | YouTube 搜索、地图与视频跳转 | Server API / Universal Link | 结果进入 Pending Action，用户决定何时打开 |
| MCP | Notion 搜索与页面读写 | Streamable HTTP MCP | Tool allowlist；写入需手机确认 |

## 模块边界

| 模块 | 职责 |
| --- | --- |
| main.py | 加载配置并组装模型、Service 与 FastAPI |
| config.py | Wellphone 环境配置与启动参数默认值 |
| service.py | 任务状态、Agent 生命周期与取消 |
| session.py | 对话上下文、设备归属与 JSON 持久化 |
| geas/memory | 通用的 SQLite/FTS5、检索 Gate 和 facts/events 提取 |
| geas/integrations/mcp | 通用 MCP Client、Tool 发现与 Agent Tool Adapter |
| agent.py | System Prompt、Tool Schema、YouTube 搜索与 Geas Agent 组装 |
| broker.py | Tool Call 排队、重投递、超时和结果匹配 |
| protocol.py | Mac 与 iOS 之间的 JSON 数据契约 |
| observability.py | 不含业务正文的结构化生命周期日志 |
| server.py | FastAPI 路由、请求验证和错误映射 |
| evals/agent.py / agent_cases.json | 真实模型 Agent Eval、确定性评分与代表性案例集 |
| APIClient.swift | 创建任务、长轮询、回传结果和读取状态 |
| JobCoordinator.swift | 前后台任务协调、进度与取消 |
| ToolExecutor.swift | 工具路由、参数校验、任务级权限边界与待处理动作构造 |
| PhotoService.swift | PhotoKit 查询、相册和照片属性修改与 Vision OCR |
| LocationService.swift | 单次定位、MapKit 地址解析与附近地点搜索 |
| HealthService.swift / WorkoutService.swift | HealthKit 只读聚合与 WorkoutKit 计划管理 |
| ContactService.swift / PermissionCenter.swift | 联系人邮箱查询与前台权限入口 |
| ReminderService.swift | EventKit 授权、提醒写入、保存核验与本地幂等映射 |
| ContentView.swift | 对话界面、待处理动作、操作审批和系统 Mail Composer |

## 数据与安全边界

- 原始照片留在 iPhone；模型只接收照片元数据和 OCR 文本；
- Server 日志不记录 Prompt、OCR、邮件正文或 Tool Result 内容；
- Session 文件只保存可见对话；原始 OCR 和 Tool Result 在每轮结束后清除；
- 长期记忆数据库按设备隔离，只记录可见对话，不保存原始 OCR 或 Tool Result；
- OCR 内容被视为不可信数据，不能作为 Agent 指令；
- 工具只能操作本次搜索返回的照片和本次任务创建或解析的相册；
- 删除、隐藏、改日期/位置和移出相册等高风险操作必须在手机端再次确认；
- 邮件工具只填充系统 Mail Composer，最终发送权始终属于用户；
- 健康数据只读且先在手机聚合；位置仅在明确任务中单次读取；
- 提醒只在手机确认后写入 EventKit；标题和备注不会进入 Mac Trace；
- YouTube API Key 只保存在 Mac；Google Maps 与 YouTube 跳转只允许固定 HTTPS 域名；
- 每个 MCP Server 必须显式配置允许挂载的原始 Tool 名；未知 Tool 会让 Server 启动失败；
- OAuth Token 按 Server URL 隔离保存在 Mac 的 `~/.geas/mcp/oauth`，不会进入 Session、日志或仓库；
- Notion 只挂载搜索、读取、创建和更新页面；创建或更新在实际调用前通过手机确认；
- 邮件与外部 App 动作只在用户点击通知或卡片后打开，Agent 不能静默切换前台应用；
- 客户端生成任务 UUID，Tool Call 在结果确认前可重复获取，降低断网造成的重复执行；
- 每台设备生成独立 ID 并只能访问所属 Session；该 ID 用于原型隔离，不等同于公网认证；
- 后台执行依赖 iOS 调度，系统终止 App 后不保证继续运行。

## 当前边界

- YouTube 仅支持公开视频搜索；官方 API 无法读写“稍后观看”；
- Google Maps 当前只负责搜索和路线跳转，不在 Wellphone 内计算路线；
- Session 对话可在 Server 重启后恢复，运行中的任务和 Tool Call 不恢复；
- 终态 TaskRecord 与 Broker Channel 当前保留至 Server 重启，尚未实现 TTL 回收；
- HTTP 通道没有认证，只适用于可信局域网原型；
- App Intents 尚未接入，当前入口仍是 Wellphone App；
- MCP Tool Catalog 在 Server 启动时固定，远端工具变化后需要重启刷新；
- HealthKit 无法向 App 区分“无数据”和“用户拒绝读取”；回答必须保留这一隐私语义；
- WorkoutKit 计划需要受支持且已配对 Apple Watch；没有手表时会返回能力不可用；
- 原生 Mail 不提供收件箱读取 API；当前未接入邮箱读取，后续可通过只读 MCP Server 扩展；
- Reminder 第一版只创建，不查询、编辑、完成、删除或创建重复/位置提醒；EventKit 写入与本地幂等映射之间仍有无法原子提交的崩溃窗口；
- 照片是否语义匹配最终仍依赖模型判断。

## Trace 与 Eval

Server 在控制台和 `~/.geas/wellphone/logs/wellphone.jsonl` 输出相同的脱敏
Trace。本地文件上限 5 MB，保留两个备份；使用 `task_id` 串联模型调用、Tool、
Memory 与任务终态。Trace 不记录 Prompt、回答、Tool 参数或结果，以及照片、健康、
位置、邮件和 MCP 内容。

Agent Eval Suite `0.3` 包含 23 个代表性案例，覆盖工具选择、精确参数、写入边界、
Prompt Injection 和虚假完成声明：

```bash
uv run python -m apps.wellphone.evals.agent
```

## 外部服务 OAuth

OAuth 首次授权在 Mac 完成，Wellphone Server 启动期间不会弹出登录页面。先在
`.env` 配置对应 MCP Server，再运行一次登录命令。

Notion 使用通用 MCP OAuth：

```bash
uv run python -m apps.wellphone.mcp_login notion
```
