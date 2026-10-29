"""华人街（意大利）公开页面抓取：分类广告列表 / 详情、新闻列表 / 正文。

只读公开页面，带缓存和并发限制，避免给对方网站造成压力。
"""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

BASE = "https://www.huarenjiewang.com/city/yidali"
NEWS_HOME = "https://yidali.huarenjie.com/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# 分类广告大类（意大利站 catid）
CATEGORIES = {
    "招聘求职": 108,
    "房屋租售": 218,
    "店铺买卖": 219,
    "生活服务": 220,
    "跳蚤市场": 221,
    "贸易": 85,
}

# 招聘求职下的行业子类
JOB_SUBCATEGORIES = {
    "餐馆酒吧业": 109,
    "按摩保健业": 110,
    "美容美甲/理发": 111,
    "老外工": 112,
    "店员收银/卖散": 113,
    "司机/送货": 114,
    "工厂/裁剪公司": 115,
    "保姆/月嫂/带小孩": 116,
    "家教/学校/培训": 117,
    "其他行业": 118,
    "工厂搭铺": 226,
    "文员/经理/销售": 353,
    "临时工": 355,
    "留学生招聘": 431,
}

# 房屋租售下的子类
HOUSE_SUBCATEGORIES = {
    "整房出租": 222,
    "单间分租": 223,
    "房屋出售/求购": 224,
    "身份证/暂住证": 225,
}

FABU_URL = f"{BASE}/fabu/{{catid}}.html"


def post_link(category: str) -> dict:
    """华人街发帖页（需登录华人街账号后才能发）。"""
    catid = (
        JOB_SUBCATEGORIES.get(category)
        or HOUSE_SUBCATEGORIES.get(category)
        or CATEGORIES.get(category)
    )
    if not catid:
        return {"error": f"未知分类：{category}"}
    return {
        "category": category,
        "url": FABU_URL.format(catid=catid),
        "note": "需要先登录华人街账号；把 AI 写好的标题和正文复制进去即可发布。",
    }


# 意大利主流找房网站：生成带筛选条件的搜索链接（这些网站有反爬保护，不直接抓取）
def _slug(text: str) -> str:
    text = text.strip().lower()
    for a, b in (("à", "a"), ("è", "e"), ("é", "e"), ("ì", "i"), ("ò", "o"), ("ù", "u"), ("'", "-")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def property_search_links(
    city_it: str,
    contract: str = "affitto",
    max_price: int | None = None,
    min_rooms: int | None = None,
    province_it: str | None = None,
) -> dict:
    city = _slug(city_it)
    province = _slug(province_it or city_it)
    sale = contract == "vendita"

    immo_q = []
    if max_price:
        immo_q.append(f"prezzoMassimo={int(max_price)}")
    if min_rooms:
        immo_q.append(f"localiMinimo={int(min_rooms)}")
    immobiliare = f"https://www.immobiliare.it/{'vendita' if sale else 'affitto'}-case/{city}/"
    if immo_q:
        immobiliare += "?" + "&".join(immo_q)

    casa = f"https://www.casa.it/{'vendita' if sale else 'affitto'}/residenziale/{city}/"
    if max_price:
        casa += f"?priceMax={int(max_price)}"

    idealista = f"https://www.idealista.it/{'vendita' if sale else 'affitto'}-case/{city}-{province}/"
    if max_price:
        idealista += f"con-prezzo_{int(max_price)}/"

    return {
        "note": "这些网站有反爬保护，无法直接读取房源；以下是已带好筛选条件的搜索链接，点开即可看实时房源。"
        "Immobiliare 支持价格和房间数筛选；Idealista 链接需要 城市-省份 格式，小城市要填 province_it。",
        "links": [
            {"site": "Immobiliare.it", "url": immobiliare},
            {"site": "Casa.it", "url": casa},
            {"site": "Idealista", "url": idealista},
        ],
    }

# 城市 areaid（首次抓列表页时会自动补全）
CITY_AREAS: dict[str, int] = {
    "罗马": 29, "普拉托": 30, "拿波里": 31, "佛罗伦萨": 33, "博洛尼亚": 35,
    "西西里岛": 36, "帕多瓦": 38, "其他城市": 39, "全意大利": 40,
    "维罗纳": 235, "博尔扎诺": 236, "里米尼": 237, "安科纳": 238, "佩鲁贾": 239,
    "巴里": 241, "撒丁岛": 242, "威尼斯周边城市": 243, "卡拉布里亚": 250,
}
CITY_ALIASES = {
    "那不勒斯": "拿波里", "米兰": "米兰", "milano": "米兰", "roma": "罗马", "prato": "普拉托",
    "firenze": "佛罗伦萨", "napoli": "拿波里", "torino": "都灵", "bologna": "博洛尼亚",
    "padova": "帕多瓦", "verona": "维罗纳", "威尼斯": "威尼斯周边城市", "venezia": "威尼斯周边城市",
    "西西里": "西西里岛", "撒丁": "撒丁岛",
}

ALLOWED_ARTICLE_HOSTS = ("xinouzhou.com", "huarenjie.com", "huarenjiewang.com")

CACHE_TTL = 600  # 秒
_cache: dict[str, tuple[float, str]] = {}
_sem = asyncio.Semaphore(3)
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,it;q=0.8"},
            timeout=20,
            follow_redirects=True,
        )
    return _client


