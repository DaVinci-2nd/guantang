import asyncio
import os
import sys

from guantang.config import Config
from guantang.engine import Engine
from guantang.mcp_client import MCPManager
from guantang.model_defs import ModelStore
from guantang.prompts import PromptAssembler
from guantang.providers.base import ProviderError
from guantang.providers.factory import build_provider
from guantang.zh_translator import ZhTranslator

GRAY = "\033[90m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


async def chat_loop(engine, system_prompt, player_name):
    history = []
    print(f"{GREEN}灌汤 CLI。输入内容开始对话，/help 查看命令{RESET}")
    while True:
        try:
            line = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            cmd = line.split(maxsplit=1)[0]
            if cmd == "/quit":
                break
            elif cmd == "/help":
                print("  /model        查看当前模型")
                print("  /tools        查看已加载的工具")
                print("  /new          清空当前会话")
                print("  /quit         退出")
            elif cmd == "/model":
                print(f"当前模型：{engine.provider.model}")
            elif cmd == "/tools":
                if not engine.mcp.tools:
                    print("当前没有已加载的工具")
                for t in engine.mcp.tools:
                    print(f"  - {t['name']}（来自 {t['server']}）：{t['description'][:60]}")
            elif cmd == "/new":
                history = []
                print("会话已清空")
            else:
                print("  /model   /tools   /new   /quit")
            continue

        try:
            async for event in engine.run(system_prompt, line, history):
                kind = event[0]
                if kind == "reasoning":
                    print(f"{GRAY}〔思考〕{event[1]}{RESET}", end="", flush=True)
                elif kind == "text":
                    print(event[1], end="", flush=True)
                elif kind == "warn":
                    print(f"{YELLOW}\n〔中文守则〕发现非中文内容：{event[1]}{RESET}")
                elif kind == "tool_call":
                    print(f"\n{CYAN}〔工具〕→ {event[1].name}({event[1].arguments}){RESET}")
                elif kind == "tool_exec":
                    print(f"{CYAN}〔执行〕{event[1]}{RESET}")
                elif kind == "tool_result":
                    print(f"{CYAN}〔结果〕{event[2][:300]}{RESET}")
                elif kind == "end":
                    messages = event[1]
                    history = [m for m in messages[1:] if m["role"] != "system"]
                    print()
        except ProviderError as e:
            print(f"{RED}\n{e}{RESET}")


async def main():
    cfg = Config()
    os.system("")
    assembler = PromptAssembler(cfg.root)
    models = ModelStore(cfg.root).list()
    model_def = next((m for m in models if m["name"] == cfg.get("default_model", "")), None) or (models[0] if models else None)
    if not model_def:
        print(f"{RED}没有可用模型，请先在 UI 的模型页配置模型{RESET}")
        sys.exit(1)
    if model_def.get("provider") == "mock":
        provider = build_provider({"provider": "mock", "model": "mock-chat"}, "", timeout=30, max_retries=0)
    else:
        key = model_def.get("api_key") or cfg.api_key("DEEPSEEK_API_KEY")
        if not key:
            print(f"{RED}模型 {model_def['name']} 未配置 API key{RESET}")
            sys.exit(1)
        provider = build_provider(
            model_def, key,
            timeout=cfg.get("timeout", 120),
            max_retries=cfg.get("max_retries", 2),
        )
    mcp = MCPManager()
    await mcp.start([])
    translator = ZhTranslator(
        provider,
        str(cfg.root / cfg.get("zh_cache_file", "data/zh_cache.json")),
        cfg.root / cfg.get("translator_file", "prompts/translator.md"),
    )
    engine = Engine(
        provider, mcp, translator,
        temperature=cfg.get("temperature"),
        max_tokens=cfg.get("max_tokens"),
    )
    player_name = cfg.player()["name"]
    system_md = assembler.cli_system_text()
    system_prompt = assembler.build_system_prompt(system_md, "灌汤", player_name, model_name=provider.model)
    try:
        await chat_loop(engine, system_prompt, player_name)
    finally:
        await mcp.close()
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
