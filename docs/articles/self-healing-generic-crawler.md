# 我手搓了一个逐步成熟的 JS 自适应通用爬虫

网页采集最麻烦的通常不是第一次跑通，而是页面小改版后，输出悄悄变空或变错。
这个项目从单站教学脚本逐步演进为配置驱动的通用 Crawler：保留既有 Python
包名、`GenericSpider(config, network_policy=None)`、旧 YAML 和输出格式，同时把
原来分散的提取与修复逻辑收敛到一条正式管线。它不是推倒重写。

## 从“失败”变成可验证的工程事件

硬编码脚本容易把页面结构、字段语义和业务规则绑在一起。selector 漂移只是
一种失败；延迟 hydration、内嵌 JSON 路径变化、XHR schema 变化以及“非空但错误”
的候选同样危险。

Crawler 不承诺自动兼容任意网页。它把每个字段候选交给同一个 QualityGate，
失败或低置信度时可以记录 Repair Episode，并通过自建 fixture 重放来判断候选，
而不是让模型自己宣布成功。

## 唯一正式提取顺序

```text
configured selector
→ fallback_selectors
→ 已批准的历史修复
→ Scrapling adaptive
→ 可选 LLM candidate
→ empty
```

每一层结果都经过 QualityGate；不合格才进入下一层。LLM 默认关闭，只在确定性
路径失败或自适应结果低置信度时提出 selector 候选。候选会在当前页面重新提取
并校验，可以完成这一次显式启用模型的运行，但不会自动写回 YAML、历史修复库
或 Python 源码。

当前运行会按 URL、字段、失效 selector 和页面版本在内存中复用相同候选，避免
重复调用；DOM 版本变化就会重新评估。这个内存缓存不是批准，也不是持久化。

## JS 数据与证据底座

旧 YAML 无需修改。需要时可声明 `embedded_json` 或被动 `network_json` capture，
继续用现有 `source` 路径读取字段。网络 capture 只观察页面本来产生的首个匹配
GET、2xx、XHR/fetch JSON 响应，不主动请求接口。

Experience Store 默认关闭。显式启用后，SQLite 保存 Episode 与 append-only
事件，大对象进入 SHA-256 内容寻址目录；授权页面默认只保存结构特征和脱敏
片段。Cookie、Authorization、浏览器 profile、local/session storage 和密钥不会
进入存储。

## 能力边界

- 只在本地靶场、自有站点或明确授权材料上使用。
- 不提供访问控制、验证码、风控或检测机制规避。
- LLM/reviewer 意见不是正确性证明；重放指标和 QualityGate 才是硬裁决。
- v2.1 仍是 Alpha：它证明本地管线与经验数据底座，不代表任意真实网站兼容。

正式回归命令是：

```bash
crawler benchmark --json --check-baseline
```

开源地址：<https://github.com/Ulysses-G-Yang/approval-first-research-automation>
