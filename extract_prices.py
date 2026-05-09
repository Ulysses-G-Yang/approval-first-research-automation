import asyncio
from playwright.async_api import async_playwright
import pandas as pd
import os
from datetime import datetime
import random

# (platform, itemId) — "taobao" → item.taobao.com, "tmall" → detail.tmall.com
ITEM_LIST = [
    ("tmall", "1001275866697"), ("tmall", "1004255228845"), ("taobao", "1004595431163"),
    ("tmall", "1006987007484"), ("tmall", "1007270938748"), ("tmall", "1008285907014"),
    ("taobao", "1008970276352"), ("taobao", "1013417371270"), ("tmall", "1013521420810"),
    ("tmall", "1017188962489"), ("tmall", "1026184736743"), ("taobao", "1029535568329"),
    ("tmall", "1032355946810"), ("tmall", "1034629920198"), ("tmall", "1043153220823"),
    ("taobao", "614222592438"), ("tmall", "624594525059"), ("taobao", "750663940615"),
    ("taobao", "809602254748"), ("taobao", "857313431548"), ("tmall", "863664655605"),
    ("taobao", "889266769452"), ("taobao", "922031521389"), ("tmall", "922118791218"),
    ("tmall", "923305303835"), ("tmall", "930774230127"), ("taobao", "957462204359"),
    ("taobao", "961711032342"), ("taobao", "970912038001"), ("taobao", "974035901467"),
    ("tmall", "974309155913"), ("taobao", "976142446228"), ("tmall", "976409764381"),
    ("tmall", "980515537284"), ("tmall", "981429393541"), ("tmall", "985639707135"),
    ("tmall", "988349043678"), ("taobao", "991508277575"), ("tmall", "991765516042"),
    ("tmall", "992776673983"), ("taobao", "992983090316"), ("tmall", "994264685360"),
]

WORK_DIR = r"D:\taobao_work"
OUTPUT_FILE = os.path.join(WORK_DIR, "sku_prices_complete.xlsx")

async def extract_one_item(page, item_id, platform="tmall"):
    domain = "item.taobao.com" if platform == "taobao" else "detail.tmall.com"
    url = f"https://{domain}/item.htm?id={item_id}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2, 4))
    except:
        print(f"  [[FAIL]] 页面加载失败")
        return None

    data = await page.evaluate("""
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
        print(f"  [[FAIL]] 非天猫商品或数据格式不同，跳过")
        return None

    try:
        res = data["loaderData"]["home"]["data"]["res"]
        item = res["item"]
        seller = res["seller"]
        skus = res["skuBase"]["skus"]
        props = res["skuBase"].get("props", [])
        sku2info = res["skuCore"]["sku2info"]
    except KeyError as e:
        print(f"  [[FAIL]] 数据结构不匹配: 缺少 {e}")
        return None

    prop_map = {}
    for prop in props:
        for v in prop.get("values", []):
            prop_map[v["vid"]] = v["name"]

    item_title = item.get("title", "")
    has_subsidy = "补贴" in item_title
    subsidy_rate = 0.15 if "15%" in item_title else 0

    rows = []

    # 当 skuBase.skus 为空时，尝试 sku2info["0"] 单 SKU 兜底
    if not skus:
        fallback = sku2info.get("0") or sku2info.get("0;0")
        if fallback:
            skus = [{"skuId": "0", "propPath": "", "quantity": fallback.get("quantity", "")}]

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

    return rows


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = await context.new_page()

        existing_ids = set()
        if os.path.exists(OUTPUT_FILE):
            old_df = pd.read_excel(OUTPUT_FILE)
            existing_ids = set(old_df["itemId"].astype(str))
            print(f"[LOAD] 已加载 {len(old_df)} 条历史数据，{len(existing_ids)} 个商品")
        else:
            old_df = pd.DataFrame()

        all_rows = []
        success_count = 0
        skip_count = 0
        fail_count = 0

        for idx, (platform, item_id) in enumerate(ITEM_LIST):
            if item_id in existing_ids:
                print(f"\n[{idx+1}/{len(ITEM_LIST)}] {platform}/{item_id} - 已采集，跳过")
                skip_count += 1
                continue

            print(f"\n[{idx+1}/{len(ITEM_LIST)}] 正在采集 {platform}/{item_id}...")
            rows = await extract_one_item(page, item_id, platform)

            if rows:
                all_rows.extend(rows)
                existing_ids.add(item_id)
                success_count += 1
                print(f"  [OK] 提取到 {len(rows)} 条 SKU")

                if old_df.empty:
                    combined = pd.DataFrame(all_rows)
                else:
                    combined = pd.concat([old_df, pd.DataFrame(all_rows)], ignore_index=True)
                combined.to_excel(OUTPUT_FILE, index=False)
            else:
                fail_count += 1

            delay = random.uniform(10, 20)
            print(f"  [WAIT] 等待 {delay:.1f} 秒...")
            await asyncio.sleep(delay)

        print(f"\n{'='*50}")
        print(f"[SUMMARY] 本次采集完毕")
        print(f"   成功: {success_count} 个商品")
        print(f"   跳过: {skip_count} 个（已存在）")
        print(f"   失败: {fail_count} 个")
        print(f"   本次新增: {len(all_rows)} 条 SKU")
        print(f"[FILE] {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())