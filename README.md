# 自我修复的通用网页数据采集框架

> 一个自带自适应 + LLM 智能修复能力的通用网页数据采集框架。

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
  api_key: "YOUR_API_KEY"
  model: gemini-2.5-flash
  timeout: 10
```

完整字段、分页、浏览器与动作示例见 [configs/spider_template.yaml](configs/spider_template.yaml)。包含真实 API Key 的配置请保存为 `*.local.yaml`，不要提交。

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
configs/       站点与模板配置
core/          通用爬虫引擎与 LLM 修复模块
docs/          教育归档、技术文章与发布说明
scripts/       现场验证与文章草稿工具
tests/         离线夹具与单元测试
```

## 安全与贡献

- 不提交 API Key、Cookie、浏览器 profile、私有页面数据或采集结果。
- 贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。
- 本项目仅用于合法、获得授权的数据采集与工程研究；使用者须自行遵守适用法律、网站政策与速率限制。

## License

MIT
