# 华人街 AI 助手 · v1.0

网页里填入自己的 Claude API Key，就能用自然语言检索意大利华人街的信息。两个页面：

- **找工作**：招聘 / 求职帖、同行开价、意大利新闻。老板直接问"普拉托有没有在找工作的车工"即可。
- **找房子**：华人街整租 / 分租 / 买卖帖，加上 Immobiliare.it、Casa.it、Idealista 带好筛选条件（城市、预算、房间数）的搜索链接。

两个页面都能让 AI 写好招聘帖 / 出租帖（一键复制），并给出华人街对应分类的发帖页，登录自己的账号后粘贴发布。

## 在线使用

- 在线页面：https://liyucheng1997.github.io/400_lab-huarenjie-AI/ （备用：https://huarenjie.liyucheng.me ）
- 限制：huarenjiewang.com 拒绝服务器（机房 IP）的访问，在线版读不到华人街实时招聘 / 租房帖，只能用新闻、找房网站链接、发帖页和 Claude 网页搜索。要完整功能请本机运行。
- 填入自己的 Claude API Key（在 https://console.anthropic.com/settings/keys 创建）即可提问。Key 只保存在你的浏览器里，提问由浏览器直接发给 Claude；服务器只负责读取华人街公开页面，不经手 Key，也不保存对话。

## 本机运行

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python server.py
```

打开 http://127.0.0.1:8000 ，填入 API Key（在 https://console.anthropic.com/settings/keys 创建）。
想让同一局域网的其他电脑访问：`set HOST=0.0.0.0` 后再启动。

## 工作原理

- `scraper.py`：实时抓取 huarenjiewang.com 意大利站分类广告（按最新发帖翻页，本地按关键词/城市/供求过滤）、帖子详情，以及华人街意大利频道的新闻（新欧洲 xinouzhou.com）。带 10 分钟缓存和并发限制。
- `server.py`：FastAPI 抓取中转。只提供 `/api/config`（系统提示词和工具定义）和 `/api/tool`（执行抓取类工具，按 IP 限流），不调用 Claude、不经手 Key、不存对话。允许跨域的来源由环境变量 `ALLOWED_ORIGINS` 指定。
- `static/`：前端页面，用 Anthropic 官方 JS SDK 在浏览器里直接调用 Claude Opus 5.5 + 工具调用：模型自己决定搜什么关键词、看哪些帖子详情，最后汇总作答；另配了只限这几个网站的网页搜索作补充。页面放在 GitHub Pages 时，抓取请求发往 `huarenjie.liyucheng.me`，本机运行时发往同源。

## 部署

- `.github/workflows/pages.yml`：推送 main 后把 `static/` 发布到 GitHub Pages。
- `.github/workflows/deploy-server.yml`：`server.py` / `scraper.py` 等改动后，通过受限 SSH 钥匙让服务器执行 `/usr/local/bin/deploy-huarenjie`（源码 `deploy/deploy-huarenjie.sh`）：拉取最新代码、装依赖、重启 `app-huarenjie` 服务并做健康检查。
- 服务器上：程序在 `/opt/apps/huarenjie`（systemd 服务 `app-huarenjie`，端口 4400），nginx 反向代理 `huarenjie.liyucheng.me` + certbot HTTPS（`deploy/nginx-huarenjie.conf`）。

## 注意

- 只读取公开页面，被网站打码的电话不会去破解，需到原帖查看。
- Immobiliare / Casa.it / Idealista 有反爬保护（直接请求返回 403），所以不抓取，只生成筛选链接，并用 Claude 的网页搜索查这些网站被搜索引擎收录的页面。
- 不自动发帖：发帖需要登录华人街账号，AI 只负责写帖子和给出发帖页。
- 网站改版后列表解析可能失效，修改 `scraper.parse_listing_page` 即可。
- 每次提问会消耗访客自己的 API 额度（检索类问题通常要调用几轮工具）。
