# AI 自动化研究助手

## 目标与边界

本项目的可选研究助手面向两类使用者：开发者可以继续编写 YAML、Python 工具和可复用工作流；非开发者可以描述研究目标，让已配置的大模型生成一份可读、可批准的执行计划。它建立在主 `GenericSpider` 和本地工具之上；当前版本覆盖公开网页、常见数据文件和 DOCX/PDF/Markdown 文档到可追溯 Markdown 产物的流程。

模型不拥有执行权限。它只能从注册表中选择工具，并说明为什么需要该步骤；任务执行器再根据用户的逐项批准调用工具。第一版没有任意 shell、任意 Python、页面 JavaScript、登录、私有网络或自动发布能力。DOCX、文本型 PDF、Markdown 和 TXT 的转换由受控工具完成，不会因此扩大模型权限。

## 安装与模型配置

使用 Python 3.12 安装项目依赖：

```bash
pip install -r requirements.txt
```

也可以安装为命令行工具：

```bash
pip install -e .
agent --help
agent doctor
agent list-workflows
```

`agent doctor` 只检查本地 Python、依赖、Playwright Chromium、非秘密配置和凭据引用，不会打印 API Key。`agent list-workflows` 会在创建任务前展示内置工作流的前置条件和可能调用的受控工具。

配置命令会以隐藏输入读取 API Key，并使用 `keyring` 写入 Windows Credential Manager。默认非秘密配置位于 `~/GenericCrawler/agent.yaml`，其格式参考 [agent_template.yaml](../configs/agent_template.yaml)。文件中只能出现 `secret_ref`，不会写入 API Key。

```bash
# 兼容 OpenAI Chat Completions 格式的服务
python agent.py configure provider \
  --name default \
  --kind openai_compatible \
  --model your-model \
  --base-url https://your-endpoint/v1 \
  --make-default

# 原生 Gemini 或 Qwen
python agent.py configure provider --name gemini --kind gemini --model gemini-2.5-flash
python agent.py configure provider --name qwen --kind qwen --model qwen-plus
```

OpenAI 兼容模式使用 `{base_url}/chat/completions`；如果 `base_url` 已以 `/chat/completions` 结尾，则直接使用该地址。Gemini 的可选 `endpoint` 会传给 SDK 的 `HttpOptions.base_url`；Qwen 的可选 `endpoint` 会传给 DashScope SDK 的 `base_address`。所有 Provider 使用统一的 JSON 规划协议，不依赖厂商工具调用格式。

## 命令流程

创建一个模型规划任务：

```bash
python agent.py run "比较这些公开产品页并生成 Markdown 报告" \
  --url https://example.com/product-a \
  --url https://example.com/product-b
```

系统会先询问是否可以将目标、已明确提供的 URL、输入文件名和工具元数据发送给模型。不会在规划阶段发送文件正文、浏览器 Cookie 或 API Key。传入 `--approve-planning` 表示在非交互环境中显式同意这次规划请求。

任务创建后，CLI 会显示每个步骤的工具、输入、网络目标、风险与产物位置。一次只可以批准下一个未完成步骤：

```bash
python agent.py approve <task-id> step-01
python agent.py resume <task-id>
python agent.py status <task-id>
python agent.py export <task-id>
```

`resume` 一次只执行一个已批准步骤。未批准步骤绝不会因 `resume`、失败重试或模型回复而自动执行。

首次体验不需要模型 API Key 或联网网页。使用仓库中的本地样例即可：

```bash
agent run "汇总示例市场笔记" \
  --workflow file_report \
  --input examples/research-report/market-notes.csv \
  --workspace-root .demo-tasks
```

样例与预期产物说明见 [工作流画廊](WORKFLOW_GALLERY.md)。

## 内置工作流

以下工作流不需要模型即可生成固定计划，适合离线验证和开发者使用：

```bash
# 本地 CSV、JSON、TXT 或 Markdown 到报告
python agent.py run "汇总季度数据" --workflow file_report --input data.csv

# 显式公共 URL 到知识包、表格和报告
python agent.py run "整理参考网页" --workflow web_to_markdown --url https://example.com/article

# 有 URL 时读取来源；没有 URL 时只搜索公开候选来源并生成来源清单
python agent.py run "寻找公开研究资料" --workflow research_report

# 开发者：复用现有 GenericSpider 配置，配置中不得包含 actions
python agent.py run "采集站点列表" --workflow crawler_report --input configs/site.yaml

# 将 DOCX、PDF、Markdown 或 TXT 转为 Markdown 与图片资产
python agent.py run "整理文章资料" --workflow document_to_markdown --input article.docx

# 仅准备本地平台草稿包；不会登录、上传或发布
python agent.py run "准备草稿" --workflow content_save_draft --platform juejin --input article.docx
```

