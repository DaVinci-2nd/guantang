import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".html", ".htm", ".css", ".scss", ".csv",
    ".tsv", ".log", ".xml", ".bat", ".cmd", ".ps1", ".sh", ".sql", ".env",
    ".gitignore", ".vue", ".java", ".c", ".h", ".cpp", ".hpp", ".go", ".rs",
    ".rb", ".php", ".lua",
}

MAX_READ_BYTES = 256 * 1024
DEFAULT_MAX_CHARS = 8000
MAX_LIST_ITEMS = 200
COMMAND_TIMEOUT = 60
MAX_COMMAND_OUTPUT = 4000


def _norm(path: str) -> str:
    return os.path.normcase(str(path))


def _under(base: Path, path: Path) -> bool:
    b = _norm(base)
    p = _norm(path)
    return p == b or p.startswith(b + os.sep)


def resolve_path(path_arg, workdirs: list, must_exist: bool = True):
    if not workdirs:
        return None, "尚未添加任何工作目录，请先调用 workdir_add 添加工作目录"
    raw = str(path_arg or "").strip()
    if not raw:
        return None, "路径不能为空"
    try:
        if os.path.isabs(raw):
            candidates = [Path(raw)]
        else:
            candidates = [Path(w) / raw for w in workdirs]
        resolved = None
        for cand in candidates:
            try:
                r = cand.expanduser().resolve()
            except Exception:
                continue
            if any(_under(Path(w), r) for w in workdirs):
                resolved = r
                break
        if resolved is None:
            return None, "路径不在任何已添加的工作目录内，禁止访问"
        if must_exist and not resolved.exists():
            return None, f"路径不存在：{resolved}"
        return resolved, None
    except Exception as e:
        return None, f"路径解析失败：{e}"


def _text_args(args, *keys):
    return [(str(args.get(k) or "")).strip() for k in keys]


def _describe_workdir_change(args):
    path, new_path = _text_args(args, "path", "new_path")
    return f"修改工作目录：把 {path} 替换为 {new_path}"


async def describe_operation(name: str, args: dict, tool_def=None) -> str:
    path, new_path, command, cwd = _text_args(args, "path", "new_path", "command", "cwd")
    content = str(args.get("content") or "")
    old_text = str(args.get("old_text") or "")
    new_text = str(args.get("new_text") or "")
    if name == "workdir_add":
        return f"添加工作目录：{path}"
    if name == "workdir_change":
        return _describe_workdir_change(args)
    if name == "workdir_remove":
        return f"删除工作目录：{path}"
    if name == "create_file":
        return f"创建文件：{path}，将写入 {len(content)} 个字符"
    if name == "edit_text":
        if old_text:
            return f"修改文件：{path}，把「{old_text[:80]}」替换为「{new_text[:80]}」"
        return f"修改文件：{path}，在末尾追加 {len(new_text)} 个字符"
    if name == "delete_path":
        recursive = "是" if args.get("recursive") else "否"
        return f"删除：{path}，递归删除：{recursive}"
    if name == "create_dir":
        return f"创建文件夹：{path}"
    if name == "run_command":
        return f"执行命令：{command}，执行目录：{cwd or '默认工作目录'}"
    if tool_def:
        return f"{tool_def.get('description') or name}，参数：{args}"
    return f"调用工具：{name}，参数：{args}"


def _fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _workdirs_status(workdirs) -> str:
    if not workdirs:
        return "当前没有任何工作目录"
    lines = [f"当前共 {len(workdirs)} 个工作目录"]
    lines += [f"  {i + 1}. {w}" for i, w in enumerate(workdirs)]
    return "\n".join(lines)


async def execute_workdir_add(args, workdirs):
    raw = str(args.get("path") or "").strip()
    if not raw:
        return "添加失败：路径不能为空"
    try:
        p = Path(raw).expanduser().resolve()
    except Exception as e:
        return f"添加失败：路径解析错误：{e}"
    if not p.is_dir():
        return f"添加失败：目录不存在或不是文件夹：{p}"
    if not os.access(str(p), os.R_OK):
        return f"添加失败：目录不可读：{p}"
    if any(_norm(w) == _norm(p) for w in workdirs):
        return f"该目录已在工作目录列表中：{p}"
    workdirs.append(str(p))
    return f"已添加工作目录：{p}\n{_workdirs_status(workdirs)}"


