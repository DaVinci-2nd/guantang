import asyncio
import base64
import json
import mimetypes
import os
import uuid
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState
from pydantic import BaseModel, Field

from .builtin_tools import parse_tools, read_approval_reject_text, tools_to_yaml
from .cards import build_card_png, card_to_setting, parse_card
from .cherry import parse_assistant
from .config import Config
from .engine import ApprovalStopped, Engine
from .mcp_client import MCPManager
from .model_defs import ModelStore
from .modes import ModeStore
from .prompts import PromptAssembler
from .providers.base import ProviderError
from .providers.factory import build_provider
from .roles import RoleStore
from .send_log import send_log, set_context
from .skills import SkillStore
from .storage import Storage
from .thinking_presets import THINKING_PRESETS, build_thinking
from .zh_translator import ZhTranslator

REJECT_STOP_TEXT = "此操作已被手动拒绝，对话已中断。"

VISION_KEYWORDS = ("vision", "multimodal")
DETAIL_LEVELS = ("auto", "low", "high", "original")


def _supports_vision(model_def: dict | None) -> bool:
    if not model_def or not model_def.get("model"):
        return False
    if model_def.get("vision"):
        return True
    name = str(model_def["model"]).lower()
    return any(k in name for k in VISION_KEYWORDS)


def build_latest_path(messages: list[dict], choices: dict | None = None) -> list[dict]:
    picks = {}
    roots = sorted({m["branch_root"] for m in messages if m.get("branch_root")})
    for r in roots:
        bs = sorted({m["branch_id"] for m in messages if m.get("branch_root") == r})
        pick = (choices or {}).get(r)
        picks[r] = pick if pick in bs else max(bs)

    def show(m):
        root = m.get("branch_root", 0)
        b = m.get("branch_id", 0)
        if not root:
            return True
        return picks.get(root) == b

    cut_root = None
    for r in roots:
        if picks.get(r) != max({m["branch_id"] for m in messages if m.get("branch_root") == r}):
            cut_root = r
            break

    if cut_root is None:
        return [m for m in messages if show(m)]

    cut_time = None
    for m in messages:
        if m.get("branch_root") == cut_root:
            ct = m.get("created_at") or 0
            if cut_time is None or ct < cut_time:
                cut_time = ct
    if cut_time is None:
        cut_time = 0

    result = []
    for m in messages:
        root = m.get("branch_root", 0)
        if root == cut_root:
            if m.get("branch_id") == picks.get(cut_root):
                result.append(m)
            continue
        if (m.get("created_at") or 0) > cut_time:
            continue
        if show(m):
            result.append(m)
    return result


def _msg_content_text(content) -> str:
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content or "")


def restore_messages_from_log(entries: list[dict], cleared_at: float = 0) -> list[dict]:
    restored = []
    seen = set()
    for entry in reversed(entries):
        if cleared_at and entry.get("ts", 0) > cleared_at:
            continue
        if (entry.get("context") or {}).get("purpose") != "chat":
            continue
        msgs = (entry.get("request") or {}).get("messages") or []
        for m in msgs:
            role = m.get("role")
            if role == "system":
                continue
            if role == "user":
                content = _msg_content_text(m.get("content")).strip()
                if not content:
                    continue
                sig = ("u", content)
                if sig in seen:
                    continue
                seen.add(sig)
                restored.append({"sender": "player", "content": content, "created_at": entry.get("ts", 0), "_sig": sig})
        output = entry.get("output")
        if output:
            blocks = output.get("blocks") or []
            content = output.get("text") or ""
            reasoning = output.get("reasoning") or ""
            if not (content or reasoning or blocks):
                continue
            sig = ("a", output.get("branch_id") or 0, json.dumps(blocks, ensure_ascii=False, sort_keys=True))
            if sig in seen:
                continue
            seen.add(sig)
            restored.append({
                "sender": "character",
                "character_name": (entry.get("context") or {}).get("role") or "",
                "content": content,
                "reasoning": reasoning,
                "blocks": blocks,
                "interrupted": False,
                "branch_id": output.get("branch_id") or (entry.get("context") or {}).get("branch_id") or 0,
                "branch_root": output.get("branch_root") or (entry.get("context") or {}).get("regenerate_from") or 0,
                "created_at": entry.get("ts", 0),
                "_sig": sig,
            })
            continue
        pending_tool_blocks = None
        for m in msgs:
            role = m.get("role")
            if role == "assistant":
                content = m.get("content") or ""
                reasoning = m.get("reasoning_content") or ""
                tool_calls = m.get("tool_calls") or []
                blocks = []
                if reasoning:
                    blocks.append({"type": "reasoning", "text": reasoning})
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except (json.JSONDecodeError, TypeError):
                        args = {"_raw": fn.get("arguments")}
                    blocks.append({"type": "tool", "name": fn.get("name", ""), "arguments": args, "result": ""})
                sig = ("a", (entry.get("context") or {}).get("branch_id") or 0, json.dumps(blocks, ensure_ascii=False, sort_keys=True))
                if sig in seen:
                    pending_tool_blocks = None
                    continue
                seen.add(sig)
                restored.append({
                    "sender": "character",
                    "character_name": (entry.get("context") or {}).get("role") or "",
                    "content": content,
                    "reasoning": reasoning,
                    "blocks": blocks,
                    "interrupted": False,
                    "branch_id": (entry.get("context") or {}).get("branch_id") or 0,
                    "branch_root": (entry.get("context") or {}).get("regenerate_from") or 0,
                    "created_at": entry.get("ts", 0),
                    "_sig": sig,
                })
                pending_tool_blocks = blocks
            elif role == "tool" and pending_tool_blocks is not None:
                tb = next(
                    (b for b in reversed(pending_tool_blocks)
                     if b.get("type") == "tool" and b.get("name") == m.get("name") and not b.get("result")),
                    None,
                )
                if tb is not None:
                    tb["result"] = m.get("content") or ""
    return restored


def build_branch_tree(messages: list[dict]) -> list[dict]:
    tree = []
    for m in messages:
        if m.get("branch_root"):
            continue
        node = {
            "message": {k: v for k, v in m.items() if k != "_sig"},
            "branches": [],
        }
        for bid in sorted({x.get("branch_id") for x in messages if x.get("branch_root") == m.get("id")}):
            branch_msgs = [
                {k: v for k, v in x.items() if k != "_sig"}
                for x in messages
                if x.get("branch_root") == m.get("id") and x.get("branch_id") == bid
            ]
            node["branches"].append({"branch_id": bid, "messages": branch_msgs})
        tree.append(node)
    return tree


