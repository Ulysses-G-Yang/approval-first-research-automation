# 审批优先的研究自动化工具

> 面向明确的公开网页和用户主动提供的本地文件，提供本地优先、先规划、
> 后审批、再执行并保留证据的研究工作流。

[![CI](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English README](README.md)

> **状态：Alpha。** 当前源码是 `v2.1.0` 候选版本；高级自愈和平台适配器
> 仍为实验模块，最新正式 Release 仍是 `v2.0.1`。

![产品预览](docs/assets/product-preview.png)

## 为什么做这个项目

研究自动化经常让用户在“全部手工操作”和“让 Agent 不经确认直接执行”之间
二选一。本项目把规划和执行拆开：

1. 确定性工作流或已配置模型先生成计划；
2. 用户审查下一步操作；
3. 只有被批准的下一步能够执行；
4. 结果、来源、审批和日志保存在同一个本地任务空间中。

Crawler 是这套流程中的一个受控工具，不是整个产品。本项目不承诺无限制浏览器
自动化、反检测能力或任意网站兼容性。

## 当前能力状态

| 能力 | 状态 | 边界 |
| --- | --- | --- |
| 本地 CSV/JSON/TXT/Markdown 报告 | **已测试** | 有可重复的离线测试。 |
| DOCX、文本型 PDF 转 Markdown | **已测试** | 扫描页会保留，但不做 OCR。 |
| 离线内容草稿包 | **已测试** | 只创建本地文件，不登录、不上传、不发布。 |
| 公开 HTTP 网页研究 | **有限可用** | 已检查公开目标；连接阶段网络加固见 [#6](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/6)。 |
| 审批检查点与本地记录 | **有限可用** | 精确内容绑定和异常恢复见 [#4](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/4)、[#5](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/5)。 |
| 配置化浏览器采集 | **有限可用** | 安装包与浏览器网络门禁见 [#3](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/3)、[#6](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/6)。 |
| Page Evolution Lab | **已测试** | 本地回归夹具，不代表真实网站覆盖率。 |
| 五层自愈、QualityGate、修复记忆 | **实验性** | 模块存在，但尚未接入正式 Crawler 路径。 |
| 电商 Adapter 与域名匹配 | **实验性** | 属于模板代码，不代表已验证支持 19 个网站。 |
| OCR、平台草稿保存 | **计划中** | 当前尚未提供。 |

完整能力边界见 [产品范围](docs/PRODUCT_SCOPE.md)，实施顺序见唯一权威
[路线图](ROADMAP.md)。

## 从源码安装

当前验证的运行时是 Python 3.12。

```bash
git clone https://github.com/Ulysses-G-Yang/approval-first-research-automation.git
cd approval-first-research-automation
python -m venv .venv
```

激活环境并安装：

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .

# macOS/Linux
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

浏览器工作流还需要安装 Chromium：

```bash
playwright install chromium
agent doctor
agent list-workflows
```

在 [#3](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/3)
关闭前，Crawler 开发应从源码目录运行；当前 wheel 尚未证明包含完整 Crawler 包。

## 运行离线工作流

用仓库里的 CSV 示例创建任务计划：

```bash
agent run "汇总市场记录" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv
```

命令会显示任务 ID 和第一步计划。检查后，每次只批准并执行一步：

```bash
agent status <TASK_ID>
agent approve <TASK_ID> step-01
agent resume <TASK_ID>
```

重复 `status`、`approve`、`resume`，直到任务完成。随后可以导出整个本地任务空间：

```bash
agent export <TASK_ID> --output task-export.zip
```

确定性回退不要求配置模型。Provider 和模型辅助规划见
[AI Research Assistant](docs/AI_RESEARCH_ASSISTANT.md)。

## 内置工作流

| 工作流 | 用途 |
| --- | --- |
| `file_report` | 从明确提供的本地文件生成可追踪报告。 |
| `research_report` | 组合已批准的公开 URL 与本地来源。 |
| `web_to_markdown` | 从已批准来源生成 Markdown 知识包。 |
| `document_to_markdown` | 转换 DOCX、文本型 PDF、Markdown 或文本。 |
| `content_save_draft` | 准备离线的平台格式草稿包。 |
| `crawler_report` | 执行经过审查的 Crawler YAML 并生成本地报告。 |

输入和预期产物见[工作流编写说明](docs/WORKFLOW_AUTHORING.md)与
[示例目录](examples/README.md)。

## 安全边界

- 模型只能建议已注册工具及其声明参数。
- 工具只能读取任务中明确提供的 URL 或本地文件。
- 凭据保存在操作系统凭据库中，工作流和 Crawler YAML 只保存引用名。
- 助手使用的 Crawler YAML 会拒绝脚本动作和明文 API Key。
- 草稿工具只生成本地文件；内置工具不会登录、上传、保存平台草稿或发布。
- 安装、审批、恢复和浏览器网络仍存在的缺口会公开记录，不会提前写成已完成能力。

安全问题报告方式见 [Security Policy](SECURITY.md)。

## 项目结构

```text
research_assistant/   规划器、审批执行器、工具、Provider、任务空间
workflows/            版本化声明式工作流
core/                 Crawler 引擎与实验性提取模块
adapters/             实验性 Adapter 接口和模板
labs/                 本地页面演化夹具
examples/             可重复离线示例
tests/                离线单元与工作流测试
```

当前正式浏览器路径是：

```text
crawler_report -> browser.extract -> GenericSpider
```

五层 `SelfHealingEngine`、`QualityGate`、`RepairPersistence` 和 Adapter
原型尚未进入这条路径。

## 开发验证

```bash
python -m unittest discover -s tests -v
python -m compileall -q extract_prices.py agent.py core adapters research_assistant scripts labs
python -m labs.page_evolution.run_lab --json
git diff --check
```

贡献与发布要求见 [CONTRIBUTING.md](CONTRIBUTING.md) 和
[发布检查清单](docs/RELEASE_CHECKLIST.md)。

## 历史说明

仓库最初是淘宝数据采集教学项目。历史标签继续保留，但不代表当前产品承诺。
详见[教育版本说明](docs/EDUCATIONAL_VERSION.md)。

## 许可证

[MIT](LICENSE)
