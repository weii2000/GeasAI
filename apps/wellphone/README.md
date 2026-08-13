# Wellphone

Wellphone 是建立在 Geas Runtime 上的 iOS Capability Agent。它不模拟点击或接管
屏幕，而是让 Agent 调用 iOS 原生能力，在用户继续使用手机时处理后台数据任务。

当前实现覆盖照片管理、邮件起草和受控的外部服务跳转：模型负责理解意图和规划
步骤，Mac 调用 YouTube Data API 搜索公开视频；iPhone 通过 PhotoKit、Vision 和
MessageUI 执行本地能力；需要切换 App 的结果会保存为待处理动作，并在任务完成后通知用户。

## 架构

Wellphone 将“决策”和“执行”分离：

- **Mac Agent Server**：通过 FastAPI 接收任务，复用 geas.ai 和 geas.core 运行 Agent Loop；
- **Session Store**：按 `device → session → run` 隔离对话，并在 Mac 本地原子持久化可见消息；
- **Tool Broker**：把同步的 Agent Tool Call 转换为手机可轮询的任务，并等待结果；
- **Server Tool**：使用只保存在 Mac 的凭据调用 YouTube Data API；
- **iOS Executor**：校验工具作用域，调用原生 Kit 或构造受限的外部 App 链接；
- **Job Coordinator**：管理任务状态、取消和 iOS 后台执行生命周期；
- **Task Lifecycle**：区分运行、等待手机、完成、失败和取消；取消不是错误；
- **Observability**：以 JSON Lines 记录任务和工具生命周期、关联 ID、状态与耗时；
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
    participant K as PhotoKit / Vision

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

## 模块边界

| 模块 | 职责 |
| --- | --- |
| main.py | 加载配置并组装模型、Service 与 FastAPI |
| config.py | Wellphone 环境配置与启动参数默认值 |
| service.py | 任务状态、Agent 生命周期与取消 |
| session.py | 对话上下文、设备归属与 JSON 持久化 |
| agent.py | System Prompt、Tool Schema、YouTube 搜索与 Geas Agent 组装 |
| broker.py | Tool Call 排队、重投递、超时和结果匹配 |
| protocol.py | Mac 与 iOS 之间的 JSON 数据契约 |
| observability.py | 不含业务正文的结构化生命周期日志 |
| server.py | FastAPI 路由、请求验证和错误映射 |
| eval.py / eval_cases.json | 真实模型 Agent Eval、确定性评分与代表性案例集 |
| APIClient.swift | 创建任务、长轮询、回传结果和读取状态 |
| JobCoordinator.swift | 前后台任务协调、进度与取消 |
| ToolExecutor.swift | 工具路由、参数校验、任务级权限边界与待处理动作构造 |
| PhotoService.swift | PhotoKit 查询、相册和照片属性修改与 Vision OCR |
| ContentView.swift | 对话界面、待处理动作、操作审批和系统 Mail Composer |

## 数据与安全边界

- 原始照片留在 iPhone；模型只接收照片元数据和 OCR 文本；
- Server 日志不记录 Prompt、OCR、邮件正文或 Tool Result 内容；
- Session 文件只保存可见对话；原始 OCR 和 Tool Result 在每轮结束后清除；
- OCR 内容被视为不可信数据，不能作为 Agent 指令；
- 工具只能操作本次搜索返回的照片和本次任务创建或解析的相册；
- 删除、隐藏、改日期/位置和移出相册等高风险操作必须在手机端再次确认；
- 邮件工具只填充系统 Mail Composer，最终发送权始终属于用户；
- YouTube API Key 只保存在 Mac；Google Maps 与 YouTube 跳转只允许固定 HTTPS 域名；
- 邮件与外部 App 动作只在用户点击通知或卡片后打开，Agent 不能静默切换前台应用；
- 客户端生成任务 UUID，Tool Call 在结果确认前可重复获取，降低断网造成的重复执行；
- 每台设备生成独立 ID 并只能访问所属 Session；该 ID 用于原型隔离，不等同于公网认证；
- 后台执行依赖 iOS 调度，系统终止 App 后不保证继续运行。

## 当前边界

- YouTube 仅支持公开视频搜索；官方 API 无法读写“稍后观看”；
- Google Maps 当前只负责搜索和路线跳转，不在 Wellphone 内计算路线；
- Session 对话可在 Server 重启后恢复，运行中的任务和 Tool Call 不恢复；
- HTTP 通道没有认证，只适用于可信局域网原型；
- App Intents 尚未接入，当前入口仍是 Wellphone App；
- 照片是否语义匹配最终仍依赖模型判断。