async def fetch(url: str) -> str:
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    async with _sem:
        resp = await _http().get(url)
        resp.raise_for_status()
        html = resp.text
    _cache[url] = (now, html)
    return html


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def resolve_city(city: str | None) -> tuple[str | None, int | None]:
    if not city:
        return None, None
    name = CITY_ALIASES.get(city.strip().lower(), CITY_ALIASES.get(city.strip(), city.strip()))
    for key, area in CITY_AREAS.items():
        if name == key or name in key or key in name:
            return key, area
    return name, None


def _learn_cities(soup: BeautifulSoup) -> None:
    for a in soup.select('a[href*="-areaid-"]'):
        m = re.search(r"-areaid-(\d+)\.html", a.get("href", ""))
        name = _clean(a.get_text())
        if m and name and name not in ("不限",) and len(name) <= 10:
            CITY_AREAS.setdefault(name, int(m.group(1)))


def _list_url(catid: int, area: int | None, page: int) -> str:
    path = f"category-catid-{catid}"
    if area:
        path += f"-areaid-{area}"
    if page > 1:
        path += f"-page-{page}"
    return f"{BASE}/{path}.html?orderby=begintime"


def parse_listing_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    _learn_cities(soup)
    items = []
    for li in soup.select("li"):
        link = li.select_one('a[href*="information-id-"]')
        if not link:
            continue
        m = re.search(r"information-id-(\d+)", link["href"])
        title_a = li.select_one('[class*="item-title"] a') or link
        title = _clean(title_a.get("title") or title_a.get_text())
        if not m or not title:
            continue
        desc = li.select_one('[class*="item-description"]')
        meta = li.select_one('[class*="item-meta"]')
        poster = li.select_one('[class*="item-poster"]')
        spans = poster.select("span") if poster else []
        item = {
            "id": m.group(1),
            "title": title,
            "summary": _clean(desc.get_text()) if desc else "",
            "city": _clean(meta.select_one(".xx1").get_text()) if meta and meta.select_one(".xx1") else "",
            "type": _clean(meta.select_one(".xx5").get_text()) if meta and meta.select_one(".xx5") else "",
            "tags": [_clean(t.get_text()) for t in meta.select(".mayi")] if meta else [],
            # 房型、中介/房东 等其他属性
            "attrs": [
                _clean(f.get_text())
                for f in (meta.find_all("font", recursive=False) if meta else [])
                if f.get("class", [""])[0] not in ("xx1", "xx5")
                and not f.select(".mayi")
                and _clean(f.get_text())
            ],
            "poster": _clean(spans[0].get_text()) if spans else "",
            "posted": _clean(spans[1].get_text()) if len(spans) > 1 else "",
            "pinned": bool(li.select_one('[class*="pinned"]')),
            "url": link["href"],
        }
        if not item["summary"] and not meta:
            # 非标准模板：退化为整行文本
            item["summary"] = _clean(li.get_text(" "))[:120]
        items.append(item)
    # 同一页里置顶帖可能重复出现
    seen, unique = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)
    return unique


