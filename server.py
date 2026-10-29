"""华人街 AI 助手服务端：只做华人街公开页面的抓取中转。

浏览器用访客自己的 Claude API Key 直接调用 Claude，服务端不经手 Key，也不保存任何对话；
它只提供两样东西：/api/config（系统提示词和工具定义）和 /api/tool（执行抓取类工具）。

运行：python server.py  然后打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import scraper

MODEL = "claude-opus-5-5"
STATIC = Path(__file__).parent / "static"
# GitHub Pages 上的页面跨域调用这里；本机运行时页面和接口同源，不需要 CORS
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "https://liyucheng1997.github.io,http://127.0.0.1:8000,http://localhost:8000"
).split(",")
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "90"))  # 每个 IP 每分钟最多调用几次工具

app = FastAPI(title="华人街 AI 助手")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    max_age=86400,
)


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    # 页面和静态文件每次都向服务器确认，更新后不会看到旧版
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

JOB_TYPES = list(scraper.JOB_SUBCATEGORIES)

TOOLS = [
    {
        "name": "search_classifieds",
        "description": (
            "检索华人街（huarenjiewang.com）意大利站的分类广告，按最新发帖排序翻页抓取后按关键词过滤。"
            "招工、找工作、找人、找房、租房、看行情时都用它。每页约 30 条，pages=3 约覆盖最新 90 条。"
            "关键词是“任一命中即可”，请给出同义词（如 车工/车位/平车，副卡/收银，烫工/熨烫）。"
            "结果为空时可以换关键词、去掉关键词、放宽城市或增加 pages 再搜。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(scraper.CATEGORIES),
                    "description": "大类，默认 招聘求职",
                },
                "job_type": {
                    "type": "string",
                    "enum": JOB_TYPES,
                    "description": "招聘求职下的行业子类，可选；知道行业时填写能更精准",
                },
                "house_type": {
                    "type": "string",
                    "enum": list(scraper.HOUSE_SUBCATEGORIES),
                    "description": "房屋租售下的子类，可选（category 需为 房屋租售）。床位/搭铺/单人间属于 单间分租",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "在标题、摘要、工种标签里匹配的关键词，任一命中即可；可留空",
                },
                "city": {
                    "type": "string",
                    "description": "城市中文名，如 普拉托、米兰、罗马、佛罗伦萨、都灵、拿波里；不填为全意大利",
                },
                "post_type": {
                    "type": "string",
                    "enum": ["招聘", "求职", "提供", "寻找", "出售", "求购"],
                    "description": (
                        "供求类型。招聘求职类：老板找人查“求职”，想看同行开价或找工作查“招聘”。"
                        "房屋类：“提供”=有房出租，“寻找”=求租，“出售”/“求购”=买卖"
                    ),
                },
                "pages": {"type": "integer", "minimum": 1, "maximum": 8, "description": "翻几页，默认 3"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_listing_detail",
        "description": "读取一条分类广告的完整内容（正文、工种、区域、电话等）。需要核实细节、工资、住宿、联系方式时使用。",
        "input_schema": {
            "type": "object",
            "properties": {"listing_id": {"type": "string", "description": "广告号，即 URL 中 information-id- 后的数字"}},
            "required": ["listing_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_italy_news",
        "description": "获取华人街意大利频道“新闻快递”的最新新闻标题和链接（来源新欧洲 xinouzhou.com）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}, "description": "按标题过滤，可留空"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "read_article",
        "description": "读取新闻文章或论坛帖子的正文。只支持 xinouzhou.com、huarenjie.com、huarenjiewang.com 的链接。",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "property_search_links",
        "description": (
            "生成意大利主流找房网站（Immobiliare.it、Casa.it、Idealista）带筛选条件的搜索链接。"
            "这些网站不能直接读取，用户点链接看实时房源。找房时都应附上这些链接。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city_it": {"type": "string", "description": "城市意大利文名，如 Prato、Milano、Roma、Firenze、Napoli、Torino"},
                "province_it": {"type": "string", "description": "所在省份意大利文名，城市本身是省会时可不填（Idealista 需要）"},
                "contract": {"type": "string", "enum": ["affitto", "vendita"], "description": "affitto=租，vendita=买"},
                "max_price": {"type": "integer", "description": "最高价格（欧元，租房为月租）"},
                "min_rooms": {"type": "integer", "description": "最少房间数（意大利算法：客厅也算一间，如 bilocale=2）"},
            },
            "required": ["city_it"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_post_link",
        "description": "获取华人街对应分类的发帖页链接。用户想发招聘、求职、出租、求租帖时，写好帖子后附上这个链接。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": JOB_TYPES + list(scraper.HOUSE_SUBCATEGORIES) + list(scraper.CATEGORIES),
                    "description": "尽量选具体子类，如 工厂/裁剪公司、餐馆酒吧业、整房出租、单间分租",
                },
            },
            "required": ["category"],
            "additionalProperties": False,
        },
    },
    {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 4,
        "allowed_domains": [
            "huarenjie.com", "huarenjiewang.com", "xinouzhou.com",
            "immobiliare.it", "casa.it", "idealista.it",
        ],
    },
]

SYSTEM = """你是“华人街 AI 助手”，服务在意大利的华人老板、打工者和租房买房的人。信息来源：华人街网站（huarenjie.com / huarenjiewang.com）的公开分类广告和论坛、新欧洲（xinouzhou.com）新闻，以及意大利找房网站 Immobiliare.it、Casa.it、Idealista（只能给搜索链接和网页搜索结果，不能直接读取房源列表）。通过工具实时检索。

