"""Interactive decisions: exact snapshots, no prefix rules or implicit MCP grants."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.persistence.approvals import Approval
from app.persistence.models import Run, RunStatus
from app.security.tool_secrets import contains_secret


class ApprovalError(ValueError):
    pass


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass
class PendingApproval:
    user_id: UUID
    future: asyncio.Future


class ApprovalBroker:
    def __init__(self):
        self.pending: dict[UUID, PendingApproval] = {}
        self.rules: dict[str, tuple[str, str]] = {}
        self.revisions: dict[tuple[str, str], int] = {}

    def revoke(self, user_id: str, conversation_id: str) -> None:
        self.rules = {key: scope for key, scope in self.rules.items() if scope != (user_id, conversation_id)}
        scope = (user_id, conversation_id)
        self.revisions[scope] = self.revisions.get(scope, 0) + 1

    async def authorize(self, tool, call, context, lifecycle, timeout: float) -> bool:
        scope = (context.user_id, context.conversation_id)
        revision = self.revisions.get(scope, 0)
        binding = {
            "user_id": context.user_id, "conversation_id": context.conversation_id,
            "project_id": context.project_id, "directory": context.execution_directory,
            "execution_mode": context.execution_mode, "tool_id": tool.id,
            "permissions": sorted(tool.required_permissions), "arguments": call.arguments,
        }
        serialized = json.dumps(binding, ensure_ascii=False)
        # Internal UUIDs, canonical directories and content hashes are typed
        # metadata, not free text: digit patterns in them can resemble bank cards.
        # Only these built-ins have executor-prepared fields; remote arguments
        # remain fully untrusted, including any underscore-prefixed keys.
        arguments = call.arguments
        if tool.id == "builtin.local.command":
            arguments = {key: arguments[key] for key in ("executable", "args")}
        elif tool.id == "builtin.local.delete":
            arguments = {"path": arguments["path"]}
        if contains_secret(arguments) or len(serialized) > 65536:
            return False  # Never persist a secret in an approval snapshot.
        rule = digest(binding)
        session_allowed = tool.id == "builtin.local.command" and bool(call.arguments.get("_session_safe"))
        if session_allowed and rule in self.rules:
            await lifecycle.emit("approval.rule_used", {"rule": rule, "tool_id": tool.id, "call_id": call.call_id})
            return True
        binding = {**binding, "run_id": context.run_id, "call_id": call.call_id}
        now = datetime.now(timezone.utc)
        approval = Approval(
            id=uuid4(), run_id=UUID(context.run_id), user_id=UUID(context.user_id),
            call_id=call.call_id, fingerprint=digest(binding), tool_id=tool.id, binding=binding,
            status="pending", session_allowed=session_allowed, expires_at=now + timedelta(seconds=timeout),
            warning="本次调用可能修改文件、访问网络或执行项目代码，以本机用户权限运行，不属于系统级沙箱。",
        )
        future = asyncio.get_running_loop().create_future()
        approval_id = approval.id
        self.pending[approval_id] = PendingApproval(approval.user_id, future)
        session = lifecycle.session
        try:
            session.add(approval)
            run = await session.get(Run, approval.run_id)
            run.status = RunStatus.WAITING_APPROVAL
            await lifecycle.emit("approval.requested", {
                "approval_id": str(approval.id), "fingerprint": approval.fingerprint,
                "tool_id": tool.id, "call_id": call.call_id,
            })
            try:
                decision = await asyncio.wait_for(asyncio.shield(future), timeout=max(0, (approval.expires_at - datetime.now(timezone.utc)).total_seconds()))
            except asyncio.TimeoutError:
                # A concurrent decision cannot turn an expired wait into execution.
                decision = "expired"
            if self.revisions.get(scope, 0) != revision:
                decision = "revoked"
            await session.execute(update(Approval).where(Approval.id == approval.id).values(status=decision))
            run.status = RunStatus.RUNNING
            await lifecycle.emit("approval.resolved", {"approval_id": str(approval.id), "decision": decision})
            if decision == "approve_session" and session_allowed:
                self.rules[rule] = (context.user_id, context.conversation_id)
            return decision in {"approve_once", "approve_session"}
        except BaseException:
            await session.rollback()
            await session.execute(update(Approval).where(Approval.id == approval_id).values(status="cancelled"))
            await session.commit()
            # Explicit cancellation revokes the conversation's previously granted rules.
            self.revoke(context.user_id, context.conversation_id)
            raise
        finally:
            self.pending.pop(approval_id, None)
            if not future.done():
                future.cancel()

    async def decide(self, session, approval_id: UUID, user_id: UUID, fingerprint: str, decision: str) -> None:
        if decision not in {"approve_once", "approve_session", "reject"}:
            raise ApprovalError("不支持的审批决定")
        approval = await session.scalar(select(Approval).where(Approval.id == approval_id, Approval.user_id == user_id).with_for_update())
        pending = self.pending.get(approval_id)
        if approval is None:
            raise LookupError("审批不存在")
        if (approval.status != "pending" or approval.fingerprint != fingerprint
                or approval.expires_at <= datetime.now(timezone.utc) or pending is None
                or pending.future.done() or pending.user_id != user_id):
            raise ApprovalError("审批已失效或与当前请求不匹配")
        run = await session.get(Run, approval.run_id)
        if run.status is not RunStatus.WAITING_APPROVAL:
            raise ApprovalError("任务已停止等待审批")
        if decision == "approve_session" and not approval.session_allowed:
            raise ApprovalError("该调用仅允许本次批准")
        approval.status = decision
        await session.commit()
        if not pending.future.done():
            pending.future.set_result(decision)


default_broker = ApprovalBroker()
