import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import readline from "node:readline";
import chalk from "chalk";
import {
  CombinedAutocompleteProvider,
  Container,
  CURSOR_MARKER,
  Editor,
  Input,
  Markdown,
  ProcessTerminal,
  SelectList,
  Spacer,
  Text,
  TUI,
  matchesKey,
  truncateToWidth,
  visibleWidth,
  type Component,
  type Focusable,
  type MarkdownTheme,
  type SelectItem,
  type SelectListTheme,
} from "@earendil-works/pi-tui";

type Phase = "PLAN" | "REVIEW" | "PENDING_APPROVAL" | "IDLE";
type AgentPhase = "PLAN" | "REVIEW";

type ModelRef = {
  provider: string;
  model: string;
};

type ModelItem = {
  provider: string;
  id: string;
  name: string;
};

type ConversationMessage = {
  role: "user" | "assistant";
  content: string;
  phase: Phase;
};

type PlanTask = {
  title: string;
  status: "pending" | "in_progress" | "completed";
  acceptance_criteria: string | null;
  start_time: string | null;
  due_time: string | null;
  subtasks: PlanTask[];
};

type PlanData = {
  title: string;
  goal: string;
  description: string;
  acceptance_criterion: string;
  constraints: string[];
  tasks: PlanTask[];
};

type SessionState = {
  session_id: string;
  cwd: string;
  phase: Phase | null;
  conversation: ConversationMessage[];
  plan: PlanData | null;
  plan_model: ModelRef | null;
  review_model: ModelRef | null;
  usage: { tokens: number; cost: number };
};

type SavedSession = {
  id: string;
  updated_at: string;
};

type InitialState = {
  state: SessionState;
  models: ModelItem[];
  providers: string[];
};

type RPCEvent =
  | { event: "assistant_start"; phase: AgentPhase }
  | { event: "text_delta"; phase: AgentPhase; delta: string }
  | {
      event: "plan_published";
      plan_id: number;
      plan_title: string;
      created_task_count: number;
    }
  | {
      event: "tool_start";
      phase: AgentPhase;
      tool_call_id: string;
      name: string;
      args: Record<string, unknown>;
    }
  | {
      event: "tool_end";
      phase: AgentPhase;
      tool_call_id: string;
      name: string;
      is_error: boolean;
      content: string;
    };

const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../..",
);

const selectTheme: SelectListTheme = {
  selectedPrefix: (text) => chalk.cyan(text),
  selectedText: (text) => chalk.cyan.bold(text),
  description: (text) => chalk.gray(text),
  scrollInfo: (text) => chalk.gray(text),
  noMatch: (text) => chalk.red(text),
};

const markdownTheme: MarkdownTheme = {
  heading: (text) => chalk.cyan.bold(text),
  link: (text) => chalk.cyan(text),
  linkUrl: (text) => chalk.gray(text),
  code: (text) => chalk.yellow(text),
  codeBlock: (text) => text,
  codeBlockBorder: (text) => chalk.gray(text),
  quote: (text) => chalk.gray(text),
  quoteBorder: (text) => chalk.gray(text),
  hr: (text) => chalk.gray(text),
  listBullet: (text) => chalk.cyan(text),
  bold: (text) => chalk.bold(text),
  italic: (text) => chalk.italic(text),
  strikethrough: (text) => chalk.strikethrough(text),
  underline: (text) => chalk.underline(text),
};

class RPCClient {
  onEvent?: (event: RPCEvent) => void;
  private child: ChildProcessWithoutNullStreams;
  private nextId = 1;
  private pending = new Map<
    number,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >();
  private stderr = "";

  constructor() {
    const python =
      process.env.GEAS_PYTHON ?? path.join(projectRoot, ".venv/bin/python");
    this.child = spawn(python, ["-m", "apps.blueprint.rpc"], {
      cwd: projectRoot,
      stdio: ["pipe", "pipe", "pipe"],
    });
    readline.createInterface({ input: this.child.stdout }).on("line", (line) =>
      this.handleLine(line),
    );
    this.child.stderr.on("data", (data: Buffer) => {
      this.stderr = (this.stderr + data.toString()).slice(-4000);
    });
    this.child.on("exit", (code) => {
      const error = new Error(
        this.stderr.trim() || `Python RPC exited with code ${code}`,
      );
      for (const request of this.pending.values()) request.reject(error);
      this.pending.clear();
    });
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const id = this.nextId++;
    this.child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
      });
    });
  }

  kill(): void {
    this.child.kill();
  }

  private handleLine(line: string): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(line) as Record<string, unknown>;
    } catch {
      return;
    }
    if (message.type === "event") {
      this.onEvent?.(message as unknown as RPCEvent);
      return;
    }
    const id = message.id;
    if (typeof id !== "number") return;
    const request = this.pending.get(id);
    if (!request) return;
    this.pending.delete(id);
    if (message.ok) request.resolve(message.result);
    else request.reject(new Error(String(message.error ?? "RPC error")));
  }
}

