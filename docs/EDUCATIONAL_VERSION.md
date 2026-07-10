# v1.0 Educational Archive / v1.0 教育归档

## Why the archive exists

`v1.0-educational` preserves an earlier, single-site proof of concept. It is a historical artifact for discussing dynamic page state, brittle selectors, configuration migration, and failure handling. It is not the active product line and is not a production-ready collector.

`v1.0-educational` is intentionally immutable. Its historical README uses older language and remains available for context; this document is the current explanation of how that material should be understood.

## 中文说明

`v1.0-educational` 是早期单站点实验的历史归档，不是当前产品线，也不是生产采集器。它保留的价值在于讨论动态页面状态、selector 漂移、配置迁移与失败处理。

归档中的代码不是通用的“热更新 JS 架构”，更不代表任何平台的当前兼容性。当前主线使用本地 [页面演化靶场](../labs/page_evolution/README.md) 展示同类工程问题：用自建 HTML 夹具复现结构变化，再观察受限的适配与候选方案流程。整个演示不访问第三方网站。

## What it demonstrates

The archived script illustrates one way a dynamic page can expose serialized state through an inline script. It is useful for studying why a page-specific extraction strategy can become fragile when markup, data shape, or client-side code changes.

It does **not** provide a generalized "hot-update JavaScript architecture." The current project does not claim that capability. We keep the example as an input to a more general conversation about extraction compatibility.

## The modern, safe teaching path

The active branch separates reusable execution behavior from configuration and task approval. For a reproducible demonstration, use the [Page Evolution Lab](../labs/page_evolution/README.md):

1. Run local HTML fixtures representing page versions.
2. Observe a configured selector succeed, then drift.
3. Observe the bounded adaptive and mocked selector-suggestion paths.
4. Review the result without contacting a third-party site.

The lab intentionally uses synthetic content. It is a compatibility exercise, not a target-site exercise.

## Boundaries

- Use the archive only with lawful, permissioned, public or local data.
- Do not use it to bypass access controls, collect non-public information, defeat rate limits, or violate a service's terms.
- Do not present historical, site-specific behavior as a promise of compatibility with any current platform.
- Keep target-specific logic out of the main product narrative and out of default demonstrations.
