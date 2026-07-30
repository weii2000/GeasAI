import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from geas.ai.models import Models
from geas.ai.types import AssistantMessage, Message
from geas.core.agent import Agent
from geas.core.types import AgentState, AgentTool

from .profiles import load_skill_profiles
from .session import PlanSession
from .types import ConversationMessage, Phase, Plan, ReviewReport


SESSION_VERSION = 1


@dataclass
class AgentSnapshot:
    provider: str
    model: str
    messages: list[Message]


@dataclass
class SessionSnapshot:
    version: Literal[1]
    session_id: str
    cwd: str
    created_at: str
    updated_at: str
    phase: Phase
    conversation: list[ConversationMessage]
    plan: Plan
    review_report: ReviewReport | None
    plan_agent: AgentSnapshot
    review_agent: AgentSnapshot


_SNAPSHOT = TypeAdapter(SessionSnapshot)


@dataclass(frozen=True)
class SessionManager:
    session_id: str
    cwd: Path
    session_file: Path
    created_at: str

    @classmethod
    def create(
        cls,
        cwd: Path | None = None,
        root: Path | None = None,
    ) -> "SessionManager":
        resolved_cwd = (cwd or Path.cwd()).resolve()
        session_id = uuid4().hex
        return cls(
            session_id=session_id,
            cwd=resolved_cwd,
            session_file=(
                _session_directory(resolved_cwd, root)
                / f"{session_id}.json"
            ),
            created_at=_now(),
        )

    @classmethod
    def open(
        cls,
        session_id: str,
        cwd: Path | None = None,
        root: Path | None = None,
    ) -> "SessionManager":
        _validate_session_id(session_id)
        resolved_cwd = (cwd or Path.cwd()).resolve()
        session_file = (
            _session_directory(resolved_cwd, root)
            / f"{session_id}.json"
        )
        snapshot = _read_snapshot(session_file)
        _validate_snapshot(snapshot, session_id, resolved_cwd)
        return cls(
            session_id=session_id,
            cwd=resolved_cwd,
            session_file=session_file,
            created_at=snapshot.created_at,
        )

    @classmethod
    def continue_recent(
        cls,
        cwd: Path | None = None,
        root: Path | None = None,
    ) -> "SessionManager | None":
        resolved_cwd = (cwd or Path.cwd()).resolve()
        directory = _session_directory(resolved_cwd, root)
        files = list(directory.glob("*.json"))
        if not files:
            return None

        latest = max(files, key=lambda path: path.stat().st_mtime)
        return cls.open(latest.stem, resolved_cwd, root)

    @classmethod
    def list_saved(
        cls,
        cwd: Path | None = None,
        root: Path | None = None,
    ) -> list["SessionManager"]:
        resolved_cwd = (cwd or Path.cwd()).resolve()
        files = sorted(
            _session_directory(resolved_cwd, root).glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        sessions: list[SessionManager] = []
        for path in files:
            try:
                sessions.append(cls.open(path.stem, resolved_cwd, root))
            except ValueError:
                continue
        return sessions

    def save(self, session: PlanSession) -> None:
        snapshot = SessionSnapshot(
            version=SESSION_VERSION,
            session_id=self.session_id,
            cwd=str(self.cwd),
            created_at=self.created_at,
            updated_at=_now(),
            phase=session.phase,
            conversation=[*session.conversation],
            plan=session.plan,
            review_report=session.review_report,
            plan_agent=_agent_snapshot(session.plan_agent),
            review_agent=_agent_snapshot(session.review_agent),
        )
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = self.session_file.with_suffix(".tmp")
        temporary_file.write_bytes(
            _SNAPSHOT.dump_json(snapshot, indent=2)
        )
        # ponytail: one writer per session; add file locking if concurrent
        # editing is ever supported.
        temporary_file.replace(self.session_file)

    def load(
        self,
        models: Models,
        skills_root: Path | None = None,
        extra_tools: list[AgentTool] | None = None,
    ) -> PlanSession:
        snapshot = _read_snapshot(self.session_file)
        _validate_snapshot(snapshot, self.session_id, self.cwd)
        profile_args = (
            load_skill_profiles(skills_root)
            if skills_root is not None
            else ()
        )
        session = PlanSession(
            _restore_agent(snapshot.plan_agent, models),
            _restore_agent(snapshot.review_agent, models),
            *profile_args,
            extra_tools=extra_tools,
        )
        session.phase = snapshot.phase
        session.conversation = [*snapshot.conversation]
        session.plan = snapshot.plan
        session.review_report = snapshot.review_report
        return session


def _session_directory(cwd: Path, root: Path | None) -> Path:
    sessions_root = root or Path.home() / ".geas" / "sessions"
    digest = hashlib.sha256(str(cwd).encode()).hexdigest()[:8]
    return sessions_root / f"{cwd.name}-{digest}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_session_id(session_id: str) -> None:
    if (
        len(session_id) != 32
        or any(character not in "0123456789abcdef" for character in session_id)
    ):
        raise ValueError("Invalid session ID")


def _read_snapshot(path: Path) -> SessionSnapshot:
    try:
        return _SNAPSHOT.validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise ValueError(f"Cannot read session: {path}") from error


def _validate_snapshot(
    snapshot: SessionSnapshot,
    session_id: str,
    cwd: Path,
) -> None:
    if snapshot.session_id != session_id:
        raise ValueError("Session ID mismatch")
    if Path(snapshot.cwd).resolve() != cwd:
        raise ValueError("Session belongs to a different project")
    if any(
        isinstance(message, AssistantMessage)
        and message.stop_reason == "pending"
        for agent in (snapshot.plan_agent, snapshot.review_agent)
        for message in agent.messages
    ):
        raise ValueError("Pending assistant messages cannot be restored")


def _agent_snapshot(agent: Agent) -> AgentSnapshot:
    return AgentSnapshot(
        provider=agent.state.model.provider,
        model=agent.state.model.id,
        messages=[*agent.state.messages],
    )


def _restore_agent(snapshot: AgentSnapshot, models: Models) -> Agent:
    model = models.get_model(snapshot.provider, snapshot.model)
    if model is None:
        raise ValueError(
            f"Unknown saved model: {snapshot.provider}/{snapshot.model}"
        )
    return Agent(
        state=AgentState(
            model=model,
            messages=[*snapshot.messages],
        ),
        stream_function=models.stream,
    )