class Dialog implements Component, Focusable {
  private _focused = false;

  constructor(
    private title: string,
    private child: Component,
    private hint: string,
  ) {}

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
    if ("focused" in this.child) {
      (this.child as Component & Focusable).focused = value;
    }
  }

  handleInput(data: string): void {
    this.child.handleInput?.(data);
  }

  invalidate(): void {
    this.child.invalidate();
  }

  render(width: number): string[] {
    const innerWidth = Math.max(1, width - 4);
    const top = `╭─ ${this.title} `;
    const lines = [
      chalk.cyan(top + "─".repeat(Math.max(0, width - top.length - 1)) + "╮"),
      ...this.child.render(innerWidth).map((line) => frame(line, width)),
      frame(chalk.gray(this.hint), width),
      chalk.cyan("╰" + "─".repeat(Math.max(0, width - 2)) + "╯"),
    ];
    return lines;
  }
}

class SecretInput implements Component, Focusable {
  private input = new Input();

  constructor(
    private prefix: string,
    onSubmit: (value: string) => void,
    onEscape: () => void,
  ) {
    this.input.onSubmit = onSubmit;
    this.input.onEscape = onEscape;
  }

  get focused(): boolean {
    return this.input.focused;
  }

  set focused(value: boolean) {
    this.input.focused = value;
  }

  handleInput(data: string): void {
    this.input.handleInput(data);
  }

  invalidate(): void {
    this.input.invalidate();
  }

  render(width: number): string[] {
    const available = Math.max(0, width - this.prefix.length);
    const hidden = "•".repeat(
      Math.min(Array.from(this.input.getValue()).length, available),
    );
    return [
      truncateToWidth(
        this.prefix + hidden + (this.focused ? CURSOR_MARKER : ""),
        width,
      ),
    ];
  }
}

class BlueprintTUI {
  private terminal = new ProcessTerminal();
  private tui = new TUI(this.terminal, true);
  private header = new Text("", 1, 0);
  private chat = new Container();
  private status = new Text("", 1, 0);
  private editor = new Editor(
    this.tui,
    { borderColor: (text) => chalk.cyan(text), selectList: selectTheme },
    { paddingX: 1, autocompleteMaxVisible: 6 },
  );
  private footer = new Text("", 1, 0);
  private state: SessionState;
  private streaming: {
    block: Markdown;
    text: string;
  } | null = null;
  private tools = new Map<string, Text>();
  private busy = false;

  constructor(
    private rpc: RPCClient,
    private initial: InitialState,
  ) {
    this.state = initial.state;
    this.editor.setAutocompleteProvider(
      new CombinedAutocompleteProvider(
        [
          { name: "new", description: "开始新会话" },
          { name: "resume", description: "恢复历史会话" },
          { name: "model", description: "选择 PLAN / REVIEW 模型" },
          { name: "login", description: "保存 Provider API Key" },
          { name: "quit", description: "退出 Blueprint" },
        ],
        projectRoot,
      ),
    );
    this.editor.onSubmit = (text) => void this.handleInput(text);
    this.rpc.onEvent = (event) => this.handleEvent(event);

    this.tui.addChild(this.header);
    this.tui.addChild(new Spacer(1));
    this.tui.addChild(this.chat);
    this.tui.addChild(this.status);
    this.tui.addChild(this.editor);
    this.tui.addChild(this.footer);
    this.tui.setFocus(this.editor);
    this.tui.addInputListener((data) => {
      if (matchesKey(data, "escape") && this.tui.hasOverlay()) {
        this.tui.hideOverlay();
        return { consume: true };
      }
      if (matchesKey(data, "ctrl+c")) {
        if (this.tui.hasOverlay()) this.tui.hideOverlay();
        else void this.quit();
        return { consume: true };
      }
      return undefined;
    });
    this.rebuildConversation();
    this.setStatus(
      this.state.phase === null
        ? "请先用 /model 配置 PLAN 和 REVIEW 模型"
        : `${this.state.phase} · Ready`,
    );
  }