def _matches(item: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = " ".join([item["title"], item["summary"], " ".join(item["tags"]), item["city"]]).lower()
    return any(k.lower() in hay for k in keywords if k)


async def search_classifieds(
    category: str = "招聘求职",
    job_type: str | None = None,
    keywords: list[str] | None = None,
    city: str | None = None,
    post_type: str | None = None,
    pages: int = 3,
    limit: int = 40,
    house_type: str | None = None,
) -> dict:
    """按最新发帖顺序翻页抓取，然后在本地按关键词 / 供求类型过滤。"""
    catid = (
        JOB_SUBCATEGORIES.get(job_type or "")
        or HOUSE_SUBCATEGORIES.get(house_type or "")
        or CATEGORIES.get(category, 108)
    )
    city_name, area = resolve_city(city)
    pages = max(1, min(int(pages or 3), 8))
    keywords = [k.strip() for k in (keywords or []) if k and k.strip()]

    if area is None and city_name:
        # 先抓一页学习城市列表，再试一次
        parse_listing_page(await fetch(_list_url(catid, None, 1)))
        city_name, area = resolve_city(city)

    urls = [_list_url(catid, area, p) for p in range(1, pages + 1)]
    pages_html = await asyncio.gather(*(fetch(u) for u in urls), return_exceptions=True)

    results, scanned, errors = [], 0, []
    seen = set()
    for url, html in zip(urls, pages_html):
        if isinstance(html, Exception):
            errors.append(f"{url}: {html}")
            continue
        for item in parse_listing_page(html):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            scanned += 1
            if area is None and city_name and city_name not in item["city"] and not _matches(item, [city_name]):
                continue
            # 不同分类里“供求”所在的字段位置不同，统一在属性里找
            if post_type and post_type not in " ".join([item["type"], *item["attrs"], *item["tags"]]):
                continue
            if _matches(item, keywords):
                results.append(item)

    return {
        "source": "华人街分类广告（意大利）",
        "category": job_type or house_type or category,
        "city": city_name or "全部城市",
        "city_filter_applied": area is not None,
        "keywords": keywords,
        "scanned_posts": scanned,
        "matched": len(results),
        "results": results[: max(1, min(int(limit or 40), 80))],
        "errors": errors,
    }


async def get_listing_detail(listing_id: str) -> dict:
    listing_id = re.sub(r"\D", "", str(listing_id))
    url = f"{BASE}/information-id-{listing_id}.html"
    soup = BeautifulSoup(await fetch(url), "html.parser")
    h1 = soup.select_one(".information_title h1") or soup.select_one("h1")
    fields = {}
    for li in soup.select(".extra_contact li"):
        label = li.select_one("span")
        if not label:
            continue
        key = _clean(label.get_text()).rstrip("：:")
        value = _clean(li.get_text(" ").replace(label.get_text(), "", 1))
        value = value.replace("点击查看完整号码", "").replace("@发送悄悄话", "").strip()
        if key:
            fields[key] = value
    body = soup.select_one(".maincon")
    if body:
        for junk in body.select(".contact_me, .tit_area, .status_bar, script, style"):
            junk.decompose()
    crumbs = [_clean(a.get_text()) for a in soup.select(".location a")]
    return {
        "id": listing_id,
        "url": url,
        "title": _clean(h1.get_text()) if h1 else "",
        "category_path": crumbs,
        "fields": fields,
        "content": _clean(body.get_text(" ")).split("首发网址")[0][:3000] if body else "",
        "note": "电话如显示为 ****，需在原帖页面点击查看完整号码。",
    }


async def get_italy_news(keywords: list[str] | None = None, limit: int = 20) -> dict:
    soup = BeautifulSoup(await fetch(NEWS_HOME), "html.parser")
    news, seen = [], set()
    for a in soup.select("a.hrj-news-item"):
        href = a.get("href", "")
        title = _clean(a.get("title") or a.get_text())
        if not href or href in seen or not title:
            continue
        seen.add(href)
        news.append({"title": title, "url": href})
    kws = [k for k in (keywords or []) if k]
    if kws:
        news = [n for n in news if any(k in n["title"] for k in kws)] or news
    return {"source": "华人街意大利频道·新闻快递（新欧洲）", "results": news[: max(1, min(limit, 40))]}


async def read_article(url: str) -> dict:
    host = urlparse(url).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_ARTICLE_HOSTS):
        return {"error": f"只允许读取以下网站的文章：{', '.join(ALLOWED_ARTICLE_HOSTS)}"}
    if "information-id-" in url:
        m = re.search(r"information-id-(\d+)", url)
        return await get_listing_detail(m.group(1))
    soup = BeautifulSoup(await fetch(url), "html.parser")
    h1 = soup.select_one("h1")
    body = (
        soup.select_one(".content-text")
        or soup.select_one(".article-main")
        or soup.select_one("#postlist .t_f")  # Discuz 论坛帖
        or soup.select_one("article")
        or soup.body
    )
    published = soup.select_one(".article-meta__item--published .article-meta__value")
    for junk in body.select("script, style, ins, iframe"):
        junk.decompose()
    return {
        "url": url,
        "title": _clean(h1.get_text()) if h1 else "",
        "published": _clean(published.get_text()) if published else "",
        "content": _clean(body.get_text(" "))[:6000],
    }
