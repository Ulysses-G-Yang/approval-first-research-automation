# Approval-First Crawler — 让 AI 帮你采集网页，但每一步你说了算

[English](README.md)

> 一个自适应、多平台的通用网页采集框架。
> 模型负责规划和修复，人负责审批和决策。
> 页面改版了？选择器自动修复，不用熬夜改 YAML。

[![CI](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/Ulysses-G-Yang/approval-first-research-automation/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-2EA44F)](LICENSE)

![可复核研究工作流预览](docs/assets/product-preview.png)

[查看本地生成的工作流概览动画](docs/assets/workflow-overview.gif)

## 目录

- [为什么你需要这个项目？](#为什么你需要这个项目)
- [能做什么](#能做什么)
- [30 秒快速开始](#30-秒快速开始)
- [架构概览](#架构概览)
- [模型接入](#模型接入)
- [页面采集引擎](#页面采集引擎)
- [工作空间与产物](#工作空间与产物)
- [项目结构](#项目结构)
- [安全与贡献](#安全与贡献)
- [License](#license)

## 为什么你需要这个项目？

**问题是：** 现有的 AI 爬虫方案分两种——要么把浏览器完全交给 LLM（不安全、不可审计），要么纯靠手写 XPath/CSS（页面一改版就挂了）。

**这个项目的答案是第三条路：** 让 LLM 出方案、做修复，但**执行权永远在你手里**。每一步网络请求、每一次选择器修复，都需要你审批后才执行。

| 你的痛点 | 本项目的解法 |
|----------|-------------|
| LLM 自主执行不可控，不知道它访问了什么 | **审批优先架构**：模型出计划，每一步你批准后才执行，所有动作写入审计日志 |
| 网站改版，选择器批量失效，熬夜维护 | **5 层自适应闭环**：配置→备用→历史记忆→自适应解析→LLM 修复，改版后自动恢复 |
| 每接入一个网站都要从零写脚本 | **平台适配层**：电商/社交/列表型网站有通用适配器，换网站只需微调字段 |
| 采集的数据不知道怎么整理 | **数据→报告全链路**：自动去重、质量校验、生成可溯源 Markdown 报告 |
| 不想被云 API 绑架 | **本地优先**：默认 Scrapling 自适应（不调 LLM），可选 Ollama 本地模型 |

## 能做什么

### 核心采集引擎

| 能力 | 状态 | 说明 |
|------|------|------|
| YAML 配置驱动采集 | ✅ | 一个 YAML 定义一个站点，无需写 Python |
| 5 层自适应闭环修复 | ✅ | 配置→备用→记忆→Scrapling→LLM，改版自动恢复 |
| 字段级质量校验 | ✅ | 12 种校验规则：非空、类型、正则、枚举、长度范围 |
| 修复记忆持久化 | ✅ | 成功修复自动缓存，下次同一模式跳过 LLM（省钱） |
| 平台适配层 | ✅ | 通用适配器抽象 + 电商适配器（19 个域名） |
| Playwright 反检测 | ✅ | playwright-stealth 集成，可选 stealth 模式 |
| 分页自动采集 | ✅ | next_selector 翻页 + 限速延迟 |

### 审批与控制

| 能力 | 状态 | 说明 |
|------|------|------|
| 模型规划 → 人工审批 → 执行 | ✅ | 每步需批准，所有动作写入审计日志 |
| 多 Provider 支持 | ✅ | OpenAI 兼容 / Gemini / Qwen |
| API Key 安全存储 | ✅ | 系统凭据库，YAML 不存明文 |
| 确定性 YAML 工作流 | ✅ | 不调模型也能离线执行 |

### 数据输出

| 能力 | 状态 | 说明 |
|------|------|------|
| 可溯源 Markdown 报告 | ✅ | 每条数据带来源 URL + 采集时间 + artifact ID |
| DOCX / PDF → Markdown | ✅ | 文档图片保留为本地资产 |
| 离线草稿包（掘金/知乎/CSDN） | ✅ | 不登录、不上传、不发布 |
| 多格式导出 | ✅ | JSON / JSONL / CSV |

### 开发中

| 能力 | 状态 |
|------|------|
| 视觉定位回退（截图→多模态识别） | 🧪 |
| 训练数据导出管线（ShareGPT / Alpaca） | 🧪 |
| OCR 扫描 PDF | 📋 |
| 平台草稿发布适配器 | 📋 |
| 更多平台适配器（社交媒体、搜索引擎） | 📋 |

## 30 秒快速开始

Python 3.12 是已验证的运行时。以下命令本地运行，不需要 API Key：

```bash
conda env create -f environment.yml
conda activate generic-crawler-py312
pip install -e .
agent doctor
agent list-workflows
```

用仓库内置的离线 CSV 示例创建第一个任务：

```bash
agent run "汇总示例市场笔记" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv \
  --workspace-root .demo-tasks

agent approve <task-id> step-01
agent resume <task-id>
agent status <task-id>
```

每次 `resume` 最多执行一个已批准步骤。任务完成后工作区内会保留报告、来源清单、计划、批准记录与运行日志。

## 架构概览

本项目是审批优先的研究与内容自动化工具，结合了可配置浏览器采集引擎与受限任务执行器：

| 阶段 | 系统做什么 | 你保留什么 |
| --- | --- | --- |
| **采集** | 读取明确提供的公开 URL 与本地文件 | 原始输入、清洗后的结构化数据 |
| **核验** | 展示计划、工具、目标、风险和产物位置，逐步等待批准 | `plan.json`、批准记录、日志、来源清单 |
| **输出** | 生成 Markdown 报告、数据集、文档包或离线草稿包 | 可复核、可导出的任务产物 |

模型不能直接运行 shell、任意 Python、页面 JavaScript，不能登录网站，也不能自行发布内容。

## 模型接入

非开发者可以先配置一个模型，再用自然语言创建任务；开发者也可以完全不启用模型，直接运行版本化 YAML 工作流。

```bash
# 任何 OpenAI Chat Completions 兼容端点
agent configure provider \
  --name default \
  --kind openai_compatible \
  --model your-model \
  --base-url https://your-endpoint/v1 \
  --make-default

# 原生 Gemini 或 Qwen
agent configure provider --name gemini --kind gemini --model gemini-2.5-flash
agent configure provider --name qwen --kind qwen --model qwen-plus
```

CLI 以隐藏输入方式读取 API Key，并存入系统凭据库。YAML 配置中只保存 `secret_ref`，不出现明文 Key。

## 页面采集引擎

内置 `GenericSpider` 在你需要结构化网页采集时可用。它按以下顺序处理字段提取失败：

1. YAML 中配置的 CSS 选择器
2. 备用选择器列表（fallback_selectors）
3. 修复记忆库查询（历史成功修复的缓存）
4. Scrapling 自适应解析
5. 显式开启的 LLM 选择器修复（默认关闭，仅当前页面重试一次）

LLM 修复失败时记录日志并返回空值，不会把"没有数据"伪装成成功。

[页面演化靶场](labs/page_evolution/README.md) 使用纯本地 HTML 夹具演示选择器漂移、自适应回退和候选方案复核，不访问第三方网站。

```bash
python -m labs.page_evolution.run_lab
```

## 工作空间与产物

每个任务默认隔离在 `~/GenericCrawler/tasks/<task-id>/` 下：

```text
task.json          目标与状态，不包含 API Key
plan.json          受限工具计划
approvals.jsonl    审批审计记录
run.jsonl          执行日志
artifacts/         来源快照、清洗数据、Markdown 和资产
artifacts/report.md
artifacts/sources.jsonl
```

`agent export <task-id>` 将整个任务目录打包为可移植 zip。

## 项目结构

```text
agent.py                  CLI 入口
research_assistant/       审批引擎：规划器、执行器、工具注册、工作空间
adapters/                 平台适配层：BaseAdapter 抽象 + ECommerceAdapter 等
core/
  spider_engine.py        配置驱动的浏览器采集引擎（Playwright + Scrapling）
  self_healing.py         自适应闭环引擎（5 层退化链路）
  quality_gate.py         字段级质量校验（12 种规则）
  repair_persistence.py   修复记忆持久化（JSONL）
  llm_repair.py           LLM 选择器修复（Gemini / Qwen）
workflows/                版本化 YAML 声明式工作流
examples/                 可复现的本地示例
labs/                     离线兼容性教学实验
docs/                     产品文档、架构说明、工作流画廊
tests/                    离线单元测试和集成测试
configs/                  站点配置模板（豆瓣、淘宝等）
adaptive_closed_loop_demo.py  端到端闭环 Demo（可独立运行）
```

## 历史说明

仓库名和 `v1.0-educational` tag 保留了早期单站点教学实验。该 tag 是不可变历史资料，不代表当前产品线。不应被用于绕过访问控制、采集非公开数据或违反服务条款。

详情见 [教育归档说明](docs/EDUCATIONAL_VERSION.md)。

## 安全与贡献

- 只处理公开、获得授权的网页和数据。
- 不提交 API Key、Cookie、浏览器 profile、私人页面快照或任务产物。
- 贡献工作流或插件前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。
- 后续受审阅扩展见 [ROADMAP.md](ROADMAP.md)。

## License

MIT