def merge_messages(storage_msgs: list[dict], restored: list[dict]) -> list[dict]:
    storage_by_key = {}
    for m in storage_msgs:
        key = ("u", (m.get("content") or "").strip()) if m["sender"] == "player" else (
            "a", m.get("branch_id") or 0, json.dumps(m.get("blocks") or [], ensure_ascii=False, sort_keys=True)
        )
        storage_by_key.setdefault(key, []).append(m)
    result = []
    seen = set()
    for r in restored:
        sig = r.get("_sig")
        if sig in seen:
            continue
        seen.add(sig)
        sm = storage_by_key.get(sig)
        if sm:
            result.append(sm[0])
        else:
            result.append({k: v for k, v in r.items() if k != "_sig"})
    for m in storage_msgs:
        key = ("u", (m.get("content") or "").strip()) if m["sender"] == "player" else (
            "a", m.get("branch_id") or 0, json.dumps(m.get("blocks") or [], ensure_ascii=False, sort_keys=True)
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(m)
    return result


def validate_replace_rules(rules: list[dict]):
    import re

    for i, rule in enumerate(rules):
        pattern = str(rule.get("pattern") or "").strip()
        if not pattern:
            raise HTTPException(400, f"替换规则 {i + 1} 缺少匹配词")
        try:
            re.compile(pattern)
        except re.error as e:
            raise HTTPException(400, f"替换规则 {i + 1} 的正则无效：{e}")


class RolePayload(BaseModel):
    name: str
    avatar: str | None = ""
    model: str | None = ""
    thinking_mode: str | None = ""
    thinking_strength: str | None = "medium"
    thinking_custom: str | None = ""
    temperature: float | None = 1.0
    max_tokens: int | None = 4096
    skills: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    default_mode: str | None = ""
    setting: str | None = ""


class SkillPayload(BaseModel):
    name: str
    type: str = "mcp"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict | None = None
    enabled: bool = True
    provider: str = "tavily"
    api_key: str = ""
    base_url: str = ""
    url: str = ""
    tool_name: str = ""


class ModePayload(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    replace_rules: list[dict] = Field(default_factory=list)


class ModelPayload(BaseModel):
    name: str
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    api_key: str = "DEEPSEEK_API_KEY"
    vision: bool = False
    detail: str = ""


class SessionPayload(BaseModel):
    character_name: str = ""
    mode: str = ""
    title: str | None = None
    workdirs: list[str] | None = None


class MessagePayload(BaseModel):
    content: str
    attachments: list[dict] | None = None


class ConfigPayload(BaseModel):
    player: dict | None = None
    ui: dict | None = None
    multimodal: dict | None = None
    auto_title: dict | None = None
    search_tool_prompt: str | None = None


class AppContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.roles = RoleStore(cfg.root)
        self.skills = SkillStore(cfg.root)
        self.modes = ModeStore(cfg.root)
        self.models = ModelStore(cfg.root)
        self.storage = Storage(cfg.root / "data" / "guantang.db")
        self.assembler = PromptAssembler(cfg.root)
        self.pending_tasks = []

    def resolve_skills(self, role: dict) -> list[dict]:
        chosen = set(role.get("skills") or [])
        all_skills = {s["name"]: s for s in self.skills.list()}
        return [
            all_skills[n]
            for n in chosen
            if n in all_skills and all_skills[n].get("type", "mcp") == "mcp" and all_skills[n].get("enabled", True)
        ]

    def resolve_search_skills(self, role: dict) -> list[dict]:
        chosen = set(role.get("skills") or [])
        all_skills = {s["name"]: s for s in self.skills.list()}
        return [
            all_skills[n]
            for n in chosen
            if n in all_skills and all_skills[n].get("type") == "search" and all_skills[n].get("enabled", True)
        ]

    def builtin_tools_file(self):
        return self.cfg.root / self.cfg.get("builtin_tools_file", "prompts/builtin_tools.yaml")

    def builtin_tools_text(self) -> str:
        path = self.builtin_tools_file()
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def builtin_tools_defs(self) -> list[dict]:
        return parse_tools(self.builtin_tools_text())

    def approval_reject_text(self) -> str:
        return read_approval_reject_text(self.builtin_tools_text())

    async def build_engine(self, model_def: dict, skill_defs: list[dict], search_defs: list[dict] | None = None) -> Engine:
        mcp = MCPManager()
        await mcp.start(skill_defs or [])
        api_key = model_def.get("api_key") or self.cfg.api_key("DEEPSEEK_API_KEY")
        provider = build_provider(
            model_def,
            api_key,
            timeout=self.cfg.get("timeout", 120),
            max_retries=self.cfg.get("max_retries", 2),
        )
        translator = ZhTranslator(
            provider,
            str(self.cfg.root / self.cfg.get("zh_cache_file", "data/zh_cache.json")),
            self.cfg.root / self.cfg.get("translator_file", "prompts/translator.md"),
        )
        return Engine(
            provider,
            mcp,
            translator,
            temperature=self.cfg.get("temperature"),
            max_tokens=self.cfg.get("max_tokens"),
            search_skills=search_defs,
            builtin_loader=self.builtin_tools_defs,
            search_tool_prompt=self.cfg.get("search_tool_prompt", "") or "",
        )

    async def process_chat(self, ws: WebSocket, data: dict, session_id: int):
        ctx = self
        cfg = self.cfg
        try:
            session, role, model_def = ctx.session_context(session_id)
        except HTTPException as e:
            await ws.send_json({"type": "error", "text": e.detail})
            return
        skill_defs = ctx.resolve_skills(role)
        search_defs = ctx.resolve_search_skills(role)
        engine = await ctx.build_engine(model_def, skill_defs, search_defs)
        try:
            async def abort_watch():
                try:
                    m = await ws.receive_json()
                except Exception:
                    return "disconnect"
                return "abort" if m.get("type") == "abort" else "other"

            abort_task = asyncio.create_task(abort_watch())

            async def approval_handler(name: str, arguments: dict, operation: str, diff=None) -> str:
                abort_task.cancel()
                approval_id = f"ap_{uuid.uuid4().hex[:10]}"
                payload = {
                    "type": "approval",
                    "approval_id": approval_id,
                    "name": name,
                    "arguments": arguments,
                    "operation": operation,
                }
                if diff:
                    payload["diff"] = diff
                await ws.send_json(payload)
                while True:
                    resp = await ws.receive_json()
                    if resp.get("type") == "abort":
                        return "reject"
                    if resp.get("type") != "approval_response" or resp.get("approval_id") != approval_id:
                        continue
                    decision = resp.get("decision")
                    if decision in ("allow", "reject", "reject_stop"):
                        return decision

            workdirs = session.get("workdirs") or []
            engine.approval_handler = approval_handler
            engine.approval_reject_text = ctx.approval_reject_text()
            engine.workdirs = workdirs
            engine.temperature = role.get("temperature") if role.get("temperature") is not None else cfg.get("temperature")
            engine.max_tokens = role.get("max_tokens") if role.get("max_tokens") is not None else cfg.get("max_tokens")
            engine.replace_rules = []
            if session.get("mode"):
                mode = ctx.modes.get(session["mode"])
                if mode:
                    engine.replace_rules = mode.get("replace_rules") or []

            async def check_cancel() -> bool:
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=1)
                except asyncio.TimeoutError:
                    return False
                except WebSocketDisconnect:
                    return True
                return msg.get("type") == "abort"

            engine.check_cancel = check_cancel
            regenerate_from = None
            if data.get("regenerate_from"):
                try:
                    regenerate_from = int(data.get("regenerate_from"))
                except (TypeError, ValueError):
                    regenerate_from = None
                if regenerate_from and not ctx.storage.get_message(session_id, regenerate_from):
                    regenerate_from = None
                if regenerate_from:
                    target = ctx.storage.get_message(session_id, regenerate_from)
                    if target and (target["sender"] != "player" or target.get("branch_root")):
                        for m in reversed(ctx.storage.list_messages(session_id)):
                            if m["id"] < regenerate_from and m["sender"] == "player" and not m.get("branch_root"):
                                regenerate_from = m["id"]
                                break
            branch_choices = {}
            raw_choices = data.get("branch_choices")
            if isinstance(raw_choices, dict):
                for k, v in raw_choices.items():
                    try:
                        branch_choices[int(k)] = int(v)
                    except (TypeError, ValueError):
                        continue
            branch_id = None
            if not regenerate_from and branch_choices:
                branch_msgs = ctx.messages_with_restored(session_id)
                branch_roots = sorted({m["branch_root"] for m in branch_msgs if m.get("branch_root")})
                if branch_roots:
                    last_root = branch_roots[-1]
                    root_branches = sorted({
                        m["branch_id"] for m in branch_msgs
                        if m.get("branch_root") == last_root
                    })
                    chosen = branch_choices.get(last_root)
                    if chosen and chosen in root_branches and root_branches and chosen != max(root_branches):
                        regenerate_from = last_root
                        branch_id = chosen
            if branch_id is None:
                branch_id = ctx.storage.latest_branch_id(session_id)
            if regenerate_from:
                later = [m for m in ctx.storage.list_messages(session_id) if m["id"] > regenerate_from]
                if later and all(
                    not (m.get("content") or m.get("reasoning") or m.get("blocks") or m.get("tool_events"))
                    for m in later
                ):
                    for m in later:
                        await asyncio.to_thread(ctx.storage.delete_message, session_id, m["id"])
                    regenerate_from = None
            set_context(
                session_id=session_id, role=role["name"], model=model_def.get("model", ""),
                purpose="chat", regenerate_from=regenerate_from or 0, branch_id=branch_id or 0,
            )
            system = ctx.build_system(session, role, model_def)
            thinking = build_thinking(
                model_def.get("model", ""),
                role.get("thinking_mode", ""),
                role.get("thinking_custom", ""),
            )
            super_messages = data.get("super_messages")
            use_super = bool(super_messages)
            history_messages = ctx.messages_with_restored(session_id)
            if use_super:
                system_parts = [m.get("content", "") for m in super_messages if m.get("role") == "system"]
                super_system = "\n\n".join(p for p in system_parts if p)
                if super_system:
                    system = super_system
                player_rows = [m for m in super_messages if m.get("role") == "user"]
                if player_rows:
                    last_player = player_rows[-1].get("content", "")
                    if history_messages and history_messages[-1]["sender"] == "player":
                        ctx.storage.update_message(session_id, history_messages[-1]["id"], content=last_player)
                    else:
                        ctx.storage.add_message(session_id, "player", last_player)
                history = [
                    {"role": "user" if m.get("role") == "user" else "assistant", "content": m.get("content", "")}
                    for m in super_messages
                    if m.get("role") != "system"
                ]
            else:
                if history_messages and history_messages[-1]["sender"] == "player" and not _supports_vision(model_def):
                    recognized_id = await ctx.recognize_message_images(history_messages[-1])
                    if recognized_id:
                        updated = ctx.storage.get_message(session_id, recognized_id)
                        history_messages = ctx.messages_with_restored(session_id)
                        try:
                            await ws.send_json({"type": "recognized", "message": updated})
                        except WebSocketDisconnect:
                            raise
                path = build_latest_path(history_messages, branch_choices)
                if regenerate_from:
                    path = [m for m in path if (m.get("id") or 0) <= regenerate_from]
                history = ctx.history_to_openai(path, model_def)
                ctx.maybe_auto_title(session_id, ws)
            reasoning = ""
            reply = ""
            tool_events = []
            blocks = []
            text_buf = ""
            reasoning_buf = ""
            if regenerate_from:
                has_after = any(m["id"] > regenerate_from for m in ctx.storage.list_messages(session_id))
                if has_after:
                    await asyncio.to_thread(ctx.storage.mark_branch_root, session_id, regenerate_from, branch_id)
                    branch_id = await asyncio.to_thread(ctx.storage.next_branch_id, session_id)
            ai_msg = await asyncio.to_thread(
                ctx.storage.add_message,
                session_id, "character", "", "", [], character_name=role["name"], blocks=[], interrupted=True,
                branch_id=branch_id or 0, branch_root=regenerate_from or 0,
            )
            ai_id = ai_msg["id"]
            try:
                await ws.send_json({"type": "msg_created", "message_id": ai_id})
            except WebSocketDisconnect:
                raise
            if regenerate_from:
                try:
                    await ws.send_json({"type": "branch_created", "message": ai_msg, "regenerate_from": regenerate_from})
                except WebSocketDisconnect:
                    raise
            flush_count = 0

            def current_blocks():
                b = list(blocks)
                if reasoning_buf:
                    b.append({"type": "reasoning", "text": reasoning_buf})
                if text_buf:
                    b.append({"type": "text", "text": text_buf})
                return b

            def build_output():
                return {
                    "reasoning": reasoning,
                    "text": reply,
                    "tool_events": tool_events,
                    "blocks": current_blocks(),
                }

            async def persist(interrupted):
                output = build_output()
                send_log.record_output(session_id, output)
                await asyncio.to_thread(
                    ctx.storage.update_message,
                    session_id, ai_id, content=output["text"], blocks=output["blocks"], interrupted=interrupted,
                    reasoning=output["reasoning"],
                )

            async def finalize(interrupted):
                output = build_output()
                await asyncio.to_thread(
                    ctx.storage.update_message,
                    session_id, ai_id, content=output["text"], blocks=output["blocks"], interrupted=interrupted,
                    reasoning=output["reasoning"],
                )
                send_log.record_output(session_id, output)
                return await asyncio.to_thread(ctx.storage.get_message, session_id, ai_id)

            try:
                async for event in engine.run_messages(system, history, thinking=thinking):
                    if abort_task.cancelled():
                        abort_task = asyncio.create_task(abort_watch())
                    if abort_task.done():
                        raise asyncio.CancelledError()
                    kind = event[0]
                    if kind == "reasoning":
                        back = event[2] if len(event) > 2 else 0
                        if back > 0:
                            reasoning = reasoning[:-back]
                            reasoning_buf = reasoning_buf[:-back]
                        reasoning += event[1]
                        reasoning_buf += event[1]
                        await ws.send_json({"type": "reasoning", "delta": event[1], "backspace": back})
                    elif kind == "text":
                        back = event[2] if len(event) > 2 else 0
                        if back > 0:
                            reply = reply[:-back]
                            text_buf = text_buf[:-back]
                        reply += event[1]
                        text_buf += event[1]
                        await ws.send_json({"type": "text", "delta": event[1], "backspace": back})
                    elif kind == "tool_call":
                        await ws.send_json(
                            {"type": "tool_call", "name": event[1].name, "arguments": event[1].arguments}
                        )
                    elif kind == "tool_exec":
                        if reasoning_buf:
                            blocks.append({"type": "reasoning", "text": reasoning_buf})
                            reasoning_buf = ""
                        if text_buf:
                            blocks.append({"type": "text", "text": text_buf})
                            text_buf = ""
                        await ws.send_json({"type": "tool_exec", "name": event[1]})
                        tool_events.append({"name": event[1], "arguments": event[2], "result": ""})
                        blocks.append({"type": "tool", "name": event[1], "arguments": event[2], "result": ""})
                    elif kind == "tool_result":
                        for te in tool_events:
                            if te["name"] == event[1] and not te.get("result"):
                                te["result"] = event[2]
                                break
                        for blk in blocks:
                            if blk["type"] == "tool" and blk["name"] == event[1] and not blk.get("result"):
                                blk["result"] = event[2]
                                break
                        await ws.send_json({"type": "tool_result", "name": event[1], "text": event[2][:800]})
                    flush_count += 1
                    if flush_count % 20 == 0:
                        await persist(True)
            except asyncio.CancelledError:
                output = build_output()
                await asyncio.to_thread(
                    ctx.storage.update_message,
                    session_id, ai_id, content=output["text"], blocks=output["blocks"], interrupted=True,
                    reasoning=output["reasoning"],
                )
                send_log.record_output(session_id, output)
                ctx.storage.update_session(session_id, workdirs=workdirs)
                ctx.maybe_auto_title(session_id)
                abort_task.cancel()
                try:
                    await ws.send_json({
                        "type": "end",
                        "message": await asyncio.to_thread(ctx.storage.get_message, session_id, ai_id),
                        "interrupted": True,
                    })
                except WebSocketDisconnect:
                    pass
                return
            except ProviderError as e:
                await persist(True)
                try:
                    await ws.send_json({"type": "error", "text": str(e)})
                except WebSocketDisconnect:
                    pass
                ctx.storage.update_session(session_id, workdirs=workdirs)
                ctx.maybe_auto_title(session_id)
                abort_task.cancel()
                return
            except ApprovalStopped as e:
                for te in tool_events:
                    if te["name"] == e.name and not te.get("result"):
                        te["result"] = REJECT_STOP_TEXT
                        break
                for blk in blocks:
                    if blk["type"] == "tool" and blk["name"] == e.name and not blk.get("result"):
                        blk["result"] = REJECT_STOP_TEXT
                        break
                ctx.storage.update_session(session_id, workdirs=workdirs)
                saved = await finalize(True)
                ctx.maybe_auto_title(session_id)
                abort_task.cancel()
                try:
                    await ws.send_json({"type": "end", "message": saved, "interrupted": True})
                except WebSocketDisconnect:
                    pass
                return
            except WebSocketDisconnect:
                abort_task.cancel()
                await persist(True)
                ctx.storage.update_session(session_id, workdirs=workdirs)
                ctx.maybe_auto_title(session_id)
                raise
            except Exception as e:
                import traceback

                traceback.print_exc()
                send_log.record_error(session_id, f"处理出错：{type(e).__name__}: {e}")
                await persist(True)
                ctx.storage.update_session(session_id, workdirs=workdirs)
                ctx.maybe_auto_title(session_id)
                abort_task.cancel()
                try:
                    await ws.send_json({"type": "error", "text": f"处理出错：{type(e).__name__}: {e}"})
                except WebSocketDisconnect:
                    pass
                return
            saved = await finalize(False)
            ctx.storage.update_session(session_id, workdirs=workdirs)
            ctx.maybe_auto_title(session_id, ws)
            abort_task.cancel()
            await ws.send_json({"type": "end", "message": saved})
        finally:
            await engine.mcp.close()
            await engine.provider.close()

    def session_context(self, session_id: int):
        session = self.storage.get_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        role = self.roles.get(session["character_name"])
        if not role:
            raise HTTPException(400, "该会话绑定的角色已被删除，请先在右侧选择一个角色")
        model_def = self.models.get(role.get("model") or "")
        if not model_def:
            mock_models = [m for m in self.models.list() if m.get("provider") == "mock"]
            if mock_models:
                model_def = mock_models[0]
            else:
                raise HTTPException(400, f"角色引用的模型不存在：{role.get('model')}")
        return session, role, model_def

    def build_system(self, session: dict, role: dict, model_def: dict | None = None) -> str:
        mode_text = ""
        if session.get("mode"):
            mode = self.modes.get(session["mode"])
            if mode:
                mode_text = mode.get("content", "")
        model_name = model_def.get("model", "") if model_def else ""
        return self.assembler.build_system_prompt(
            role.get("setting", ""),
            role["name"],
            self.cfg.player()["name"],
            mode_text,
            model_name,
        )

    def build_message_content(self, msg: dict, model_def: dict | None = None) -> str | list:
        text = msg.get("content") or ""
        detail = None
        if model_def:
            d = str(model_def.get("detail") or "").strip().lower()
            if d in DETAIL_LEVELS:
                detail = d
        images = []
        for att in msg.get("attachments") or []:
            if att.get("kind") != "image" or att.get("recognized"):
                continue
            url = att.get("url") or ""
            if not url.startswith("/files/data/attachments/"):
                continue
            path = self.cfg.root / url.removeprefix("/files/")
            if not path.exists():
                continue
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            block = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            if detail:
                block["image_url"]["detail"] = detail
            images.append(block)
        if not images:
            return text
        return [{"type": "text", "text": text}] + images

    def history_to_openai(self, messages: list[dict], model_def: dict | None = None) -> list[dict]:
        result = []
        for m in messages:
            if m["sender"] == "player":
                result.append({"role": "user", "content": self.build_message_content(m, model_def)})
                continue
            content = m.get("content") or ""
            reasoning = m.get("reasoning") or ""
            blocks = m.get("blocks") or []
            tool_blocks = [b for b in blocks if b.get("type") == "tool"]
            if not tool_blocks:
                msg = {"role": "assistant", "content": content}
                if reasoning:
                    msg["reasoning_content"] = reasoning
                result.append(msg)
                continue
            base = m.get("id") or round(float(m.get("created_at") or 0), 3)
            tool_calls = []
            for i, tb in enumerate(tool_blocks):
                try:
                    args = json.dumps(tb.get("arguments") or {}, ensure_ascii=False)
                except (TypeError, ValueError):
                    args = "{}"
                tool_calls.append({
                    "id": f"call_{base}_{i}",
                    "type": "function",
                    "function": {"name": tb.get("name", ""), "arguments": args},
                })
            assistant = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
            if reasoning:
                assistant["reasoning_content"] = reasoning
            result.append(assistant)
            for i, tb in enumerate(tool_blocks):
                result.append({
                    "role": "tool",
                    "tool_call_id": f"call_{base}_{i}",
                    "name": tb.get("name", ""),
                    "content": tb.get("result") or "",
                })
        return result

    def messages_with_restored(self, session_id: int) -> list[dict]:
        return self.storage.list_messages(session_id)

    async def recognize_message_images(self, msg: dict) -> str | None:
        mm = self.cfg.multimodal()
        if not mm.get("enabled"):
            return None
        images = [
            att for att in (msg.get("attachments") or [])
            if att.get("kind") == "image" and not att.get("recognized")
        ]
        if not images:
            return None
        set_context(session_id=msg.get("session_id"), role="图片识别", model=mm.get("model", ""), purpose="recognize")

        async def mark_and_save(extra_text: str):
            attachments = list(msg.get("attachments") or [])
            for att in attachments:
                if att.get("kind") == "image" and not att.get("recognized"):
                    att["recognized"] = True
            content = (msg.get("content") or "")
            if extra_text:
                content = content + "\n\n" + extra_text
            await asyncio.to_thread(
                self.storage.update_message,
                msg["session_id"], msg["id"], content=content, attachments=attachments,
            )
            return msg["id"]

        model_def = self.models.get(mm["model"]) if mm.get("model") else None
        if not model_def or not mm.get("prompt"):
            return await mark_and_save("")
        descriptions = []
        for att in images:
            url = att.get("url") or ""
            if not url.startswith("/files/data/attachments/"):
                continue
            path = self.cfg.root / url.removeprefix("/files/")
            if not path.exists():
                continue
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            provider = build_provider(
                model_def,
                (model_def.get("api_key") or self.cfg.api_key("DEEPSEEK_API_KEY")),
                timeout=self.cfg.get("timeout", 120),
                max_retries=self.cfg.get("max_retries", 2),
            )
            try:
                text = ""
                async for event in provider.stream_chat(
                    [
                        {"role": "system", "content": mm["prompt"]},
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                            ],
                        },
                    ]
                ):
                    if event[0] == "text":
                        text += event[1]
                desc = text.strip()
                if desc:
                    descriptions.append(f"【图片：{att.get('name', '图片')}】{desc}")
            except Exception as e:
                send_log.record_error(msg.get("session_id"), f"图片识别失败：{type(e).__name__}: {e}")
                descriptions.append(f"【图片：{att.get('name', '图片')}】图片识别失败，无法获取描述")
            finally:
                await provider.close()
        return await mark_and_save("\n\n".join(descriptions))

    def build_session_turn_text(self, messages: list[dict], rounds: int | None = None) -> str:
        lines = []
        for m in messages:
            if m["sender"] not in ("player", "character"):
                continue
            content = (m.get("content") or "").strip()
            if not content:
                continue
            label = "玩家" if m["sender"] == "player" else "角色"
            lines.append(f"{label}：{content}")
            if rounds is not None and len(lines) >= rounds * 2:
                break
        return "\n\n".join(lines)

    async def auto_title_session(self, session_id: int, ws: WebSocket | None = None):
        try:
            at = self.cfg.auto_title()
            session = self.storage.get_session(session_id)
            if not session or session.get("title_set"):
                return
            if not at.get("enabled") or not at.get("model") or not at.get("prompt"):
                return
            model_def = self.models.get(at["model"])
            if not model_def:
                return
            set_context(session_id=session_id, role="自动标题", model=model_def.get("model", ""), purpose="auto_title")
            messages = self.storage.list_messages(session_id)
            mode = int(at.get("mode", 1))
            text = ""
            if mode == 1:
                first = next((m for m in messages if m["sender"] == "player"), None)
                if not first:
                    return
                text = (first.get("content") or "").strip()
            else:
                rounds = int(at.get("rounds", 3)) if mode == 3 else 1
                if rounds < 1:
                    rounds = 1
                if rounds > 9:
                    rounds = 9
                text = self.build_session_turn_text(messages, rounds)
            if not text:
                await asyncio.to_thread(self.storage.update_session, session_id, title_set=1)
                return
            provider = build_provider(
                model_def,
                (model_def.get("api_key") or self.cfg.api_key("DEEPSEEK_API_KEY")),
                timeout=self.cfg.get("timeout", 120),
                max_retries=self.cfg.get("max_retries", 2),
            )
            title = ""
            try:
                async for event in provider.stream_chat(
                    [
                        {"role": "system", "content": at["prompt"]},
                        {"role": "user", "content": text},
                    ]
                ):
                    if event[0] == "text":
                        title += event[1]
            finally:
                await provider.close()
            title = title.strip().replace("\n", " ")[:50]
            await asyncio.to_thread(self.storage.update_session, session_id, title=title, title_set=1)
            if ws is not None:
                try:
                    await ws.send_json({"type": "title", "title": title, "session_id": session_id})
                except WebSocketDisconnect:
                    pass
        except Exception as e:
            send_log.record_error(session_id, f"自动标题生成失败：{type(e).__name__}: {e}")

    def should_auto_title(self, session_id: int, player_count: int, ai_count: int) -> bool:
        at = self.cfg.auto_title()
        if not at.get("enabled") or not at.get("model") or not at.get("prompt"):
            return False
        session = self.storage.get_session(session_id)
        if not session or session.get("title_set"):
            return False
        mode = int(at.get("mode", 1))
        if mode == 1:
            return player_count == 1
        if mode == 2:
            return player_count == 1 and ai_count >= 1
        rounds = int(at.get("rounds", 3))
        if rounds < 1:
            rounds = 1
        if rounds > 9:
            rounds = 9
        return player_count >= rounds and ai_count >= rounds

    def maybe_auto_title(self, session_id: int, ws: WebSocket | None = None):
        messages = self.storage.list_messages(session_id)
        player_count = sum(1 for m in messages if m["sender"] == "player")
        ai_count = sum(1 for m in messages if m["sender"] == "character")
        if self.should_auto_title(session_id, player_count, ai_count):
            task = asyncio.create_task(self.auto_title_session(session_id, ws))
            self.pending_tasks.append(task)
            task.add_done_callback(lambda t: self.pending_tasks.discard(t))

    def role_to_response(self, role: dict) -> dict:
        def text(v):
            return str(v) if v else ""
        return {
            "name": role.get("name", ""),
            "avatar": text(role.get("avatar")),
            "model": text(role.get("model")),
            "thinking_mode": text(role.get("thinking_mode")),
            "thinking_strength": text(role.get("thinking_strength")) or "medium",
            "thinking_custom": text(role.get("thinking_custom")),
            "temperature": role.get("temperature"),
            "max_tokens": role.get("max_tokens"),
            "skills": role.get("skills") or [],
            "modes": role.get("modes") or [],
            "default_mode": text(role.get("default_mode")),
            "setting": str(role.get("setting") or ""),
            "has_avatar_file": role.get("has_avatar_file", False),
        }


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    ctx = AppContext(cfg)
    send_log.configure(cfg.root / "data" / "send_log.db")
    app = FastAPI(title="灌汤")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache"
        return response
    app.state.ctx = ctx

    @app.get("/api/state")
    async def state():
        try:
            builtin_tools = ctx.builtin_tools_defs()
        except Exception as e:
            builtin_tools = {"error": f"内置工具配置解析失败：{e}"}
        return {
            "roles": [ctx.role_to_response(r) for r in ctx.roles.list()],
            "skills": ctx.skills.list(),
            "modes": ctx.modes.list(),
            "models": ctx.models.list(),
            "thinking_presets": THINKING_PRESETS,
            "builtin_tools": builtin_tools,
            "sessions": ctx.storage.list_sessions(),
            "session_characters": ctx.storage.session_characters(),
            "player": cfg.player(),
            "ui": cfg.ui(),
            "multimodal": cfg.multimodal(),
            "auto_title": cfg.auto_title(),
            "search_tool_prompt": cfg.get("search_tool_prompt", "") or "",
        }

    @app.get("/api/roles")
    async def list_roles():
        return [ctx.role_to_response(r) for r in ctx.roles.list()]

    @app.post("/api/roles")
    async def create_role(payload: RolePayload):
        try:
            role = ctx.roles.create(payload.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))
        return ctx.role_to_response(role)

    @app.put("/api/roles/{name}")
    async def update_role(name: str, payload: RolePayload):
        if not ctx.roles.get(name):
            raise HTTPException(404, "角色不存在")
        try:
            role = ctx.roles.update(name, payload.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))
        return ctx.role_to_response(role)

    @app.delete("/api/roles/{name}")
    async def delete_role(name: str):
        ctx.roles.delete(name)
        return {"ok": True}

    @app.post("/api/roles/{name}/avatar")
    async def upload_avatar(name: str, file: UploadFile):
        content = await file.read()
        if not content:
            raise HTTPException(400, "文件为空")
        try:
            avatar = ctx.roles.save_avatar(name, file.filename or "avatar.png", content)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"avatar": avatar}

    def unique_role_name(name: str) -> str:
        if not ctx.roles.get(name):
            return name
        i = 2
        while ctx.roles.get(f"{name}{i}"):
            i += 1
        return f"{name}{i}"

    def save_card_avatar(name: str, content: bytes):
        if content.startswith(b"\x89PNG"):
            try:
                avatar = ctx.roles.save_avatar(name, "avatar.png", content)
                ctx.roles.update(name, {"avatar": avatar})
            except ValueError:
                pass

    @app.post("/api/roles/import-card")
    async def import_card(file: UploadFile):
        content = await file.read()
        if not content:
            raise HTTPException(400, "文件为空")
        try:
            card = parse_card(content)
        except ValueError as e:
            raise HTTPException(400, str(e))
        name = unique_role_name(card["name"] or "导入角色")
        try:
            role = ctx.roles.create({"name": name, "setting": card_to_setting(card)})
        except ValueError as e:
            raise HTTPException(400, str(e))
        save_card_avatar(name, content)
        return ctx.role_to_response(ctx.roles.get(name))

    @app.post("/api/roles/import-cherry")
    async def import_cherry(file: UploadFile):
        content = await file.read()
        if not content:
            raise HTTPException(400, "文件为空")
        try:
            parsed = parse_assistant(content)
        except ValueError as e:
            raise HTTPException(400, str(e))
        name = unique_role_name(parsed["name"])
        try:
            role = ctx.roles.create({"name": name, "setting": parsed["setting"]})
        except ValueError as e:
            raise HTTPException(400, str(e))
        return ctx.role_to_response(role)

    @app.post("/api/roles/import-native")
    async def import_native(file: UploadFile):
        content = await file.read()
        if not content:
            raise HTTPException(400, "文件为空")
        try:
            card = parse_card(content)
        except ValueError as e:
            raise HTTPException(400, str(e))
        ext = card.get("extensions") or {}
        gt = ext.get("guantang") if isinstance(ext, dict) else None
        if not isinstance(gt, dict):
            raise HTTPException(400, "不是本框架导出的角色卡")
        name = str(gt.get("name") or "").strip() or card["name"] or "导入角色"
        name = unique_role_name(name)
        data = {k: v for k, v in gt.items() if k in (
            "avatar", "model", "thinking_mode", "thinking_strength", "thinking_custom",
            "temperature", "max_tokens", "skills", "modes", "default_mode",
        ) and v is not None}
        data["name"] = name
        data["setting"] = str(gt.get("setting") or "")
        try:
            role = ctx.roles.create(data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        save_card_avatar(name, content)
        return ctx.role_to_response(ctx.roles.get(name))

    @app.get("/api/roles/{name}/export")
    async def export_role(name: str):
        role = ctx.roles.get(name)
        if not role:
            raise HTTPException(404, "角色不存在")
        gt = {k: role.get(k) for k in (
            "name", "avatar", "model", "thinking_mode", "thinking_strength", "thinking_custom",
            "temperature", "max_tokens", "skills", "modes", "default_mode", "setting",
        )}
        card = {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": role.get("name") or name,
                "description": (role.get("setting") or "")[:200],
                "personality": "",
                "scenario": "",
                "first_mes": "",
                "mes_example": "",
                "system_prompt": role.get("setting") or "",
                "post_history_instructions": "",
                "creator_notes": "",
                "tags": [],
                "creator": "",
                "character_version": "",
                "extensions": {"guantang": gt},
            },
        }
        avatar_bytes = None
        avatar = role.get("avatar") or ""
        if role.get("has_avatar_file") and avatar:
            try:
                avatar_bytes = (ctx.roles.dir / name / avatar).read_bytes()
            except OSError:
                avatar_bytes = None
        png = build_card_png(avatar_bytes, json.dumps(card, ensure_ascii=False))
        encoded_name = url_quote(f"{name}.png")
        return Response(
            content=png,
            media_type="image/png",
            headers={"Content-Disposition": f"attachment; filename=\"card.png\"; filename*=UTF-8''{encoded_name}"},
        )

    @app.get("/api/roles/{name}/system-prompt")
    async def role_system_prompt(name: str, mode: str = ""):
        role = ctx.roles.get(name)
        if not role:
            raise HTTPException(404, "角色不存在")
        mode_text = ""
        if mode:
            m = ctx.modes.get(mode)
            if m:
                mode_text = m.get("content", "")
        model_def = ctx.models.get(role.get("model") or "")
        model_name = model_def.get("model", "") if model_def else ""
        return {"system_prompt": ctx.assembler.build_system_prompt(
            role.get("setting", ""), role["name"], cfg.player()["name"], mode_text, model_name
        )}

    @app.get("/api/variables")
    async def variables():
        from .variables import GROUPS, VARIABLE_LIST

        return {"variables": VARIABLE_LIST, "groups": GROUPS}

    @app.get("/api/skills")
    async def list_skills():
        return ctx.skills.list()

    @app.post("/api/skills")
    async def upsert_skill(payload: SkillPayload):
        data = payload.model_dump()
        if data.get("type", "mcp") == "mcp" and not data.get("command"):
            raise HTTPException(400, "MCP 技能必须填写命令")
        return ctx.skills.upsert(data)

    @app.put("/api/skills/{name}")
    async def update_skill(name: str, payload: SkillPayload):
        if not ctx.skills.get(name):
            raise HTTPException(404, "技能不存在")
        data = payload.model_dump()
        if name != data["name"]:
            ctx.skills.delete(name)
        return ctx.skills.upsert(data)

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str):
        ctx.skills.delete(name)
        return {"ok": True}

    @app.get("/api/modes")
    async def list_modes():
        return ctx.modes.list()

    @app.post("/api/modes")
    async def upsert_mode(payload: ModePayload):
        data = payload.model_dump()
        validate_replace_rules(data.get("replace_rules") or [])
        return ctx.modes.upsert(data)

    @app.put("/api/modes/{name}")
    async def update_mode(name: str, payload: ModePayload):
        if not ctx.modes.get(name):
            raise HTTPException(404, "模式不存在")
        data = payload.model_dump()
        validate_replace_rules(data.get("replace_rules") or [])
        if name != data["name"]:
            ctx.modes.delete(name)
        return ctx.modes.upsert(data)

    @app.delete("/api/modes/{name}")
    async def delete_mode(name: str):
        ctx.modes.delete(name)
        return {"ok": True}

    @app.get("/api/models")
    async def list_models():
        return ctx.models.list()

    @app.post("/api/models")
    async def upsert_model(payload: ModelPayload):
        data = payload.model_dump()
        if not data.get("base_url"):
            raise HTTPException(400, "必须填写接口地址")
        if not data.get("model"):
            raise HTTPException(400, "必须填写模型名称")
        return ctx.models.upsert(data)

    @app.put("/api/models/{name}")
    async def update_model(name: str, payload: ModelPayload):
        if not ctx.models.get(name):
            raise HTTPException(404, "模型不存在")
        data = payload.model_dump()
        if name != data["name"]:
            ctx.models.delete(name)
        return ctx.models.upsert(data)

    @app.delete("/api/models/{name}")
    async def delete_model(name: str):
        ctx.models.delete(name)
        return {"ok": True}

    @app.get("/api/sessions")
    async def list_sessions():
        return ctx.storage.list_sessions()

    @app.post("/api/sessions")
    async def create_session(payload: SessionPayload):
        if payload.character_name and not ctx.roles.get(payload.character_name):
            raise HTTPException(400, "角色不存在")
        mode = payload.mode
        if not mode and payload.character_name:
            role = ctx.roles.get(payload.character_name)
            mode = role.get("default_mode", "") if role else ""
        return ctx.storage.create_session(payload.character_name, mode)

    @app.put("/api/sessions/{session_id}")
    async def update_session(session_id: int, payload: SessionPayload):
        session = ctx.storage.get_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        if payload.title is not None:
            return ctx.storage.update_session(session_id, title=payload.title, title_set=1)
        if payload.workdirs is not None:
            norm = []
            seen = set()
            for raw in payload.workdirs:
                path = str(raw or "").strip()
                if not path:
                    continue
                try:
                    p = Path(path).expanduser().resolve()
                except Exception as e:
                    raise HTTPException(400, f"路径解析错误：{path}：{e}")
                if not p.is_dir():
                    raise HTTPException(400, f"目录不存在或不是文件夹：{p}")
                if not os.access(str(p), os.R_OK):
                    raise HTTPException(400, f"目录不可读：{p}")
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                norm.append(str(p))
            return ctx.storage.update_session(session_id, workdirs=norm)
        if payload.character_name:
            if not ctx.roles.get(payload.character_name):
                raise HTTPException(400, "角色不存在")
            mode = payload.mode
            if not mode:
                role = ctx.roles.get(payload.character_name)
                mode = role.get("default_mode", "") if role else ""
            return ctx.storage.update_session(session_id, character_name=payload.character_name, mode=mode)
        return ctx.storage.update_session(session_id, mode=payload.mode or None)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: int):
        ctx.storage.delete_session(session_id)
        return {"ok": True}

    class SessionImportPayload(BaseModel):
        session: dict = {}
        messages: list[dict] = []

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: int):
        session = ctx.storage.get_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        data = {
            "version": 1,
            "session": {
                "title": session.get("title", ""),
                "character_name": session.get("character_name", ""),
                "mode": session.get("mode", ""),
                "workdirs": session.get("workdirs") or [],
            },
            "messages": ctx.storage.list_messages(session_id),
        }
        text = json.dumps(data, ensure_ascii=False)
        return Response(
            content=text,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="session_{session_id}.json"'},
        )

    @app.post("/api/sessions/import")
    async def import_session(payload: SessionImportPayload):
        s = payload.session or {}
        session = ctx.storage.create_session(s.get("character_name", ""), s.get("mode", ""))
        ctx.storage.update_session(
            session["id"],
            title=s.get("title", ""),
            workdirs=s.get("workdirs") or [],
        )
        id_map = {}
        for m in payload.messages or []:
            new = ctx.storage.add_message(
                session["id"],
                str(m.get("sender") or "player"),
                str(m.get("content") or ""),
                reasoning=str(m.get("reasoning") or ""),
                tool_events=m.get("tool_events") or [],
                character_name=str(m.get("character_name") or ""),
                attachments=m.get("attachments") or [],
                blocks=m.get("blocks") or [],
                interrupted=bool(m.get("interrupted")),
                created_at=m.get("created_at"),
            )
            if m.get("id") is not None:
                id_map[m["id"]] = new["id"]
        for m in payload.messages or []:
            root = m.get("branch_root")
            if root and m.get("id") is not None:
                new_root = id_map.get(root)
                new_id = id_map.get(m["id"])
                if new_root and new_id:
                    ctx.storage.update_message(
                        session["id"], new_id,
                        branch_id=int(m.get("branch_id") or 0),
                        branch_root=new_root,
                    )
        return ctx.storage.get_session(session["id"])

    @app.post("/api/sessions/import-chat")
    async def import_silly_chat(file: UploadFile):
        content = await file.read()
        if not content:
            raise HTTPException(400, "文件为空")
        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(400, "酒馆聊天记录不是有效的 JSON")
        chat = data.get("chat") or (data.get("history") or {}).get("visible") or []
        if not isinstance(chat, list) or not chat:
            raise HTTPException(400, "聊天记录为空")
        char_name = ""
        for m in chat:
            if isinstance(m, dict) and not m.get("is_user") and m.get("name"):
                char_name = str(m["name"])
                break
        role = ctx.roles.get(char_name) if char_name else None
        character = char_name if role else ""
        session = ctx.storage.create_session(character, "")
        player_id = None
        last_branch = 0
        for m in chat:
            if not isinstance(m, dict):
                continue
            is_user = bool(m.get("is_user"))
            mes = str(m.get("mes") or "")
            ts = m.get("send_date")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                ts = float(ts) if ts < 1e12 else float(ts) / 1000.0
            else:
                ts = None
            if is_user:
                if not mes:
                    continue
                msg = ctx.storage.add_message(session["id"], "player", mes, created_at=ts)
                player_id = msg["id"]
                last_branch = 0
                continue
            swipes = m.get("swipes") or []
            variants = [mes] if mes else []
            for s in swipes:
                sv = str(s or "").strip()
                if sv and sv != mes and sv not in variants:
                    variants.append(sv)
            if not variants:
                continue
            ctx.storage.add_message(
                session["id"], "character", variants[0],
                character_name=character, created_at=ts,
            )
            for v in variants[1:]:
                last_branch += 1
                ctx.storage.add_message(
                    session["id"], "character", v,
                    character_name=character, created_at=ts,
                    branch_id=last_branch, branch_root=player_id or 0,
                )
        return {"session": ctx.storage.get_session(session["id"]), "roles_match": bool(role)}

    @app.get("/api/sessions/{session_id}/messages")
    async def list_messages(session_id: int):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        return ctx.messages_with_restored(session_id)

    @app.get("/api/sessions/{session_id}/branch-tree")
    async def session_branch_tree(session_id: int):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        return build_branch_tree(ctx.messages_with_restored(session_id))

    @app.post("/api/sessions/{session_id}/messages")
    async def add_message(session_id: int, payload: MessagePayload):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        return ctx.storage.add_message(session_id, "player", payload.content, attachments=payload.attachments)

    @app.delete("/api/sessions/{session_id}/messages")
    async def clear_messages(session_id: int, after: int | None = None):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        if after is not None:
            ctx.storage.delete_messages_after(session_id, after)
        else:
            ctx.storage.clear_session_messages(session_id)
        return {"ok": True}

    class UpdateMessagePayload(BaseModel):
        content: str | None = None
        blocks: list | None = None

    @app.put("/api/sessions/{session_id}/messages/{message_id}")
    async def update_message(session_id: int, message_id: int, payload: UpdateMessagePayload):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        msg = ctx.storage.update_message(session_id, message_id, content=payload.content, blocks=payload.blocks)
        if not msg:
            raise HTTPException(404, "消息不存在")
        return msg

    @app.get("/api/sessions/{session_id}/submit-context")
    async def submit_context(session_id: int, message: str = ""):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        session, role, model_def = ctx.session_context(session_id)
        system = ctx.build_system(session, role, model_def)
        messages = ctx.history_to_openai(ctx.messages_with_restored(session_id))
        if message:
            messages.append({"role": "user", "content": message})
        return {"system": system, "messages": messages}

    @app.get("/api/sessions/{session_id}/debug")
    async def session_debug(session_id: int):
        session = ctx.storage.get_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        role = ctx.roles.get(session["character_name"])
        model_def = None
        thinking = None
        skills = []
        if role:
            model_def = ctx.models.get(role.get("model") or "")
            if model_def:
                thinking = build_thinking(
                    model_def.get("model", ""),
                    role.get("thinking_mode", ""),
                    role.get("thinking_custom", ""),
                )
            skills = ctx.resolve_skills(role) + ctx.resolve_search_skills(role)

        def mask_key(data: dict) -> dict:
            out = dict(data)
            if out.get("api_key"):
                out["api_key"] = "******"
            return out

        try:
            builtin_tools = ctx.builtin_tools_defs()
        except Exception as e:
            builtin_tools = {"error": f"内置工具配置解析失败：{e}"}

        return {
            "session": session,
            "role": role,
            "model": mask_key(model_def) if model_def else None,
            "thinking": thinking,
            "parameters": {
                "temperature": cfg.get("temperature"),
                "max_tokens": cfg.get("max_tokens"),
                "timeout": cfg.get("timeout"),
                "max_retries": cfg.get("max_retries"),
            },
            "skills": [mask_key(s) for s in skills],
            "workdirs": session.get("workdirs") or [],
            "builtin_tools": builtin_tools,
            "multimodal": cfg.multimodal(),
            "auto_title": cfg.auto_title(),
            "messages": ctx.storage.list_messages(session_id),
            "send_log": send_log.for_session(session_id),
            "branch_tree": build_branch_tree(ctx.messages_with_restored(session_id)),
        }

    @app.post("/api/files")
    async def upload_file(file: UploadFile):
        content = await file.read()
        if not content:
            raise HTTPException(400, "文件为空")
        filename = file.filename or "file.bin"
        suffix = Path(filename).suffix.lower() or ".bin"
        upload_dir = cfg.root / "data" / "attachments"
        upload_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{suffix}"
        (upload_dir / name).write_bytes(content)
        return {"url": f"/files/data/attachments/{name}", "name": name}

    @app.get("/api/thinking-presets")
    async def thinking_presets():
        return {"presets": THINKING_PRESETS}

    @app.get("/api/rules")
    async def rules():
        return {"text": ""}

    class BuiltinToolsPayload(BaseModel):
        text: str

    class BuiltinToolsFormPayload(BaseModel):
        tools: list[dict]
        approval_reject_text: str | None = None

    @app.get("/api/builtin-tools")
    async def get_builtin_tools():
        text = ctx.builtin_tools_text()
        try:
            tools = parse_tools(text)
        except Exception:
            tools = []
        return {"text": text, "tools": tools, "approval_reject_text": ctx.approval_reject_text()}

    @app.put("/api/builtin-tools")
    async def save_builtin_tools(payload: BuiltinToolsPayload):
        try:
            defs = parse_tools(payload.text)
        except Exception as e:
            raise HTTPException(400, f"工具配置格式错误：{e}")
        if not defs:
            raise HTTPException(400, "工具配置不能为空")
        path = ctx.builtin_tools_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.text, encoding="utf-8")
        return {"text": payload.text, "tools": [t["name"] for t in defs]}

    @app.put("/api/builtin-tools/form")
    async def save_builtin_tools_form(payload: BuiltinToolsFormPayload):
        try:
            text = tools_to_yaml(payload.tools, payload.approval_reject_text)
            defs = parse_tools(text)
        except Exception as e:
            raise HTTPException(400, f"工具配置格式错误：{e}")
        if not defs:
            raise HTTPException(400, "工具配置不能为空")
        path = ctx.builtin_tools_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return {"text": text, "tools": defs}

    @app.post("/api/player/avatar")
    async def upload_player_avatar(file: UploadFile):
        content = await file.read()
        if not content:
            raise HTTPException(400, "文件为空")
        filename = file.filename or "avatar.png"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            raise HTTPException(400, "头像仅支持 png/jpg/webp/gif")
        avatar_dir = cfg.root / "data" / "avatar"
        avatar_dir.mkdir(parents=True, exist_ok=True)
        import time

        avatar_name = f"p-{int(time.time())}{suffix}"
        (avatar_dir / avatar_name).write_bytes(content)
        cfg.set_player(avatar=avatar_name)
        cfg.save()
        return {"avatar": avatar_name}

    @app.get("/api/config")
    async def get_config():
        return {"player": cfg.player(), "ui": cfg.ui(), "multimodal": cfg.multimodal(), "auto_title": cfg.auto_title()}

    @app.put("/api/config")
    async def update_config(payload: ConfigPayload):
        if payload.player is not None:
            cfg.set_player(name=payload.player.get("name"), avatar=payload.player.get("avatar"))
        if payload.ui is not None:
            cfg.set_ui(
                theme=payload.ui.get("theme"),
                sidebar_left=payload.ui.get("sidebar_left"),
                sidebar_right=payload.ui.get("sidebar_right"),
                centered=payload.ui.get("centered"),
            )
        if payload.multimodal is not None:
            cfg.set_multimodal(
                enabled=payload.multimodal.get("enabled"),
                model=payload.multimodal.get("model"),
                prompt=payload.multimodal.get("prompt"),
            )
        if payload.auto_title is not None:
            cfg.set_auto_title(
                enabled=payload.auto_title.get("enabled"),
                model=payload.auto_title.get("model"),
                prompt=payload.auto_title.get("prompt"),
                mode=payload.auto_title.get("mode"),
                rounds=payload.auto_title.get("rounds"),
            )
        if payload.search_tool_prompt is not None:
            cfg.data["search_tool_prompt"] = payload.search_tool_prompt
        cfg.save()
        return {"player": cfg.player(), "ui": cfg.ui(), "multimodal": cfg.multimodal(), "auto_title": cfg.auto_title()}

    @app.get("/api/settings/export")
    async def export_settings():
        roles = []
        for r in ctx.roles.list():
            item = {k: r.get(k) for k in (
                "name", "avatar", "model", "thinking_mode", "thinking_strength", "thinking_custom",
                "temperature", "max_tokens", "skills", "modes", "default_mode", "setting",
            )}
            avatar = r.get("avatar") or ""
            if r.get("has_avatar_file") and avatar:
                try:
                    item["avatar_base64"] = base64.b64encode(
                        (ctx.roles.dir / r["name"] / avatar).read_bytes()
                    ).decode("ascii")
                except OSError:
                    pass
            roles.append(item)
        player_avatar = cfg.player().get("avatar") or ""
        player_avatar_base64 = None
        if player_avatar:
            p = cfg.root / "data" / "avatar" / player_avatar
            if p.is_file():
                try:
                    player_avatar_base64 = base64.b64encode(p.read_bytes()).decode("ascii")
                except OSError:
                    pass
        data = {
            "version": 1,
            "config": cfg.data,
            "models": ctx.models.list(),
            "skills": ctx.skills.list(),
            "modes": ctx.modes.list(),
            "roles": roles,
            "builtin_tools": ctx.builtin_tools_text(),
            "approval_reject_text": ctx.approval_reject_text(),
            "player_avatar": player_avatar_base64,
        }
        return Response(
            content=json.dumps(data, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="guantang_settings.json"'},
        )

    class SettingsImportPayload(BaseModel):
        config: dict | None = None
        models: list[dict] | None = None
        skills: list[dict] | None = None
        modes: list[dict] | None = None
        roles: list[dict] | None = None
        builtin_tools: str | None = None
        approval_reject_text: str | None = None
        player_avatar: str | None = None

    @app.post("/api/settings/import")
    async def import_settings(payload: SettingsImportPayload):
        covered = []
        if payload.config is not None:
            cfg.data = dict(payload.config)
            cfg.save()
            covered.append("全局配置")
        if payload.models is not None:
            for m in payload.models:
                if isinstance(m, dict) and m.get("name"):
                    ctx.models.upsert(m)
            covered.append("模型配置")
        if payload.skills is not None:
            for s in payload.skills:
                if isinstance(s, dict) and s.get("name"):
                    ctx.skills.upsert(s)
            covered.append("技能")
        if payload.modes is not None:
            for m in payload.modes:
                if isinstance(m, dict) and m.get("name"):
                    ctx.modes.upsert(m)
            covered.append("模式")
        if payload.roles is not None:
            for r in payload.roles:
                if not isinstance(r, dict) or not r.get("name"):
                    continue
                name = r["name"]
                data = {k: r.get(k) for k in (
                    "name", "model", "thinking_mode", "thinking_strength", "thinking_custom",
                    "temperature", "max_tokens", "skills", "modes", "default_mode",
                ) if r.get(k) is not None}
                data["name"] = name
                data["setting"] = str(r.get("setting") or "")
                try:
                    ctx.roles.create(data)
                except ValueError:
                    continue
                avatar_b64 = r.get("avatar_base64")
                if avatar_b64:
                    try:
                        content = base64.b64decode(avatar_b64)
                        avatar = ctx.roles.save_avatar(name, "avatar.png", content)
                        ctx.roles.update(name, {"avatar": avatar})
                    except (ValueError, OSError):
                        pass
            covered.append("角色")
        if payload.builtin_tools is not None:
            try:
                defs = parse_tools(payload.builtin_tools)
            except Exception as e:
                raise HTTPException(400, f"内置工具配置格式错误：{e}")
            if defs:
                path = ctx.builtin_tools_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(payload.builtin_tools, encoding="utf-8")
                covered.append("内置工具")
        if payload.player_avatar:
            try:
                content = base64.b64decode(payload.player_avatar)
                avatar_dir = cfg.root / "data" / "avatar"
                avatar_dir.mkdir(parents=True, exist_ok=True)
                avatar_name = f"p-{int(time.time())}.png"
                (avatar_dir / avatar_name).write_bytes(content)
                cfg.set_player(avatar=avatar_name)
                cfg.save()
                covered.append("玩家头像")
            except (ValueError, OSError):
                pass
        return {"ok": True, "covered": covered}

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                data = await ws.receive_json()
                session_id = data.get("session_id")
                message = (data.get("message") or "").strip()
                super_messages = data.get("super_messages")
                if not session_id or (not message and not super_messages):
                    await ws.send_json({"type": "error", "text": "参数不完整"})
                    continue
                await ctx.process_chat(ws, data, session_id)
        except WebSocketDisconnect:
            pass

    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return HTMLResponse((web_dir / "index.html").read_text(encoding="utf-8"))

    app.mount("/files/roles", StaticFiles(directory=str(cfg.root / "roles")), name="role_files")
    app.mount("/files/data", StaticFiles(directory=str(cfg.root / "data")), name="data_files")
    return app