通用规则：
- 先用工具查，再回答。不要凭记忆编造帖子、电话、价格、工资或新闻。工具没查到就如实说，并建议换个说法或扩大范围。
- 关键词要考虑同义词和意大利华人常用叫法。
- 对感兴趣的帖子，必要时用 get_listing_detail 看正文核实。
- 置顶帖不一定是最新的，帖子也可能已过期，提醒用户以原帖为准。

找工作 / 招人：
- 老板想招人：重点查“求职”帖（找工作的人），同时可查“招聘”帖，了解同城同行的开价和待遇，给出招聘建议。
- 找工作：查“招聘”帖，按城市、工种、待遇、包吃住等条件筛选。
- 常用叫法：车工/车位、副卡/收银、卖散、搭铺、熨烫/烫工、住家、有居留 等。

找房子：
- 先查华人街 房屋租售（category=房屋租售，可按 house_type 选 整房出租/单间分租/房屋出售/求购；post_type=提供 是有房出租，寻找 是求租）。
- 再用 property_search_links 生成 Immobiliare / Casa.it / Idealista 的筛选链接（预算、房间数换算成意大利标准：monolocale=1、bilocale=2、trilocale=3）。需要时可用 web_search 在这些网站上查行情或房源页。
- 常用叫法：床位/搭铺/单人间/分租、整租、一房一厅、两房一厅、直接房东/中介、包水电/包网、押金、residenza（户口）、地铁线（米兰红/绿/黄/紫/蓝线）。
- 提醒租房注意事项：要正规合同（contratto di locazione）并登记、押金一般 2-3 个月、能否办 residenza、谨防先付款后看房的骗局。

发帖：
- 你不能替用户发帖（发帖要登录用户自己的华人街账号）。用户想发帖时，帮他写好帖子：标题（30 字以内，把城市、职位/房型、关键待遇写进去）和正文（条件、待遇、联系方式占位），放在一个 ```text 代码块里方便复制，然后用 get_post_link 给出对应分类的发帖页链接，告诉他登录后粘贴即可。

回答格式（中文，简洁）：
- 先一句话结论，然后列出最相关的帖子：标题（链接）、城市、发布时间、要点（工资/住宿/价格/要求）。用 Markdown，链接用原帖 URL。
- 电话号码按网站显示原样给出，被打码的提示用户去原帖查看。
- 最后可给 1-3 条实用建议。"""

MODE_HINTS = {
    "jobs": "用户当前在“找工作 / 招人”页面。",
    "house": "用户当前在“找房子”页面。没说城市时先问一句或默认查全意大利；除了华人街，也给出意大利找房网站的筛选链接。",
}


async def run_tool(name: str, args: dict) -> dict:
    if name == "search_classifieds":
        return await scraper.search_classifieds(**args)
    if name == "get_listing_detail":
        return await scraper.get_listing_detail(args["listing_id"])
    if name == "get_italy_news":
        return await scraper.get_italy_news(args.get("keywords"))
    if name == "read_article":
        return await scraper.read_article(args["url"])
    if name == "property_search_links":
        return scraper.property_search_links(**args)
    if name == "get_post_link":
        return scraper.post_link(args["category"])
    raise ValueError(f"未知工具：{name}")


# 浏览器可以让服务端执行的工具（web_search 是 Claude 的服务端工具，不经过这里）
LOCAL_TOOLS = {t["name"] for t in TOOLS if "input_schema" in t}
_calls: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def rate_limited(ip: str) -> bool:
    now = time.monotonic()
    q = _calls[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return True
    q.append(now)
    return False


@app.get("/api/config")
async def config():
    return {"model": MODEL, "system": SYSTEM, "mode_hints": MODE_HINTS, "tools": TOOLS}


@app.post("/api/tool")
async def tool(request: Request):
    if rate_limited(client_ip(request)):
        return JSONResponse({"error": "请求太频繁，请稍后再试"}, status_code=429)
    body = await request.json()
    name, args = body.get("name"), body.get("input") or {}
    if name not in LOCAL_TOOLS or not isinstance(args, dict):
        return JSONResponse({"error": f"未知工具：{name}"}, status_code=400)
    try:
        return {"result": await run_tool(name, args)}
    except Exception as exc:  # 抓取失败交给模型处理
        return JSONResponse({"error": f"工具出错：{exc}"}, status_code=502)


@app.get("/api/health")
async def health():
    return {"ok": True}


# 放在所有 /api 路由之后：其余路径都是静态页面
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