async def execute_workdir_change(args, workdirs):
    raw, new_raw = _text_args(args, "path", "new_path")
    if not raw or not new_raw:
        return "修改失败：必须同时提供原路径和新路径"
    matched = next((i for i, w in enumerate(workdirs) if _norm(w) == _norm(Path(raw).expanduser().resolve())), None) if workdirs else None
    if matched is None:
        return f"修改失败：{raw} 不在当前工作目录列表中\n{_workdirs_status(workdirs)}"
    try:
        new_p = Path(new_raw).expanduser().resolve()
    except Exception as e:
        return f"修改失败：新路径解析错误：{e}"
    if not new_p.is_dir():
        return f"修改失败：新目录不存在或不是文件夹：{new_p}"
    if not os.access(str(new_p), os.R_OK):
        return f"修改失败：新目录不可读：{new_p}"
    if any(_norm(w) == _norm(new_p) for w in workdirs):
        return f"修改失败：该目录已在工作目录列表中：{new_p}"
    workdirs[matched] = str(new_p)
    return f"已修改工作目录：{raw} → {new_p}\n{_workdirs_status(workdirs)}"


async def execute_workdir_remove(args, workdirs):
    raw = str(args.get("path") or "").strip()
    if not raw:
        return "删除失败：路径不能为空"
    try:
        p = Path(raw).expanduser().resolve()
    except Exception as e:
        return f"删除失败：路径解析错误：{e}"
    matched = next((i for i, w in enumerate(workdirs) if _norm(w) == _norm(p)), None)
    if matched is None:
        return f"删除失败：{raw} 不在当前工作目录列表中\n{_workdirs_status(workdirs)}"
    workdirs.pop(matched)
    return f"已删除工作目录：{p}\n{_workdirs_status(workdirs)}"


