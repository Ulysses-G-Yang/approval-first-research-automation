# 通用网页采集与 AI 研究助手

> 一个自带自适应解析、LLM selector 修复与审批式研究工作流的本地工具。

[![CI](https://github.com/3023345758/Taobao-Anti-Scraping-Project/actions/workflows/ci.yml/badge.svg)](https://github.com/3023345758/Taobao-Anti-Scraping-Project/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 功能演示

`GIF 占位：docs/assets/demo.gif`

框架将站点差异收敛到 YAML 配置，并在字段提取失败时依次尝试常规 CSS selector、Scrapling 自适应解析和可选 LLM selector 修复。每一步都有日志与降级，不把“没有数据”伪装成成功。

## 快速开始

```bash
conda env create -f environment.yml && conda activate generic-crawler-py312 && playwright install chromium && python extract_prices.py --config configs/douban.yaml --output output/douban.jsonl
```

本项目以 Python 3.12 作为验证运行时。也可以手动创建环境后安装依赖：

```bash
pip install -r requirements.txt
playwright install chromium
```

## AI 自动化研究助手（预览）

`agent` 将“研究一个主题、读取公开网页或本地数据文件、整理成可复核 Markdown 报告”变成一个本地、逐步批准的工作流。模型只负责生成和解释计划，实际执行只能使用已注册的工具；它不能直接运行 shell、任意 Python、页面 JavaScript、登录或发布操作。

首次配置时，API Key 会以隐藏输入方式写入 Windows Credential Manager，设置文件只保存 `secret_ref`：

```bash
python agent.py configure provider --name default --kind openai_compatible --model your-model --base-url https://your-endpoint/v1
```

Gemini 与 Qwen 也可以原生配置：

```bash
python agent.py configure provider --name gemini --kind gemini --model gemini-2.5-flash --make-default
python agent.py configure provider --name qwen --kind qwen --model qwen-plus
```

使用显式来源创建研究任务。每个步骤都会展示工具、输入、网络目标、风险等级和产物位置，随后必须分别 `approve` 和 `resume`：

```bash
python agent.py run "整理这些来源并生成市场报告" --url https://example.com/a --url https://example.com/b
python agent.py approve <task-id> step-01
python agent.py resume <task-id>
```

不想让模型参与规划时，可使用内置工作流；它们仍然逐项审批：

```bash
python agent.py run "汇总本地销售数据" --workflow file_report --input data.csv
python agent.py run "将公开页面转成知识包" --workflow web_to_markdown --url https://example.com/article
# 开发者：将一个无 actions 的 YAML 爬虫配置纳入同样的审批与审计流程
python agent.py run "采集并整理站点列表" --workflow crawler_report --input configs/site.yaml
# 将 DOCX、PDF、TXT 或 Markdown 转为带本地图片资产的 Markdown 包
python agent.py run "整理现有技术文章" --workflow document_to_markdown --input article.docx
# 准备某个平台的离线草稿包，不会登录、上传或发布
python agent.py run "准备掘金草稿" --workflow content_save_draft --platform juejin --input article.docx
```

将一份 TXT、Markdown、CSV 或 JSON URL 列表作为 `--input` 交给模型规划时，它可以选择受控的 `url_list.read` 工具先提取候选来源；读取列表本身不会自动访问每个链接。

默认任务目录为 `~/GenericCrawler/tasks/<task-id>/`，其中保存 `task.json`、`plan.json`、`approvals.jsonl`、`run.jsonl`、原始来源、去重数据、`report.md` 与 `sources.jsonl`。使用 `python agent.py status <task-id>` 查看进度，使用 `python agent.py export <task-id>` 打包完整审计材料。

工作流定义保存在 [workflows](workflows)，每个 YAML 只能引用已注册工具，不能嵌入 shell、Python 或页面脚本。详细的架构、Provider 格式、权限模型和开发者插件协议见 [AI 研究助手说明](docs/AI_RESEARCH_ASSISTANT.md)，工作流编写规则见 [工作流说明](docs/WORKFLOW_AUTHORING.md)，非秘密配置模板见 [configs/agent_template.yaml](configs/agent_template.yaml)。

## 三层提取机制

1. **配置 selector**：字段由 YAML 中的 CSS selector、属性和作用域定义，是默认且最快的路径。
2. **Scrapling 自适应解析**：常规 selector 返回空时，使用相同字段标识在页面 HTML 中尝试自适应定位。
3. **LLM selector 修复**：前两层均失败时，只有显式开启后才调用 Gemini 或 Qwen，返回候选 selector 并仅在当前页面重试一次。

LLM 修复默认关闭，候选 selector 会按“页面 URL + 字段名”缓存。API 调用超时、依赖缺失或模型返回无效内容时，任务记录日志并继续，不会中断整次采集。

## 配置驱动

新站点通常只需要新增一个 YAML 文件：

```yaml
name: example-listing
start_url: "https://example.com/list"
enable_adaptive: true

pagination:
  enabled: true
  next_selector: ".next a"
  max_pages: 2

item_selector: ".card"
fields:
  - name: title
    selector: ".title"
  - name: price
    selector: ".price"

llm:
  enable_repair: false
  provider: gemini
  secret_ref: "provider:gemini"
  model: gemini-2.5-flash
  timeout: 10
```

完整字段、分页、浏览器与动作示例见 [configs/spider_template.yaml](configs/spider_template.yaml)。请通过 `agent configure provider` 将 API Key 写入系统凭据库；YAML 只保存 `secret_ref`，不要提交真实 Key。

## 验证与测试

豆瓣 Top250 配置用于手动验证：

```bash
python extract_prices.py --config configs/douban.yaml --output output/douban.jsonl
python scripts/verify.py --artifacts-dir output/verification
```

第二条命令会先运行正常 selector，再自动注入错误 selector。它只有在 `title` 和 `rating` 非空、且日志出现 `ADAPTIVE_SUCCESS` 事件时才会通过。该命令会访问豆瓣，请仅在符合网站条款和当地法律的前提下运行。

离线测试不访问任何第三方站点：

```bash
python -m unittest discover -s tests -v
```

## 典型教育案例：淘宝配置

[configs/taobao.yaml](configs/taobao.yaml) 是将早期单站点逻辑迁移到配置驱动结构的教学案例。它不代表对任何平台的兼容性承诺，也不应用于绕过访问控制、采集非公开数据或违反服务条款。

早期版本保存在 [v1.0-educational](https://github.com/3023345758/Taobao-Anti-Scraping-Project/tree/v1.0-educational)，背景与边界见 [教育归档说明](docs/EDUCATIONAL_VERSION.md)。

## 文章草稿工具

`scripts/publish_articles.py` 一次只处理一个平台，默认仅保存草稿：

```bash
python scripts/publish_articles.py --platform juejin --mode draft
```

正式发布必须显式使用 `--mode publish`。脚本只有获得公开文章 URL 才会报告发布成功；失败时默认保留当前窗口以便人工处理。浏览器登录状态位于本机用户数据目录，永远不会写入仓库。

## 项目结构

```text
agent.py       AI 研究助手 CLI 入口
configs/       站点与模板配置
core/          通用爬虫引擎与 LLM 修复模块
docs/          教育归档、技术文章与发布说明
research_assistant/  任务编排、Provider、审批执行器与受控工具
scripts/       现场验证与文章草稿工具
tests/         离线夹具与单元测试
workflows/     可版本管理的声明式研究、文档与草稿工作流
```

## 安全与贡献

- 不提交 API Key、Cookie、浏览器 profile、私有页面数据或采集结果。
- AI 研究助手只处理已明确提供的公开 HTTP(S) 页面和本地文件。支持 DOCX、文本型 PDF、Markdown、TXT、CSV、JSON；扫描 PDF 目前只保留页面图像并提示 OCR 尚未执行。
- `content_save_draft` 只生成离线草稿包；不读取登录态、不上传图片、不自动保存平台草稿，也不自动正式发布。
- 贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。
- 本项目仅用于合法、获得授权的数据采集与工程研究；使用者须自行遵守适用法律、网站政策与速率限制。

## License

MIT
