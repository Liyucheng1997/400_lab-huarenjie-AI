"""华人街 AI 助手后端：网页填入 Claude API Key，AI 实时检索华人街信息后作答。

运行：python server.py  然后打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import scraper

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 8
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="华人街 AI 助手")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    # 页面和静态文件每次都向服务器确认，更新后不会看到旧版
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
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


def tool_status(name: str, args: dict) -> str:
    if name == "property_search_links":
        return f"正在生成 {args.get('city_it', '')} 的 Immobiliare / Casa.it / Idealista 找房链接"
    if name == "get_post_link":
        return "正在获取华人街发帖页"
    if name == "search_classifieds":
        parts = [
            args.get("city") or "全意大利",
            args.get("job_type") or args.get("house_type") or args.get("category") or "招聘求职",
        ]
        if args.get("post_type"):
            parts.append(args["post_type"])
        if args.get("keywords"):
            parts.append("「" + " / ".join(args["keywords"]) + "」")
        return "正在检索华人街：" + " · ".join(parts)
    if name == "get_listing_detail":
        return f"正在查看帖子 #{args.get('listing_id')}"
    if name == "get_italy_news":
        return "正在获取意大利新闻"
    if name == "read_article":
        return "正在阅读文章"
    return f"正在调用 {name}"


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def build_messages(history: list[dict]) -> list[dict]:
    """前端只回传纯文本历史（最近 12 条），保证 user / assistant 交替。"""
    messages: list[dict] = []
    for turn in history[-12:]:
        role, text = turn.get("role"), str(turn.get("content", "")).strip()
        if role not in ("user", "assistant") or not text:
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


async def chat_stream(api_key: str, history: list[dict], mode: str):
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
    messages = build_messages(history)
    if not messages or messages[-1]["role"] != "user":
        yield sse({"type": "error", "message": "没有收到问题"})
        return

    system = SYSTEM + f"\n\n{MODE_HINTS.get(mode, MODE_HINTS['jobs'])}今天是 {date.today().isoformat()}。"
    sources: dict[str, dict] = {}

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            async with client.beta.messages.stream(
                model=MODEL,
                max_tokens=32000,
                system=system,
                tools=TOOLS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield sse({"type": "delta", "text": event.text})
                    elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                        yield sse({"type": "status", "text": "正在网页搜索华人街相关内容"})
                response = await stream.get_final_message()

            if response.stop_reason == "refusal":
                yield sse({"type": "error", "message": "这个问题模型无法回答，请换个问法。"})
                return

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                yield sse({"type": "status", "text": tool_status(block.name, block.input)})
                try:
                    result = await run_tool(block.name, block.input)
                    found = result.get("results", []) + [
                        {"title": f"{link['site']} 筛选结果", "url": link["url"], "type": "找房网站"}
                        for link in result.get("links", [])
                    ]
                    for item in found:
                        if item.get("url"):
                            sources.setdefault(item["url"], item)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                except Exception as exc:  # 抓取失败交给模型处理
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"工具出错：{exc}",
                        "is_error": True,
                    })
            messages.append({"role": "user", "content": tool_results})
            yield sse({"type": "delta", "text": "\n\n"})
        else:
            yield sse({"type": "delta", "text": "\n\n（检索轮数已达上限，以上为目前找到的结果。）"})

        yield sse({"type": "sources", "items": list(sources.values())[:60]})
        yield sse({"type": "done"})

    except anthropic.AuthenticationError:
        yield sse({"type": "error", "message": "API Key 无效，请检查后重新填写。"})
    except anthropic.PermissionDeniedError:
        yield sse({"type": "error", "message": "这个 API Key 没有调用该模型的权限。"})
    except anthropic.RateLimitError:
        yield sse({"type": "error", "message": "请求太频繁或额度不足，请稍后再试。"})
    except anthropic.APIStatusError as exc:
        yield sse({"type": "error", "message": f"Claude API 错误（{exc.status_code}）：{exc.message}"})
    except anthropic.APIConnectionError:
        yield sse({"type": "error", "message": "连接不上 Claude API，请检查网络（国内需要能访问 api.anthropic.com）。"})


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    api_key = (body.get("api_key") or "").strip()
    if not api_key.startswith("sk-ant-"):
        return JSONResponse({"error": "请先填写有效的 Claude API Key（以 sk-ant- 开头）"}, status_code=400)
    return StreamingResponse(
        chat_stream(api_key, body.get("history") or [], body.get("mode") or "jobs"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/meta")
async def meta():
    return {
        "model": MODEL,
        "categories": list(scraper.CATEGORIES),
        "job_types": JOB_TYPES,
        "cities": list(scraper.CITY_AREAS),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