async def execute_list_dir(args, workdirs):
    path_arg = str(args.get("path") or "").strip()
    pattern = str(args.get("pattern") or "").strip()
    if not path_arg:
        return "读取失败：路径不能为空"
    p, err = resolve_path(path_arg, workdirs)
    if err:
        return err
    if not p.is_dir():
        return f"读取失败：不是文件夹：{p}"
    try:
        entries = sorted(os.scandir(str(p)), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        return f"读取失败：{e}"
    items = []
    for e in entries:
        if pattern and not fnmatch.fnmatch(e.name, pattern):
            continue
        if e.is_dir():
            items.append(f"[目录] {e.name}")
        else:
            try:
                size = _fmt_bytes(e.stat().st_size)
            except OSError:
                size = ""
            items.append(f"[文件] {e.name}，{size}")
    if len(items) > MAX_LIST_ITEMS:
        items = items[:MAX_LIST_ITEMS]
        items.append(f"……共 {len(entries)} 个匹配条目，仅显示前 {MAX_LIST_ITEMS} 个")
    head = f"{p}，共 {len(entries)} 个条目"
    if pattern:
        head += f"，过滤模式 {pattern}"
    return "\n".join([head] + items) or f"{p}：没有匹配的条目"


async def execute_read_file(args, workdirs):
    path_arg = str(args.get("path") or "").strip()
    if not path_arg:
        return "读取失败：路径不能为空"
    p, err = resolve_path(path_arg, workdirs)
    if err:
        return err
    if not p.is_file():
        return f"读取失败：不是文件：{p}"
    try:
        size = p.stat().st_size
        if size > MAX_READ_BYTES:
            return f"读取失败：文件过大：{_fmt_bytes(size)}，仅支持 {_fmt_bytes(MAX_READ_BYTES)} 以内的文本文件"
        raw = p.read_bytes()
    except OSError as e:
        return f"读取失败：{e}"
    if b"\x00" in raw[:4096]:
        return f"读取失败：{p} 是二进制文件，不支持读取"
    text = raw.decode("utf-8", errors="replace")
    max_chars = int(args.get("max_chars") or DEFAULT_MAX_CHARS)
    max_chars = max(1, min(max_chars, DEFAULT_MAX_CHARS))
    if len(text) > max_chars:
        text = text[:max_chars]
        note = f"\n文件共 {len(text)} 字符，已截取前 {max_chars} 字符"
    else:
        note = ""
    return f"=== {p} ===\n{text}{note}"


async def execute_create_file(args, workdirs):
    path_arg = str(args.get("path") or "").strip()
    content = str(args.get("content") or "")
    if not path_arg:
        return "创建失败：路径不能为空"
    p, err = resolve_path(path_arg, workdirs, must_exist=False)
    if err:
        return err
    if p.exists():
        return f"创建失败：文件已存在：{p}"
    if not p.parent.is_dir():
        return f"创建失败：上级文件夹不存在：{p.parent}"
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"创建失败：{e}"
    return f"已创建文件：{p}，{len(content)} 个字符，{_fmt_bytes(p.stat().st_size)}"


async def execute_edit_text(args, workdirs):
    path_arg, old_text, new_text = _text_args(args, "path", "old_text", "new_text")
    if not path_arg:
        return "修改失败：路径不能为空"
    p, err = resolve_path(path_arg, workdirs)
    if err:
        return err
    if not p.is_file():
        return f"修改失败：不是文件：{p}"
    try:
        content = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return f"修改失败：{e}"
    if not old_text:
        content = content + new_text
        mode = f"追加 {len(new_text)} 个字符"
    else:
        count = content.count(old_text)
        if count == 0:
            return f"修改失败：未在文件中找到指定文本「{old_text[:60]}」"
        content = content.replace(old_text, new_text)
        mode = f"替换 {count} 处"
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"修改失败：{e}"
    return f"已修改文件：{p}，{mode}"


async def execute_delete_path(args, workdirs):
    path_arg = str(args.get("path") or "").strip()
    if not path_arg:
        return "删除失败：路径不能为空"
    p, err = resolve_path(path_arg, workdirs)
    if err:
        return err
    recursive = bool(args.get("recursive"))
    try:
        if p.is_dir():
            if not recursive and any(p.iterdir()):
                return f"删除失败：文件夹非空：{p}"
            shutil.rmtree(str(p))
            return f"已删除文件夹：{p}"
        p.unlink()
        return f"已删除文件：{p}"
    except OSError as e:
        return f"删除失败：{e}"


async def execute_create_dir(args, workdirs):
    path_arg = str(args.get("path") or "").strip()
    if not path_arg:
        return "创建失败：路径不能为空"
    p, err = resolve_path(path_arg, workdirs, must_exist=False)
    if err:
        return err
    if p.exists():
        return f"创建失败：已存在：{p}"
    try:
        p.mkdir(parents=True)
    except OSError as e:
        return f"创建失败：{e}"
    return f"已创建文件夹：{p}"


_CD_PATTERN = re.compile(r"\bcd\s+([^\s&|;]*)", re.IGNORECASE)


def _has_cd_escape(command: str, workdirs: list, cwd: Path) -> bool:
    for m in _CD_PATTERN.finditer(command):
        target = m.group(1).strip().strip('"')
        if not target:
            continue
        try:
            if os.path.isabs(target):
                p = Path(target)
            else:
                p = cwd / target
            p = p.expanduser().resolve()
        except Exception:
            continue
        if not any(_under(Path(w), p) for w in workdirs):
            return True
    return False


async def execute_run_command(args, workdirs):
    command = str(args.get("command") or "").strip()
    if not command:
        return "执行失败：命令不能为空"
    if not workdirs:
        return "执行失败：尚未添加任何工作目录，请先调用 workdir_add 添加工作目录"
    cwd_arg = str(args.get("cwd") or "").strip()
    if cwd_arg:
        cwd, err = resolve_path(cwd_arg, workdirs)
        if err:
            return f"执行失败：{err}"
        if not cwd.is_dir():
            return f"执行失败：不是文件夹：{cwd}"
    else:
        cwd = Path(workdirs[0]).expanduser().resolve()
    if _has_cd_escape(command, workdirs, cwd):
        return "执行失败：命令试图切换到工作目录之外，已阻止"
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"命令执行超时：{COMMAND_TIMEOUT} 秒"
    except OSError as e:
        return f"执行失败：{e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_COMMAND_OUTPUT:
        out = out[:MAX_COMMAND_OUTPUT] + f"\n……输出过长，已截取前 {MAX_COMMAND_OUTPUT} 字符"
    head = f"执行目录：{cwd}\n命令：{command}\n退出码：{proc.returncode}"
    if proc.returncode != 0:
        return f"命令执行失败，退出码 {proc.returncode}：\n{head}\n{out or '无输出'}"
    return f"命令执行成功：\n{head}\n{out or '无输出'}"


EXECUTORS = {
    "workdir_add": execute_workdir_add,
    "workdir_change": execute_workdir_change,
    "workdir_remove": execute_workdir_remove,
    "list_dir": execute_list_dir,
    "read_file": execute_read_file,
    "create_file": execute_create_file,
    "edit_text": execute_edit_text,
    "delete_path": execute_delete_path,
    "create_dir": execute_create_dir,
    "run_command": execute_run_command,
}


async def execute_builtin(name: str, args: dict, workdirs: list) -> str:
    func = EXECUTORS.get(name)
    if func is None:
        return f"错误：未知内置工具 {name}"
    try:
        return await func(args, workdirs)
    except Exception as e:
        return f"工具执行出错：{e}"


def parse_tools(text: str) -> list[dict]:
    data = yaml.safe_load(text or "") or {}
    if not isinstance(data, dict):
        raise ValueError("工具配置必须是对象结构")
    result = []
    for key, item in data.items():
        if not isinstance(item, dict):
            raise ValueError(f"工具 {key} 的配置格式错误")
        desc = str(item.get("description") or "").strip()
        if not desc:
            raise ValueError(f"工具 {key} 缺少 description")
        params = item.get("parameters")
        if not isinstance(params, dict):
            raise ValueError(f"工具 {key} 的 parameters 格式错误")
        name = str(item.get("name") or key).strip() or key
        tool = {
            "key": key,
            "name": name,
            "description": desc,
            "parameters": params,
            "approval": bool(item.get("approval", False)),
            "required": list(item.get("required") or []),
        }
        result.append(tool)
    return result


def tools_to_yaml(tools: list[dict]) -> str:
    data = {}
    for t in tools:
        key = str(t.get("key") or "").strip()
        if not key:
            raise ValueError("工具缺少 key")
        name = str(t.get("name") or key).strip() or key
        desc = str(t.get("description") or "").strip()
        if not desc:
            raise ValueError(f"工具 {key} 缺少用途说明")
        params = t.get("parameters") or []
        if not isinstance(params, list):
            raise ValueError(f"工具 {key} 的参数格式错误")
        properties = {}
        required = []
        for p in params:
            pname = str(p.get("name") or "").strip()
            if not pname:
                raise ValueError(f"工具 {key} 存在未命名的参数")
            properties[pname] = {
                "type": str(p.get("type") or "string"),
                "description": str(p.get("description") or ""),
            }
            if p.get("required"):
                required.append(pname)
        item = {
            "approval": bool(t.get("approval", False)),
            "description": desc,
            "parameters": properties,
        }
        if required:
            item["required"] = required
        if name != key:
            item["name"] = name
        data[key] = item
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def to_openai_tools(defs: list[dict]) -> list[dict]:
    result = []
    for t in defs:
        properties = {}
        for pname, spec in (t["parameters"] or {}).items():
            if isinstance(spec, dict):
                props = dict(spec)
                if "description" not in props:
                    props["description"] = ""
                properties[pname] = props
            else:
                properties[pname] = {"type": "string", "description": str(spec)}
        schema = {"type": "object", "properties": properties}
        if t.get("required"):
            schema["required"] = t["required"]
        result.append(
            {
                "type": "function",
                "function": {"name": t["name"], "description": t["description"], "parameters": schema},
            }
        )
    return result
