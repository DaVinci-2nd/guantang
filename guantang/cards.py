import base64
import io
import json
import re
import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"

LOCAL_VARS = {
    "char", "player", "model_name", "date", "time", "datetime",
    "system", "arch", "cpu", "cpu_cores", "gpu", "memory", "hostname", "language",
    "角色名", "玩家名", "模型名", "日期", "时间", "日期时间",
    "系统", "架构", "处理器", "核心数", "显卡", "内存", "主机名", "语言",
}

ALIAS_VARS = {"user": "player"}

_MACRO_RE = re.compile(r"\{\{([^{}]*)\}\}")


def convert_macros(text: str) -> str:
    if not text:
        return text

    def repl(m):
        inner = m.group(1).strip()
        if not inner:
            return ""
        if "::" in inner:
            name, _, rest = inner.partition("::")
        else:
            name, _, rest = inner.partition(":")
        name = name.strip()
        if name == "space":
            try:
                n = int(rest.strip())
                return " " * max(0, n)
            except (TypeError, ValueError):
                return " "
        if name == "newline":
            try:
                n = int(rest.strip())
                return "\n" * max(0, n)
            except (TypeError, ValueError):
                return "\n"
        if name in ("random", "pick"):
            first = rest.split("::")[0].strip()
            return first
        if name in ("//", "comment"):
            return ""
        if name in ("if", "else", "/if", "trim", "noop", "roll", "input",
                    "banned", "getvar", "setvar", "getglobalvar", "setglobalvar",
                    "outlet", "maxPrompt", "maxContext", "maxResponse", "reverse"):
            return ""
        if name in ALIAS_VARS:
            name = ALIAS_VARS[name]
        if name in LOCAL_VARS:
            return "{{" + name + "}}"
        return ""

    return _MACRO_RE.sub(repl, text)


def _png_chunks(data: bytes):
    if not data.startswith(PNG_SIG):
        return None
    chunks = []
    pos = 8
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        if pos + 8 + length > len(data):
            break
        cdata = data[pos + 8:pos + 8 + length]
        chunks.append((ctype, cdata))
        pos += 12 + length
        if ctype == b"IEND":
            break
    return chunks


def _parse_card_json(card):
    if isinstance(card, list):
        card = next((c for c in card if isinstance(c, dict)), None)
    if isinstance(card, dict) and card.get("spec") == "chara_card_v2" and isinstance(card.get("data"), dict):
        return card["data"]
    if isinstance(card, dict):
        return card
    raise ValueError("角色卡 JSON 结构无法识别")


def normalize_card(card: dict) -> dict:
    d = _parse_card_json(card)
    return {
        "name": str(d.get("name") or ""),
        "description": str(d.get("description") or ""),
        "personality": str(d.get("personality") or ""),
        "scenario": str(d.get("scenario") or ""),
        "first_mes": str(d.get("first_mes") or ""),
        "mes_example": str(d.get("mes_example") or ""),
        "system_prompt": str(d.get("system_prompt") or ""),
        "post_history_instructions": str(d.get("post_history_instructions") or ""),
        "creator_notes": str(d.get("creator_notes") or ""),
        "tags": list(d.get("tags") or []),
        "creator": str(d.get("creator") or ""),
        "character_version": str(d.get("character_version") or ""),
        "extensions": d.get("extensions") or {},
        "character_book": d.get("character_book"),
    }


def _json_from_bytes(raw: bytes):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            parsed = json.loads(raw.decode(enc))
            if isinstance(parsed, (dict, list)):
                return parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def _json_from_text(text: bytes):
    parsed = _json_from_bytes(text)
    if parsed is not None:
        return parsed
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    return _json_from_bytes(raw)


def parse_card(data: bytes) -> dict:
    parsed = _json_from_bytes(data)
    if parsed is not None:
        return normalize_card(parsed)
    chunks = _png_chunks(data)
    if chunks:
        for ctype, cdata in chunks:
            text = None
            if ctype == b"tEXt":
                _, _, val = cdata.partition(b"\x00")
                text = val
            elif ctype == b"zTXt":
                _, _, rest = cdata.partition(b"\x00")
                if rest:
                    try:
                        text = zlib.decompress(rest[1:])
                    except zlib.error:
                        text = None
            elif ctype == b"iTXt":
                parts = cdata.split(b"\x00", 4)
                if len(parts) == 5:
                    try:
                        raw = parts[4]
                        if parts[1] == b"\x01":
                            raw = zlib.decompress(raw)
                        text = raw
                    except zlib.error:
                        text = None
            if text:
                parsed = _json_from_text(text)
                if parsed is not None:
                    return normalize_card(parsed)
        end_pos = data.rfind(b"IEND")
        if end_pos > 0:
            tail = data[end_pos + 8:]
            if tail.strip():
                parsed = _json_from_text(tail.strip())
                if parsed is not None:
                    return normalize_card(parsed)
    raise ValueError("无法识别的角色卡格式")


def card_to_setting(card: dict) -> str:
    parts = []
    if card.get("description"):
        parts.append("【角色描述】\n" + convert_macros(card["description"]))
    if card.get("personality"):
        parts.append("【性格】\n" + convert_macros(card["personality"]))
    if card.get("scenario"):
        parts.append("【场景】\n" + convert_macros(card["scenario"]))
    if card.get("system_prompt"):
        parts.append("【系统提示词】\n" + convert_macros(card["system_prompt"]))
    if card.get("post_history_instructions"):
        parts.append("【对话后指令】\n" + convert_macros(card["post_history_instructions"]))
    if card.get("first_mes"):
        parts.append("【开场白】\n" + convert_macros(card["first_mes"]))
    if card.get("mes_example"):
        parts.append("【对话示例】\n" + convert_macros(card["mes_example"]))
    if card.get("character_book"):
        parts.append("（导入时已剔除不支持的设定书 character_book）")
    return "\n\n".join(parts)


def _solid_png(size: int = 512, color=(40, 40, 48)) -> bytes:
    row = b"\x00" + bytes(color) * size
    raw = row * size
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    out = bytearray(PNG_SIG)
    for ctype, data in ((b"IHDR", ihdr), (b"IDAT", zlib.compress(raw, 9))):
        out += struct.pack(">I", len(data)) + ctype + data
        out += struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    out += b"\x00\x00\x00\x00IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return bytes(out)


def build_card_png(avatar_bytes: bytes | None, json_text: str) -> bytes:
    if avatar_bytes and avatar_bytes.startswith(PNG_SIG):
        base = avatar_bytes
    else:
        base = _solid_png()
    out = bytearray(PNG_SIG)
    pos = 8
    while pos + 8 <= len(base):
        length = struct.unpack(">I", base[pos:pos + 4])[0]
        ctype = base[pos + 4:pos + 8]
        if pos + 8 + length > len(base):
            break
        cdata = base[pos + 8:pos + 8 + length]
        if ctype == b"IEND":
            break
        if ctype in (b"tEXt", b"zTXt", b"iTXt") and cdata.split(b"\x00", 1)[0] == b"ch":
            pos += 12 + length
            continue
        out += struct.pack(">I", length) + ctype + cdata
        out += struct.pack(">I", zlib.crc32(ctype + cdata) & 0xFFFFFFFF)
        pos += 12 + length
    text = json_text.encode("utf-8")
    tc = b"tEXt" + b"ch\x00" + text
    out += struct.pack(">I", len(tc) - 4) + tc
    out += struct.pack(">I", zlib.crc32(tc) & 0xFFFFFFFF)
    out += b"\x00\x00\x00\x00IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return bytes(out)