  start(): void {
    this.tui.start();
  }

  private async handleInput(text: string): Promise<void> {
    const value = text.trim();
    if (!value) return;
    if (value === "/quit") return this.quit();
    if (this.busy) return this.setStatus("当前请求仍在运行");
    if (value === "/new") return this.newSession();
    if (value === "/resume") return this.showSessions();
    if (value === "/model") return this.showModels();
    if (value === "/login") return this.showLogin();
    if (value.startsWith("/")) {
      this.setStatus(`未知命令：${value}`);
      return;
    }
    if (this.state.phase === null) {
      this.setStatus("请先用 /model 配置两个 Agent 的模型");
      return;
    }

    this.addUserMessage(value);
    this.streaming = null;
    this.setBusy(true);
    try {
      this.state = await this.rpc.request<SessionState>("prompt", {
        text: value,
      });
      if (this.state.phase === "PENDING_APPROVAL") {
        this.addApprovalRequest();
        this.setStatus("等待确认 · 输入 y 批准，或输入修改意见");
      } else {
        this.setStatus(`${this.state.phase} · Ready`);
      }
    } catch (error) {
      this.addError(error);
    } finally {
      this.setBusy(false);
      this.renderChrome();
    }
  }

  private handleEvent(event: RPCEvent): void {
    if (event.event === "assistant_start") {
      this.streaming = null;
      this.setStatus(`${event.phase} · Thinking…`);
    } else if (event.event === "text_delta") {
      if (!this.streaming) {
        this.chat.addChild(
          new Text(chalk.cyan.bold(`Blueprint · ${event.phase}`), 1, 1),
        );
        const block = new Markdown("", 1, 0, markdownTheme);
        this.chat.addChild(block);
        this.streaming = { block, text: "" };
      }
      this.streaming.text += event.delta;
      this.streaming.block.setText(this.streaming.text);
    } else if (event.event === "plan_published") {
      this.streaming = null;
      this.chat.addChild(
        new Text(
          chalk.green(
            `✓ 已保存到 PlanWise\n` +
              `${event.plan_title} · Plan #${event.plan_id} · ` +
              `${event.created_task_count} tasks`,
          ),
          1,
          1,
        ),
      );
    } else if (event.event === "tool_start") {
      this.streaming = null;
      const args = JSON.stringify(event.args);
      const block = new Text(
        chalk.gray(`↳ ${event.name} ${args}\n  Running…`),
        1,
        1,
      );
      this.tools.set(event.tool_call_id, block);
      this.chat.addChild(block);
      this.setStatus(`${event.phase} · ${event.name}`);
    } else {
      const block = this.tools.get(event.tool_call_id);
      if (block) {
        const marker = event.is_error ? chalk.red("✗") : chalk.green("✓");
        block.setText(
          `↳ ${event.name} ${marker}` +
            (event.content ? `\n  ${event.content}` : ""),
        );
        this.tools.delete(event.tool_call_id);
      }
    }
    this.tui.requestRender();
  }

  private async newSession(): Promise<void> {
    try {
      this.state = await this.rpc.request<SessionState>("new_session");
      this.rebuildConversation();
      this.setStatus(
        this.state.phase === null
          ? "新会话 · 请先配置模型"
          : `${this.state.phase} · New session`,
      );
    } catch (error) {
      this.addError(error);
    }
  }

  private async showSessions(): Promise<void> {
    try {
      const sessions =
        await this.rpc.request<SavedSession[]>("list_sessions");
      if (!sessions.length) {
        this.setStatus("当前项目还没有已保存的 Session");
        return;
      }
      const list = new SelectList(
        sessions.map((session) => ({
          value: session.id,
          label: session.id.slice(0, 8),
          description: formatDate(session.updated_at),
        })),
        10,
        selectTheme,
      );
      list.onSelect = (item) => {
        this.tui.hideOverlay();
        void this.resumeSession(item.value);
      };
      list.onCancel = () => this.tui.hideOverlay();
      this.showDialog("Resume session", list, "↑↓ select · Enter open · Esc");
    } catch (error) {
      this.addError(error);
    }
  }

  private async resumeSession(sessionId: string): Promise<void> {
    try {
      this.state = await this.rpc.request<SessionState>("resume_session", {
        session_id: sessionId,
      });
      this.rebuildConversation();
      this.setStatus(`Resumed · ${sessionId.slice(0, 8)}`);
    } catch (error) {
      this.addError(error);
    }
  }

