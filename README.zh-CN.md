# 可复核的 AI 研究与内容自动化

[English](README.md)

> 把公开网页与本地文件变成带来源的 Markdown、数据集与待审核草稿包。
>
> 模型出计划，人来批准每一步。

[![CI](https://github.com/3023345758/Taobao-Anti-Scraping-Project/actions/workflows/ci.yml/badge.svg)](https://github.com/3023345758/Taobao-Anti-Scraping-Project/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-2EA44F)](LICENSE)

![可复核研究工作流预览](docs/assets/product-preview.png)

[查看本地生成的工作流概览动画](docs/assets/workflow-overview.gif)

## 采集、核验、输出

这不是把模型交给浏览器后任其行动的自动化脚本，而是一个本地、逐步审批的研究与内容工作流：

| 阶段 | 系统做什么 | 你保留什么 |
| --- | --- | --- |
| **采集** | 读取明确提供的公开 URL 与本地文件。 | 原始输入与清洗后的结构化数据。 |
| **核验** | 展示计划、工具、目标、风险和产物位置，逐步等待批准。 | `plan.json`、批准记录、日志和来源清单。 |
| **输出** | 生成 Markdown 报告、数据集、文档包或离线草稿包。 | 可复核、可导出的任务产物。 |

模型不能直接运行 shell、任意 Python、页面 JavaScript，不能登录网站，也不能自行发布内容。

## 现在能做什么

- 把已明确提供的公开网页整理成带来源的 Markdown 报告。
- 将 DOCX、文本型 PDF、Markdown、TXT、CSV、JSON 纳入同一任务；文档中的图片会保留为本地资产。扫描 PDF 会保存页面图像并明确提示尚未 OCR。
- 为掘金、知乎、CSDN 准备离线 Markdown 草稿包，不登录、不上传、不保存平台草稿、更不自动发布。
- 用自然语言让模型规划，或用确定性的 YAML 工作流离线执行。
- 通过 OpenAI 兼容接口、Gemini 或 Qwen 接入模型。API Key 由隐藏输入写入系统凭据库，配置文件只保存 `secret_ref`。
- 用 YAML、受审阅 Python 插件和 `GenericSpider` 扩展开发者工作流。

完整能力边界见 [产品范围](docs/PRODUCT_SCOPE.md)，常见场景见 [工作流画廊](docs/WORKFLOW_GALLERY.md)。

## 快速开始

项目以 Python 3.12 为验证运行时。以下命令本地运行，不需要 API Key：

```bash
conda env create -f environment.yml
conda activate generic-crawler-py312
pip install -e .
agent doctor
agent list-workflows
```

使用仓库内置的离线 CSV 示例创建任务。第一条命令会输出任务 ID；先阅读计划，再决定是否批准下一步。

```bash
agent run "汇总示例市场笔记" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv \
  --workspace-root .demo-tasks

agent approve <task-id> step-01
agent resume <task-id>
agent status <task-id>
```

每次 `resume` 最多执行一个已批准步骤。任务完成后，工作区内会保留报告、来源清单、计划、批准记录与运行日志。

## 模型只负责规划

非开发者可以先配置一个模型，再用自然语言创建任务；开发者也可以完全不启用模型，直接运行版本化 YAML 工作流。

```bash
# 任何 OpenAI Chat Completions 兼容端点
agent configure provider \
  --name default \
  --kind openai_compatible \
  --model your-model \
  --base-url https://your-endpoint/v1 \
  --make-default

# 原生 Gemini 与 Qwen
agent configure provider --name gemini --kind gemini --model gemini-2.5-flash
agent configure provider --name qwen --kind qwen --model qwen-plus
```

模型可以提出计划或生成可选摘要，但不能获得任意执行权限。所有实际动作只能由注册工具在逐项批准后完成。

## 页面采集是执行引擎

需要结构化网页采集时，可以使用配置驱动的 `GenericSpider`。它按以下顺序处理字段失败：

1. YAML 中的 CSS selector。
2. Scrapling 自适应解析。
3. 显式开启后才使用的、仅在当前页面重试一次的 LLM selector 建议。

LLM 修复默认关闭，失败时会记录日志并返回空值，不会把“没有数据”伪装成成功，也不承诺能够恢复任意改版站点。

[页面演化靶场](labs/page_evolution/README.md) 使用纯本地 HTML 夹具演示 selector 漂移、适配回退和候选方案复核，不访问第三方网站。

## 教育归档

`v1.0-educational` 保留了早期单站点实验的历史版本。它不是当前产品线，也不应被当作访问控制规避或生产采集方案。

请阅读 [教育归档说明](docs/EDUCATIONAL_VERSION.md)，了解历史案例、边界和与页面演化靶场的关系。

## 安全与贡献

- 只处理公开、获得授权的数据和网页。
- 不提交 API Key、Cookie、浏览器 profile、私人页面快照或任务产物。
- 贡献工作流或插件前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。
- 后续受审阅扩展见 [ROADMAP.md](ROADMAP.md)。

## License

MIT
