import asyncio
from playwright.async_api import async_playwright
import pandas as pd
import os
from datetime import datetime

WORK_DIR = r"D:\taobao_work"
OUTPUT_FILE = os.path.join(WORK_DIR, "sku_prices_complete.xlsx")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        # 找到当前打开的商品页
        target_page = None
        for pg in context.pages:
            if "item.taobao.com" in pg.url or "detail.tmall.com" in pg.url:
                target_page = pg
                break

        if not target_page:
            print("[FAIL] 未找到商品页，请先在浏览器打开一个淘宝/天猫商品")
            return

        print(f"[OK] 当前商品: {await target_page.title()}")

        data = await target_page.evaluate("""
            () => {
                const scripts = document.querySelectorAll('script');
                let target = null;
                scripts.forEach(s => {
                    const text = s.textContent || s.innerText || '';
                    if (text.includes('ICE_APP_CONTEXT') && text.includes('sku2info')) {
                        target = text;
                    }
                });
                if (!target) return null;
                const match = target.match(/var\\s+b\\s*=\\s*(\\{.*)/);
                if (!match) return null;
                let jsonStr = match[1];
                let braceCount = 0, endPos = 0;
                for (let i = 0; i < jsonStr.length; i++) {
                    if (jsonStr[i] === '{') braceCount++;
                    else if (jsonStr[i] === '}') { braceCount--; if (braceCount === 0) { endPos = i + 1; break; } }
                }
                try { return JSON.parse(jsonStr.substring(0, endPos)); } catch(e) { return null; }
            }
        """)

        if not data:
            print("[FAIL] 数据提取失败")
            return

        res = data["loaderData"]["home"]["data"]["res"]
        item = res["item"]
        seller = res["seller"]
        skus = res["skuBase"]["skus"]
        props = res["skuBase"].get("props", [])
        sku2info = res["skuCore"]["sku2info"]

        prop_map = {}
        for prop in props:
            for v in prop.get("values", []):
                prop_map[v["vid"]] = v["name"]

        item_title = item.get("title", "")
        has_subsidy = "补贴" in item_title
        subsidy_rate = 0.15 if "15%" in item_title else 0

        if not skus:
            fallback = sku2info.get("0") or sku2info.get("0;0")
            if fallback:
                skus = [{"skuId": "0", "propPath": "", "quantity": fallback.get("quantity", "")}]

        rows = []
        for sku in skus:
            sku_id = sku["skuId"]
            prop_path = sku.get("propPath", "")
            attrs = []
            if prop_path:
                for pair in prop_path.split(";"):
                    if ":" in pair:
                        pid, vid = pair.split(":")
                        attrs.append(prop_map.get(vid, vid))

            info = sku2info.get(sku_id, {})
            price_raw = info.get("price", {})
            page_price = price_raw.get("priceText", "") if isinstance(price_raw, dict) else str(price_raw)
            stock = info.get("quantity", "")

            try:
                price_num = float(page_price)
                original_price = round(price_num / (1 - subsidy_rate)) if has_subsidy and subsidy_rate else ""
                coupon_price = page_price if has_subsidy else ""
            except:
                original_price = ""
                coupon_price = ""

            rows.append({
                "itemId": str(item["itemId"]),
                "商品标题": item_title,
                "skuId": str(sku_id),
                "SKU名称": " + ".join(attrs) if attrs else "",
                "页面价(元)": page_price,
                "原价(元)": original_price,
                "券后价(元)": coupon_price,
                "优惠信息": f"国家补贴{int(subsidy_rate*100)}%" if has_subsidy else "",
                "库存": stock,
                "卖家昵称": seller.get("sellerNick", ""),
                "店铺名称": seller.get("shopName", ""),
                "爬取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        new_df = pd.DataFrame(rows)

        if os.path.exists(OUTPUT_FILE):
            old_df = pd.read_excel(OUTPUT_FILE)
            combined = pd.concat([old_df, new_df], ignore_index=True)
        else:
            combined = new_df

        combined.to_excel(OUTPUT_FILE, index=False)
        print(f"[OK] {len(rows)} 条SKU，累计 {len(combined)} 条")
        print(f"[FILE] {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())