  private showModels(): void {
    const items: SelectItem[] = [];
    for (const phase of ["PLAN", "REVIEW"] as const) {
      for (const model of this.initial.models) {
        items.push({
          value: JSON.stringify([phase, model.provider, model.id]),
          label: `${phase.padEnd(6)} ${model.provider}/${model.id}`,
          description: model.name,
        });
      }
    }
    const list = new SelectList(items, 12, selectTheme);
    list.onSelect = (item) => {
      this.tui.hideOverlay();
      const [phase, provider, model] = JSON.parse(item.value) as [
        AgentPhase,
        string,
        string,
      ];
      void this.setModel(phase, provider, model);
    };
    list.onCancel = () => this.tui.hideOverlay();
    this.showDialog("Select model", list, "↑↓ select · Enter apply · Esc");
  }

  private async setModel(
    phase: AgentPhase,
    provider: string,
    model: string,
  ): Promise<void> {
    try {
      this.state = await this.rpc.request<SessionState>("set_model", {
        phase,
        provider,
        model,
      });
      this.setStatus(`${phase} · ${provider}/${model}`);
      this.renderChrome();
    } catch (error) {
      this.addError(error);
    }
  }

  private showLogin(): void {
    const list = new SelectList(
      [
        {
          value: "@planwise",
          label: "PlanWise",
          description: "账号登录并连接 MCP",
        },
        ...this.initial.providers.map((provider) => ({
          value: provider,
          label: provider,
          description: "Provider API Key",
        })),
      ],
      8,
      selectTheme,
    );
    list.onSelect = (item) => {
      this.tui.hideOverlay();
      if (item.value === "@planwise") this.showPlanwiseUsername();
      else this.showApiKeyInput(item.value);
    };
    list.onCancel = () => this.tui.hideOverlay();
    this.showDialog("Provider", list, "↑↓ select · Enter continue · Esc");
  }

  private showApiKeyInput(provider: string): void {
    const input = new SecretInput(
      "API Key › ",
      (value) => {
        if (!value.trim()) {
          this.setStatus("API Key 不能为空");
          return;
        }
        this.tui.hideOverlay();
        void this.saveApiKey(provider, value.trim());
      },
      () => this.tui.hideOverlay(),
    );
    this.showDialog(
      `${provider} API Key`,
      input,
      "内容不会显示 · Enter save · Esc",
    );
  }

  private showPlanwiseUsername(): void {
    const input = new Input();
    input.onSubmit = (value) => {
      const username = value.trim();
      if (!username) return this.setStatus("PlanWise 用户名不能为空");
      this.tui.hideOverlay();
      this.showPlanwisePassword(username);
    };
    input.onEscape = () => this.tui.hideOverlay();
    this.showDialog(
      "PlanWise Username",
      input,
      "Enter continue · Esc",
    );
  }

  private showPlanwisePassword(username: string): void {
    const input = new SecretInput(
      "Password › ",
      (value) => {
        if (!value) return this.setStatus("PlanWise 密码不能为空");
        this.tui.hideOverlay();
        void this.loginPlanwise(username, value);
      },
      () => this.tui.hideOverlay(),
    );
    this.showDialog(
      "PlanWise Password",
      input,
      "密码不会保存 · Enter login · Esc",
    );
  }

  private async loginPlanwise(
    username: string,
    password: string,
  ): Promise<void> {
    this.setStatus("PlanWise · Logging in…");
    try {
      await this.rpc.request("login_planwise", { username, password });
      this.setStatus("PlanWise · MCP 已连接");
    } catch (error) {
      this.addError(error);
    }
  }

  private async saveApiKey(provider: string, apiKey: string): Promise<void> {
    try {
      await this.rpc.request("set_api_key", {
        provider,
        api_key: apiKey,
      });
      this.setStatus(`${provider} API Key 已保存到 .env`);
    } catch (error) {
      this.addError(error);
    }
  }

  private showDialog(
    title: string,
    child: Component,
    hint: string,
  ): void {
    this.tui.showOverlay(new Dialog(title, child, hint), {
      width: "70%",
      maxHeight: "70%",
      anchor: "center",
      margin: 1,
    });
  }

  private rebuildConversation(): void {
    this.chat.clear();
    this.streaming = null;
    this.tools.clear();
    if (!this.state.conversation.length) {
      this.chat.addChild(
        new Text(
          "告诉我你的目标，我会先制定计划，再独立评审。",
          1,
          1,
        ),
      );
    }
    for (const message of this.state.conversation) {
      if (message.role === "user") this.addUserMessage(message.content);
      else this.addAssistantMessage(message);
    }
    if (this.state.phase === "PENDING_APPROVAL") this.addApprovalRequest();
    this.renderChrome();
  }

