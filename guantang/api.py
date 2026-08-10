import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Config
from .engine import Engine
from .mcp_client import MCPManager
from .model_defs import ModelStore
from .modes import ModeStore
from .prompts import PromptAssembler
from .providers.base import ProviderError
from .providers.factory import build_provider
from .roles import RoleStore
from .skills import SkillStore
from .storage import Storage
from .thinking_presets import THINKING_PRESETS, build_thinking
from .zh_translator import ZhTranslator


class RolePayload(BaseModel):
    name: str
    avatar: str = ""
    model: str = ""
    thinking_mode: str = ""
    thinking_strength: str = "medium"
    thinking_custom: str = ""
    temperature: float | None = 1.0
    max_tokens: int | None = 4096
    skills: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    default_mode: str = ""
    setting: str = ""


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


class ModePayload(BaseModel):
    name: str
    description: str = ""
    content: str = ""


class ModelPayload(BaseModel):
    name: str
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""
    api_key: str = "DEEPSEEK_API_KEY"


class SessionPayload(BaseModel):
    character_name: str = ""
    mode: str = ""


class MessagePayload(BaseModel):
    content: str
    attachments: list[dict] | None = None


class ConfigPayload(BaseModel):
    player: dict | None = None
    ui: dict | None = None


class AppContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.roles = RoleStore(cfg.root)
        self.skills = SkillStore(cfg.root)
        self.modes = ModeStore(cfg.root)
        self.models = ModelStore(cfg.root)
        self.storage = Storage(cfg.root / "data" / "guantang.db")
        self.assembler = PromptAssembler(cfg.root)
        self.provider = None
        self.provider_key = None
        self.mcp = MCPManager()
        self.engine = None

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

    async def ensure(self, model_def: dict, skill_defs: list[dict], search_defs: list[dict] | None = None):
        search_defs = search_defs or []
        key = (model_def.get("name"), tuple(s["name"] for s in skill_defs), tuple(s["name"] for s in search_defs))
        if self.provider_key == key:
            return
        if self.provider:
            await self.provider.close()
        await self.mcp.close()
        api_key = model_def.get("api_key") or self.cfg.api_key("DEEPSEEK_API_KEY")
        self.provider = build_provider(
            model_def,
            api_key,
            timeout=self.cfg.get("timeout", 120),
            max_retries=self.cfg.get("max_retries", 2),
        )
        await self.mcp.start(skill_defs)
        translator = ZhTranslator(
            self.provider,
            str(self.cfg.root / self.cfg.get("zh_cache_file", "data/zh_cache.json")),
            self.cfg.root / self.cfg.get("translator_file", "prompts/translator.md"),
        )
        self.engine = Engine(
            self.provider,
            self.mcp,
            translator,
            temperature=self.cfg.get("temperature"),
            max_tokens=self.cfg.get("max_tokens"),
            search_skills=search_defs,
        )
        self.provider_key = key

    def session_context(self, session_id: int):
        session = self.storage.get_session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
        role = self.roles.get(session["character_name"])
        if not role:
            raise HTTPException(400, "该会话绑定的角色已被删除，请先在右侧选择一个角色")
        model_def = self.models.get(role.get("model") or "")
        if not model_def:
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

    @staticmethod
    def history_to_openai(messages: list[dict]) -> list[dict]:
        return [
            {"role": "user" if m["sender"] == "player" else "assistant", "content": m["content"]}
            for m in messages
        ]

    def role_to_response(self, role: dict) -> dict:
        return {
            k: role.get(k)
            for k in ["name", "avatar", "model", "thinking_mode", "thinking_strength", "thinking_custom", "temperature", "max_tokens", "skills", "modes", "default_mode", "setting", "has_avatar_file"]
        }


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    ctx = AppContext(cfg)
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
        return {
            "roles": [ctx.role_to_response(r) for r in ctx.roles.list()],
            "skills": ctx.skills.list(),
            "modes": ctx.modes.list(),
            "models": ctx.models.list(),
            "thinking_presets": THINKING_PRESETS,
            "sessions": ctx.storage.list_sessions(),
            "session_characters": ctx.storage.session_characters(),
            "player": cfg.player(),
            "ui": cfg.ui(),
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
        return ctx.modes.upsert(payload.model_dump())

    @app.put("/api/modes/{name}")
    async def update_mode(name: str, payload: ModePayload):
        if not ctx.modes.get(name):
            raise HTTPException(404, "模式不存在")
        data = payload.model_dump()
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

    @app.get("/api/sessions/{session_id}/messages")
    async def list_messages(session_id: int):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        return ctx.storage.list_messages(session_id)

    @app.post("/api/sessions/{session_id}/messages")
    async def add_message(session_id: int, payload: MessagePayload):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        return ctx.storage.add_message(session_id, "player", payload.content, attachments=payload.attachments)

    @app.delete("/api/sessions/{session_id}/messages")
    async def clear_messages(session_id: int):
        if not ctx.storage.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        with ctx.storage._conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        return {"ok": True}

    @app.get("/api/thinking-presets")
    async def thinking_presets():
        return {"presets": THINKING_PRESETS}

    @app.get("/api/rules")
    async def rules():
        return {"text": ""}

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
        return {"player": cfg.player(), "ui": cfg.ui()}

    @app.put("/api/config")
    async def update_config(payload: ConfigPayload):
        if payload.player is not None:
            cfg.set_player(name=payload.player.get("name"), avatar=payload.player.get("avatar"))
        if payload.ui is not None:
            cfg.set_ui(
                theme=payload.ui.get("theme"),
                sidebar_left=payload.ui.get("sidebar_left"),
                sidebar_right=payload.ui.get("sidebar_right"),
            )
        cfg.save()
        return {"player": cfg.player(), "ui": cfg.ui()}

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                data = await ws.receive_json()
                session_id = data.get("session_id")
                message = (data.get("message") or "").strip()
                if not session_id or not message:
                    await ws.send_json({"type": "error", "text": "参数不完整"})
                    continue
                try:
                    session, role, model_def = ctx.session_context(session_id)
                except HTTPException as e:
                    await ws.send_json({"type": "error", "text": e.detail})
                    continue
                skill_defs = ctx.resolve_skills(role)
                search_defs = ctx.resolve_search_skills(role)
                await ctx.ensure(model_def, skill_defs, search_defs)
                system = ctx.build_system(session, role, model_def)
                thinking = build_thinking(
                    model_def.get("model", ""),
                    role.get("thinking_mode", ""),
                    role.get("thinking_custom", ""),
                )
                history = ctx.history_to_openai(ctx.storage.list_messages(session_id))
                reasoning = ""
                reply = ""
                tool_events = []
                try:
                    async for event in ctx.engine.run_messages(system, history, thinking=thinking):
                        kind = event[0]
                        if kind == "reasoning":
                            reasoning += event[1]
                            await ws.send_json({"type": "reasoning", "delta": event[1]})
                        elif kind == "text":
                            reply += event[1]
                            await ws.send_json({"type": "text", "delta": event[1]})
                        elif kind == "tool_call":
                            await ws.send_json(
                                {"type": "tool_call", "name": event[1].name, "arguments": event[1].arguments}
                            )
                        elif kind == "tool_exec":
                            await ws.send_json({"type": "tool_exec", "name": event[1]})
                        elif kind == "tool_result":
                            tool_events.append({"name": event[1], "result": event[2]})
                            await ws.send_json({"type": "tool_result", "name": event[1], "text": event[2][:800]})
                except ProviderError as e:
                    await ws.send_json({"type": "error", "text": str(e)})
                    continue
                saved = ctx.storage.add_message(
                    session_id, "character", reply, reasoning, tool_events, character_name=role["name"]
                )
                await ws.send_json({"type": "end", "message": saved})
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
