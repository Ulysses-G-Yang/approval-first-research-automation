# 淘宝攻防演练 - 反爬虫研究项目

> 本项目用于淘宝/天猫平台的反爬虫攻防演练，探索在合规边界内自动化采集公开商品信息的技术方案。

## 项目背景

针对淘宝/天猫的反爬机制进行系统性研究，测试各类自动化采集手段的可行性与局限性，为安全评估提供技术参考。

## 核心脚本

| 脚本 | 功能 | 说明 |
|------|------|------|
| `extract_prices.py` | SKU 价格采集 | 自动登录态，批量提取商品 SKU 价格信息，输出 Excel |
| `extract_lite.py` | 精简采集版 | 仅提取 itemId/标题/销量，保留全部防封策略，1000 条自动停止 |
| `batch_collect.py` | 批量品类采集 | 品类词 × 补贴词组合搜索，自动翻页收集商品 ID |
| `collect_ids.py` | 搜索页 ID 收集 | 小批量翻页收集商品 ID，关键词随机轮换 |
| `search_api.py` | CDP 接管提取 | 通过 Chrome DevTools Protocol 接管浏览器，提取搜索结果中的商品 ID |
| `anonymous_probe.py` | 未登录态探测 | 探测淘宝 API 在未登录状态下的数据边界 |
| `analyze_html.py` | HTML 分析 | 快速分析保存的搜索结果页 HTML 结构 |

## 技术栈

- **Python 3.10+**
- **Playwright** — 浏览器自动化，支持 CDP 接管真实浏览器
- **Pandas** — 数据处理与 Excel 输出
- **Windows Credential Manager** — 凭证管理

## 防封策略

本项目实现了一套完整的反检测机制：

- **状态机浏览模拟** — 模拟真实用户的浏览行为模式
- **登录心跳维持** — 保持会话活跃
- **时段控制** — 避开非活跃时段
- **每日采集上限** — 控制请求频率
- **同店铺检测** — 避免短时间内大量访问同一店铺
- **随机延迟** — 页间/操作间随机等待
- **弹窗自动关闭** — 处理广告和登录弹窗

## 数据输出

- `sku_prices_complete.xlsx` — 完整 SKU 价格数据
- `sku_prices_complete.csv` — CSV 格式备份
- `search_ids.json` — 搜索收集的商品 ID 列表
- `snapshots/` — 页面快照存档

## 使用方式

```bash
# 安装依赖
pip install playwright pandas
playwright install chromium

# 批量采集商品 ID
python batch_collect.py 10

# 精简模式采集（1000 条自动停止）
python extract_lite.py

# 从已有 ID 提取价格
python extract_prices.py
```

## 免责声明

本项目仅供安全研究和学习交流使用，不得用于任何违反平台服务条款或法律法规的用途。使用者需自行承担使用风险。

## License

MIT
