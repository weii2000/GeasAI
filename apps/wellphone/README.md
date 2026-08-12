# Wellphone

Wellphone 是建立在 Geas Runtime 上的 iOS Capability Agent。它不模拟点击或接管
屏幕，而是让 Agent 调用 iOS 原生能力，在用户继续使用手机时处理后台数据任务。

当前实现聚焦照片整理：模型负责理解意图和规划步骤，iPhone 负责通过 PhotoKit
查询与写入相册，并使用 Vision 在设备端完成 OCR。

## 架构

Wellphone 将“决策”和“执行”分离：

- **Mac Agent Server**：通过 FastAPI 接收任务，复用 geas.ai 和 geas.core 运行 Agent Loop；
- **Tool Broker**：把同步的 Agent Tool Call 转换为手机可轮询的任务，并等待结果；
- **iOS Executor**：校验工具作用域，调用 PhotoKit 和 Vision 后返回结构化结果；
- **Job Coordinator**：管理任务状态、取消和 iOS 后台执行生命周期；
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

    U->>I: 提交自然语言任务
    I->>S: 创建任务（客户端 UUID）
    S->>A: 启动 Agent Loop
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
| agent.py | System Prompt、Tool Schema 与 Geas Agent 组装 |
| broker.py | Tool Call 排队、重投递、超时和结果匹配 |
| protocol.py | Mac 与 iOS 之间的 JSON 数据契约 |
| server.py | FastAPI 路由、请求验证和错误映射 |
| APIClient.swift | 创建任务、长轮询、回传结果和读取状态 |
| JobCoordinator.swift | 前后台任务协调、进度与取消 |
| ToolExecutor.swift | 工具路由、参数校验与任务级权限边界 |
| PhotoService.swift | PhotoKit 查询/写入与 Vision OCR |

## 数据与安全边界

- 原始照片留在 iPhone；模型只接收照片元数据和 OCR 文本；
- OCR 内容被视为不可信数据，不能作为 Agent 指令；
- 工具只能操作本次搜索返回的照片和本次任务创建或解析的相册；
- 当前写操作只有“加入相册”，不删除照片，也不修改照片内容；
- 客户端生成任务 UUID，Tool Call 在结果确认前可重复获取，降低断网造成的重复执行；
- 后台执行依赖 iOS 调度，系统终止 App 后不保证继续运行。

## 当前边界

- 仅实现照片整理，还未接入音乐、日历或第三方服务；
- Server 状态保存在内存中，不支持重启恢复或多实例；
- HTTP 通道没有认证，只适用于可信局域网原型；
- App Intents 尚未接入，当前入口仍是 Wellphone App；
- 照片是否语义匹配最终仍依赖模型判断。
