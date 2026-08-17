# 灌汤（Guantang）
这是一个以角色为单位的本地智能体框架，它将传统工程智能体框架与角色扮演结合，同时保持高度的可控性与可拓展性，可以让接入的AI以一种特定人格来完成指定的工作。

## 特性

**本地优先**
纯本地部署，任何会话信息全部存于本机，不存在云端组件

**以角色为单位**
将系统提示词、技能（MCP）、模式（追加提示词）等与角色绑定，让智能体拥有人格，可快速切换

**多工作目录**
单个会话可设置多个工作目录，支持多路径工程操作

**内置工程工具**
包含文件读写、命令执行、联网搜索等内置工具，可直接开始工程编辑任务

**高度可控**
所有提示词、发送流、上下文均可修改，重要操作设置手动审批流程

**数据透明**
可直接查看发送流和会话原始数据，无暗箱流程

**可拓展**
支持MCP即插即用

**模式自定义**
可自定义追加提示词，会话中可快速切换，可用于完成复杂工程

**Web UI + CLI**
支持Web界面与命令行两种使用方式

**额外支持**
兼容OpenAI标准的任意模型，兼容导入SillyTavern角色卡、Cherry Studio助手预设

**其它基础功能**
重说改写与分支继续、超长对话自动裁剪、附件上传、Markdown展示等

## 快速开始

要求：Python 3.11+

```bash
git clone https://github.com/DaVinci-2nd/guantang.git
cd guantang
pip install -e .
```

配置模型（两种方法）：

a. 启动后在Web UI的“模型”页添加：填写base_url、模型名与API key
b. 直接编辑 `models/*.yaml`，密钥也可写入 `.env`（`DEEPSEEK_API_KEY=...`）

启动：

```bash
python server.py        # Web UI，自动打开 http://127.0.0.1:8688
```

CLI模式：

```bash
guantang                # 或 python main.py
```

角色创建：在“角色”页创建，或导入酒馆角色卡（PNG/JSON）、Cherry Studio 助手 JSON。

数据与安全：会话/消息/附件/发送日志全部存于 `data/` 目录；API key 只存在于 `models/*.yaml` 或 `.env`；以上路径均已在 `.gitignore` 中，不会进入版本库。

## 兼容

- **模型 API**：OpenAI 兼容 `/chat/completions` 接口（DeepSeek、Moonshot Kimi、智谱 GLM、Qwen、本地 vLLM/Ollama 等）
- **角色卡**：酒馆 chara_card_v2 导入/导出（PNG chunk / JSON / base64），Cherry Studio 助手导入
- **技能**：MCP 标准（stdio 子进程），支持任意 MCP 服务器
- **搜索**：Tavily API、必应搜索（免密钥）
- **环境**：Python 3.11+，Windows / Linux / macOS

## 关于二次开发

主仓库由作者维护，聚焦核心功能。欢迎 fork 出自己的分支，自由定制。

本项目不做agent定向优化（不会提供AGENTS.md等面向修改的文档），请以fork方式使用。

## 开源协议

[Mozilla Public License 2.0](https://mozilla.org/MPL/2.0/)（见 [LICENSE](LICENSE)）

要点：修改框架自身的文件必须继续以MPL-2.0开源；允许将框架集成进闭源商业产品；使用框架生成的内容归生成者所有，与框架无关。
