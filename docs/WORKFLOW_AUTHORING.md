# 工作流说明

## 存放位置与职责

可复用工作流定义位于仓库根目录的 `workflows/*.yaml`。它们是版本化、可审计的声明式文件；每次执行时，助手把 YAML 展开为任务工作区中的 `plan.json`，并记录定义文件的 SHA-256。任务恢复不会重新解释工作流，因此后续修改 YAML 不会改变已经创建的计划。

工作流只能引用已注册工具。工具实现保留在 Python 模块中，例如 `research_assistant/document_tools.py` 与 `research_assistant/content_tools.py`；Provider、密钥和浏览器登录态不属于工作流文件。

## 最小格式

```yaml
version: 1
name: example_workflow
summary: "处理任务：$task.goal"
requires:
  - input_files
steps:
  - id: read
    for_each: input_files
    tool: file.read
    description: "读取文件：$item"
    arguments:
      path: "$item"
```

支持的 `requires` 值：`input_files`、`sources`、`yaml_inputs`、`document_inputs`、`markdown_inputs`、`platform`。

支持的 `when` 值：`always`、`has_urls`、`has_input_files`、`no_sources`、`has_yaml_inputs`、`has_document_inputs`、`has_markdown_inputs`、`provider_configured`、`has_platform`。

支持的 `for_each` 集合：`urls`、`input_files`、`yaml_input_files`、`document_input_files`、`markdown_input_files`。

支持的模板值：`$item`、`$task.goal`、`$task.options.platform`。模板仅做值替换，不执行表达式。

## 权限边界

- 工具名和参数仍需通过 `ToolRegistry` 校验；YAML 不能绕过工具的参数白名单。
- 文件路径只能来自命令行显式 `--input` 指定的文件。
- 网页读取、浏览器采集、模型摘要和产物写入仍然逐步批准。
- 平台发布适配器必须作为经审阅插件实现 `DraftPublisher` 契约；内置 `content.prepare_draft` 永远只生成离线包。

## 新工作流验证

```bash
python agent.py run "验证我的工作流" --workflow example_workflow --input example.md
python agent.py status <task-id>
```

先检查生成的计划、工具、输入、风险和输出位置，再逐步批准执行。为新 YAML 增加离线单元测试，不要把第三方网页或平台登录作为 CI 前提。