受控工具包括：

- `web.fetch` 和 `web.search`：只允许公共 HTTP(S) 目标，拒绝 localhost、私有 IP、链路本地和保留网段。
- `url_list.read`：从明确传入的 TXT、Markdown、CSV 或 JSON 中提取公开 URL 候选项；读取列表不会自动访问其中每个页面。
- `file.read`：只读取命令行中明确以 `--input` 指定的 Markdown、TXT、CSV、JSON 文件。
- `browser.extract`：在审批边界内复用现有 `GenericSpider`，但第一版禁止 YAML `actions` 和明文 LLM Key。计划会读取该显式配置的 URL 列表以展示网络目标；若配置开启 selector LLM 修复，该步骤会提升为敏感步骤并显示对应 Provider。
- `document.inspect` 与 `document.convert`：读取明确传入的 DOCX、PDF、Markdown、TXT，并在任务工作区生成 Markdown、转换清单和本地图片资产。文本型 PDF 会提取文字与内嵌图片；扫描页面会保留渲染图并标记 OCR 未执行。
- `markdown.validate`：检查转换后 Markdown 的本地图片引用是否仍可复核。
- `content.prepare_draft`：生成掘金、知乎或 CSDN 的离线草稿包；不打开浏览器、不读取 Cookie、不上传图片、不保存草稿、不发布。
- `data.normalize`、`data.to_markdown`、`report.compose`：仅向当前任务独立工作区写入可复核产物。

网页读取、文件读取、浏览器采集和所有产物写入都是单独步骤。若任务配置了模型，`report.summarize` 会作为单独的敏感步骤，明确将已批准的去重数据样本和来源元数据发送给该 Provider；`report.compose` 则始终离线地合成最终报告。模型不可据此提出新的工具调用，模型摘要失败时仍会生成确定性的本地报告。

## 任务产物与可复核性

每个任务都保存在 `~/GenericCrawler/tasks/<task-id>/`：

```text
task.json          原始目标与运行状态，不包含 API Key
plan.json          模型或内置工作流生成的受限工具计划
approvals.jsonl    每次规划或步骤批准的审计记录
run.jsonl          执行日志
artifacts/         原始网页/文件、清洗数据、Markdown 表格和报告
artifacts/versions/<attempt-id>/report.md
artifacts/versions/<attempt-id>/sources.jsonl
artifacts/versions/<attempt-id>/documents/<bundle>/article.md
artifacts/versions/<attempt-id>/documents/<bundle>/assets/
artifacts/versions/<attempt-id>/drafts/<platform>/draft-manifest.json
```

报告保留来源 URL、抓取/读取时间、产物路径和原始结构化数据；无法访问的页面会在对应步骤中失败并留下错误状态，不会被伪装为成功。`agent export` 将整个任务目录压缩，以便共享或归档。

## 开发者扩展

开发者可以在根目录 [workflows](../workflows) 新增版本化 YAML 工作流，也可以在本地创建经审阅的 Python 模块，并在 `agent.yaml` 的 `plugins` 中显式列出模块名。工作流 YAML 只负责编排已注册工具；它不能嵌入 shell、Python、JavaScript 或登录自动化。具体格式见 [工作流说明](WORKFLOW_AUTHORING.md)。

```python
def register_tools(registry):
    registry.register(MyReviewedTool())
```

工具必须声明名称、描述、风险等级、必需参数，并实现异步 `run(context, arguments)`。插件由本地设置文件明确加载，模型不能安装插件、修改插件列表或获得插件之外的执行能力。对浏览器或外部系统有影响的插件应使用 `RiskLevel.SENSITIVE` 并提供明确的输入/输出审计信息。

## 当前限制

- 仅支持公开、获得授权的 HTTP(S) 网页，以及 DOCX、文本型 PDF、Markdown、TXT、CSV、JSON。
- 不自动登录、不读取浏览器 profile、不采集私有或受访问控制页面。
- 不自动发布内容。当前草稿工作流只准备本地包；现有文章脚本仍需要单独、人工确认的草稿或发布流程，平台图片上传适配器将在后续独立实现。
- 不把模型输出当作事实。报告中的模型整理内容必须结合 `sources.jsonl` 和原始产物复核。

这些限制是第一版的权限模型组成部分，而不是待绕过的障碍。后续 OCR、受审阅的草稿保存适配器和更丰富的数据工具会在同样的批准与审计机制下逐步加入。