  private addApprovalRequest(): void {
    if (!this.state.plan) return;
    this.chat.addChild(
      new Text(chalk.yellow.bold("等待你的最终确认"), 1, 1),
    );
    this.chat.addChild(
      new Markdown(
        formatPlan(this.state.plan) +
          "\n\n---\n\n输入 `y` 确认并发布；输入其他内容作为评审意见。",
        1,
        0,
        markdownTheme,
      ),
    );
  }

  private addUserMessage(text: string): void {
    this.chat.addChild(
      new Text(`${chalk.green.bold("You")}\n${text}`, 1, 1),
    );
    this.tui.requestRender();
  }

  private addAssistantMessage(message: ConversationMessage): void {
    this.chat.addChild(
      new Text(chalk.cyan.bold(`Blueprint · ${message.phase}`), 1, 1),
    );
    this.chat.addChild(
      new Markdown(message.content, 1, 0, markdownTheme),
    );
  }

  private addError(error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    this.chat.addChild(new Text(chalk.red(`Error\n${message}`), 1, 1));
    this.setStatus(`Error · ${message}`);
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    this.editor.disableSubmit = busy;
    if (busy) this.setStatus(`${this.state.phase} · Working…`);
  }

  private setStatus(text: string): void {
    this.status.setText(chalk.yellow(text));
    this.tui.requestRender();
  }

  private renderChrome(): void {
    const plan = formatModel(this.state.plan_model);
    const review = formatModel(this.state.review_model);
    this.header.setText(
      chalk.bold("GEAS") +
        `  ${chalk.cyan("PLAN")} ${plan}` +
        `  ·  ${chalk.magenta("REVIEW")} ${review}`,
    );
    this.footer.setText(
      `${path.basename(this.state.cwd)}  ·  session ${this.state.session_id.slice(0, 8)}` +
        `  ·  ${this.state.usage.tokens} tok` +
        `  ·  $${this.state.usage.cost.toFixed(4)}` +
        "  ·  /model /resume /new",
    );
    this.tui.requestRender();
  }

  private async quit(): Promise<void> {
    try {
      await this.rpc.request("shutdown");
    } catch {
      this.rpc.kill();
    } finally {
      this.tui.stop();
      process.exit(0);
    }
  }
}

function frame(text: string, width: number): string {
  const content = truncateToWidth(text, Math.max(1, width - 4));
  return `│ ${content}${" ".repeat(Math.max(0, width - 4 - visibleWidth(content)))} │`;
}

function formatModel(model: ModelRef | null): string {
  return model ? `${model.provider}/${model.model}` : "not configured";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatPlan(plan: PlanData): string {
  const lines = [
    `# ${plan.title || "未命名计划"}`,
    "",
    `**目标**　${plan.goal || "—"}`,
    "",
    `**说明**　${plan.description || "—"}`,
    "",
    `**验收标准**　${plan.acceptance_criterion || "—"}`,
    "",
    "## 约束",
    ...(plan.constraints.length
      ? plan.constraints.map((constraint) => `- ${constraint}`)
      : ["- 无"]),
    "",
    "## 任务",
  ];

  if (!plan.tasks.length) lines.push("- 暂无任务");
  else for (const task of plan.tasks) appendTask(lines, task, 0);
  return lines.join("\n");
}

function appendTask(lines: string[], task: PlanTask, depth: number): void {
  const indent = "  ".repeat(depth);
  const status = {
    pending: "○ 待开始",
    in_progress: "◐ 进行中",
    completed: "✓ 已完成",
  }[task.status];
  lines.push(`${indent}- **${status}｜${task.title}**`);
  if (task.acceptance_criteria) {
    lines.push(`${indent}  - 验收：${task.acceptance_criteria}`);
  }
  if (task.start_time || task.due_time) {
    const start = task.start_time ? formatDate(task.start_time) : "未指定";
    const due = task.due_time ? formatDate(task.due_time) : "未指定";
    lines.push(`${indent}  - 时间：${start} → ${due}`);
  }
  for (const subtask of task.subtasks) appendTask(lines, subtask, depth + 1);
}

async function main(): Promise<void> {
  const rpc = new RPCClient();
  try {
    const initial = await rpc.request<InitialState>("initialize");
    new BlueprintTUI(rpc, initial).start();
  } catch (error) {
    rpc.kill();
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}

void main();
