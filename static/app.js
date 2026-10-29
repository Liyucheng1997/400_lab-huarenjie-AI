// 华人街 AI 助手前端：浏览器用访客自己的 API Key 直接调用 Claude，Key 和对话都只在浏览器里。
// 服务端只做华人街公开页面的抓取中转（/api/tool），不经手 Key，也不保存对话。
import Anthropic from 'https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.134.0/+esm';

const $ = (id) => document.getElementById(id);
// 在 GitHub Pages 上时，抓取接口在 huarenjie.liyucheng.me；本机运行时同源
const API_BASE = location.hostname.endsWith('github.io') ? 'https://huarenjie.liyucheng.me' : '';
const MAX_TOOL_ROUNDS = 8;
const KEY_NAME = 'claude_api_key';

const store = {
  get() {
    try { return localStorage.getItem(KEY_NAME) || sessionStorage.getItem(KEY_NAME) || ''; } catch { return ''; }
  },
  set(key, remember) {
    try {
      localStorage.removeItem(KEY_NAME);
      sessionStorage.removeItem(KEY_NAME);
      if (key) (remember ? localStorage : sessionStorage).setItem(KEY_NAME, key);
    } catch {}
  },
};

const MODES = {
  jobs: {
    hello: '你好，老板 👋',
    headline: '想招什么人，<span class="hl">直接问</span>。',
    lead: 'AI 会实时翻看华人街的招聘求职帖和意大利新闻，帮你筛出最合适的人和信息，还能帮你写好招聘帖。',
    placeholder: '描述你想招的人，或想找的工作…',
    examples: [
      '我在普拉托开服装厂，想招两个有经验的车工，最好有居留，最近有人在找工作吗？',
      '米兰附近有没有在找工作的副卡或收银员？',
      '罗马的餐馆现在招厨师一般开多少工资、包不包吃住？帮我写一条招聘帖。',
      '最近意大利有什么跟华人商家相关的新闻？',
    ],
  },
  house: {
    hello: '找房不用再一个个网站刷 🏠',
    headline: '想住哪里，<span class="hl">直接说</span>。',
    lead: 'AI 会翻看华人街的出租、分租、买卖帖，再帮你生成 Immobiliare、Casa.it、Idealista 带好筛选条件的链接，还能帮你写出租或求租帖。',
    placeholder: '说说城市、预算、房型，比如：米兰绿线附近单人间，400 欧以内…',
    examples: [
      '米兰地铁附近有没有单人间或床位出租？预算 400 欧以内。',
      '普拉托想租一套两房一厅，月租 800 以内，最好能办 residenza。',
      '佛罗伦萨现在买一套 70 平左右的房子大概多少钱？',
      '我在罗马有一间房要分租出去，帮我写一条出租帖。',
    ],
  },
};

let apiKey = store.get();
let mode = (() => { try { return localStorage.getItem('mode') || 'jobs'; } catch { return 'jobs'; } })();
if (!MODES[mode]) mode = 'jobs';
let history = []; // [{role, content}] 纯文本历史
let controller = null;

function toast(text, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind ? 'toast-' + kind : ''}`;
  el.textContent = text;
  $('toasts').appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 2600);
}

function renderKeyState() {
  const ok = !!apiKey;
  $('keyDot').classList.toggle('ok', ok);
  $('keyLabel').textContent = ok ? `API Key ···${apiKey.slice(-4)}` : '未设置 API Key';
  $('keyCard').style.display = ok ? 'none' : '';
}

function saveKey(input, remember) {
  const key = input.value.trim();
  if (!key.startsWith('sk-ant-')) { toast('API Key 应以 sk-ant- 开头', 'error'); return false; }
  apiKey = key;
  store.set(key, remember);
  input.value = '';
  renderKeyState();
  toast('API Key 已保存', 'success');
  $('prompt').focus();
  return true;
}

function showKeyCard() {
  $('keyModalInput').value = '';
  $('keyModal').showModal();
  $('keyModalInput').focus();
}

$('keyForm').addEventListener('submit', (e) => {
  e.preventDefault();
  saveKey($('keyInput'), $('keyRemember').checked);
});
$('keyModalForm').addEventListener('submit', (e) => {
  e.preventDefault();
  if (saveKey($('keyModalInput'), $('keyModalRemember').checked)) $('keyModal').close();
});
$('keyCancel').addEventListener('click', () => $('keyModal').close());
$('keyClear').addEventListener('click', () => {
  apiKey = '';
  store.set('', false);
  renderKeyState();
  $('keyModal').close();
  toast('已清除 API Key');
});
$('keyModal').addEventListener('click', (e) => { if (e.target === $('keyModal')) $('keyModal').close(); });
$('keyBtn').addEventListener('click', showKeyCard);

$('themeBtn').addEventListener('click', () => {
  const root = document.documentElement;
  const dark = root.dataset.theme !== 'dark';
  root.dataset.theme = dark ? 'dark' : 'light';
  try { localStorage.setItem('theme', root.dataset.theme); } catch {}
});

const prompt = $('prompt');
const welcomeEl = $('welcome');

function renderMode() {
  const m = MODES[mode];
  document.querySelectorAll('#modeSeg button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  $('hello').textContent = m.hello;
  $('headline').innerHTML = m.headline;
  $('lead').textContent = m.lead;
  prompt.placeholder = m.placeholder;
  $('examples').replaceChildren(...m.examples.map((text) => {
    const btn = el('button', 'example card', text);
    btn.addEventListener('click', () => { prompt.value = text; autosize(); ask(); });
    return btn;
  }));
}

function newChat() {
  if (controller) controller.abort();
  history = [];
  $('thread').replaceChildren(welcomeEl);
  renderKeyState();
  prompt.focus();
}
$('newChat').addEventListener('click', newChat);

document.querySelectorAll('#modeSeg button').forEach((btn) => {
  btn.addEventListener('click', () => {
    if (btn.dataset.mode === mode) return;
    mode = btn.dataset.mode;
    try { localStorage.setItem('mode', mode); } catch {}
    renderMode();
    newChat();
  });
});
function autosize() {
  prompt.style.height = 'auto';
  if (prompt.value) prompt.style.height = Math.min(prompt.scrollHeight, 180) + 'px';
}
prompt.addEventListener('input', autosize);
prompt.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(); }
});
$('composer').addEventListener('submit', (e) => { e.preventDefault(); ask(); });

marked.setOptions({ breaks: true, gfm: true });
function renderMarkdown(el, text) {
  el.innerHTML = DOMPurify.sanitize(marked.parse(text));
  el.querySelectorAll('a').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
}

// 代码块（AI 写好的帖子）加“复制”按钮
function addCopyButtons(root) {
  root.querySelectorAll('pre').forEach((pre) => {
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = '复制';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(pre.querySelector('code')?.innerText ?? pre.innerText);
        btn.textContent = '已复制';
        setTimeout(() => { btn.textContent = '复制'; }, 1600);
      } catch { toast('复制失败，请手动选择文字', 'error'); }
    });
    pre.append(btn);
  });
}

function scrollToBottom(force) {
  const s = $('scroller');
  if (force || s.scrollHeight - s.scrollTop - s.clientHeight < 160) s.scrollTop = s.scrollHeight;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function setBusy(busy) {
  const btn = $('sendBtn');
  btn.classList.toggle('stop', busy);
  btn.title = busy ? '停止' : '发送';
  btn.innerHTML = busy
    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="3"/></svg>'
    : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
}

function renderSources(card, items) {
  if (!items.length) return;
  const det = el('details', 'sources');
  det.append(el('summary', '', `检索到的帖子 / 文章（${items.length}）`));
  const list = el('div', 'src-list');
  for (const it of items) {
    const a = el('a', 'src');
    a.href = it.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.append(el('span', 'src-title', it.title));
    a.append(el('span', 'src-meta', [it.city, it.type, it.posted].filter(Boolean).join(' · ')));
    list.append(a);
  }
  det.append(list);
  card.append(det);
}

let configPromise = null;
function loadConfig() {
  configPromise ??= fetch(`${API_BASE}/api/config`).then((r) => {
    if (!r.ok) throw new Error(`抓取服务出错（${r.status}）`);
    return r.json();
  }).catch((err) => { configPromise = null; throw err; });
  return configPromise;
}

async function runTool(name, input, signal) {
  const resp = await fetch(`${API_BASE}/api/tool`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, input }),
    signal,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `抓取服务出错（${resp.status}）`);
  return data.result;
}

function toolStatus(name, args) {
  switch (name) {
    case 'property_search_links':
      return `正在生成 ${args.city_it || ''} 的 Immobiliare / Casa.it / Idealista 找房链接`;
    case 'get_post_link': return '正在获取华人街发帖页';
    case 'search_classifieds': {
      const parts = [args.city || '全意大利', args.job_type || args.house_type || args.category || '招聘求职'];
      if (args.post_type) parts.push(args.post_type);
      if (args.keywords?.length) parts.push('「' + args.keywords.join(' / ') + '」');
      return '正在检索华人街：' + parts.join(' · ');
    }
    case 'get_listing_detail': return `正在查看帖子 #${args.listing_id}`;
    case 'get_italy_news': return '正在获取意大利新闻';
    case 'read_article': return '正在阅读文章';
    default: return `正在调用 ${name}`;
  }
}

// 只回传纯文本历史（最近 12 条），保证 user / assistant 交替
function buildMessages() {
  const messages = [];
  for (const turn of history.slice(-12)) {
    const text = String(turn.content || '').trim();
    if (!text) continue;
    const last = messages[messages.length - 1];
    if (last && last.role === turn.role) last.content += '\n\n' + text;
    else messages.push({ role: turn.role, content: text });
  }
  while (messages.length && messages[0].role !== 'user') messages.shift();
  return messages;
}

function errorText(err) {
  if (err instanceof Anthropic.AuthenticationError) return 'API Key 无效，请检查后重新填写。';
  if (err instanceof Anthropic.PermissionDeniedError) return '这个 API Key 没有调用该模型的权限。';
  if (err instanceof Anthropic.RateLimitError) return '请求太频繁或额度不足，请稍后再试。';
  if (err instanceof Anthropic.APIConnectionError) return '连接不上 Claude API，请检查网络（国内需要能访问 api.anthropic.com）。';
  if (err instanceof Anthropic.APIError) return `Claude API 错误（${err.status}）：${err.message}`;
  return err.message || '网络出错，请重试';
}

async function ask() {
  if (controller) { controller.abort(); return; }
  const text = prompt.value.trim();
  if (!text) return;
  if (!apiKey) { toast('请先填写 Claude API Key', 'error'); showKeyCard(); return; }

  welcomeEl.remove();
  prompt.value = ''; autosize();

  const userMsg = el('div', 'msg msg-user');
  userMsg.append(el('div', 'bubble', text));
  const aiMsg = el('div', 'msg msg-ai');
  const card = el('div', 'card');
  const steps = el('ul', 'steps');
  const status = el('div', 'status');
  status.append(el('div', 'spinner'), el('span', '', '正在理解你的需求…'));
  const answer = el('div', 'answer');
  card.append(steps, status, answer);
  aiMsg.append(card);
  $('thread').append(userMsg, aiMsg);
  scrollToBottom(true);

  history.push({ role: 'user', content: text });
  controller = new AbortController();
  const { signal } = controller;
  setBusy(true);

  let full = '';
  let failed = false;
  let pending = false;
  const paint = () => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; renderMarkdown(answer, full); scrollToBottom(); });
  };
  const fail = (message) => {
    failed = true;
    card.append(el('div', 'error', message));
  };
  const showStep = (msg) => {
    steps.append(el('li', '', msg));
    status.style.display = '';
    status.lastChild.textContent = msg;
    scrollToBottom();
  };
  const sources = new Map();

  try {
    const cfg = await loadConfig();
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true, maxRetries: 2 });
    const today = new Date().toISOString().slice(0, 10);
    const system = `${cfg.system}\n\n${cfg.mode_hints[mode] || cfg.mode_hints.jobs}今天是 ${today}。`;
    const messages = buildMessages();

    let round = 0;
    for (; round < MAX_TOOL_ROUNDS; round++) {
      const stream = client.beta.messages.stream({
        model: cfg.model,
        max_tokens: 32000,
        system,
        tools: cfg.tools,
        messages,
        thinking: { type: 'adaptive' },
        output_config: { effort: 'medium' },
        cache_control: { type: 'ephemeral' },
        betas: ['server-side-fallback-2026-07-01'],
        fallbacks: 'default',
      }, { signal });
      stream.on('text', (delta) => {
        full += delta;
        if (full.trim()) status.style.display = 'none';
        paint();
      });
      stream.on('streamEvent', (ev) => {
        if (ev.type === 'content_block_start' && ev.content_block.type === 'server_tool_use') showStep('正在网页搜索华人街相关内容');
      });
      const response = await stream.finalMessage();

      if (response.stop_reason === 'refusal') { fail('这个问题模型无法回答，请换个问法。'); break; }
      messages.push({ role: 'assistant', content: response.content });
      if (response.stop_reason === 'pause_turn') continue;
      if (response.stop_reason !== 'tool_use') break;

      const results = await Promise.all(response.content.filter((b) => b.type === 'tool_use').map(async (block) => {
        showStep(toolStatus(block.name, block.input || {}));
        try {
          const result = await runTool(block.name, block.input || {}, signal);
          const found = [...(result.results || []), ...(result.links || []).map((l) => ({ title: `${l.site} 筛选结果`, url: l.url, type: '找房网站' }))];
          for (const item of found) if (item.url && !sources.has(item.url)) sources.set(item.url, item);
          return { type: 'tool_result', tool_use_id: block.id, content: JSON.stringify(result) };
        } catch (err) {
          if (err.name === 'AbortError') throw err;
          return { type: 'tool_result', tool_use_id: block.id, content: err.message, is_error: true };
        }
      }));
      messages.push({ role: 'user', content: results });
      full += '\n\n'; paint();
    }
    if (round === MAX_TOOL_ROUNDS) { full += '\n\n（检索轮数已达上限，以上为目前找到的结果。）'; paint(); }
    renderSources(card, [...sources.values()].slice(0, 60));
  } catch (err) {
    if (signal.aborted || err instanceof Anthropic.APIUserAbortError) fail('已停止');
    else {
      fail(errorText(err));
      if (err instanceof Anthropic.AuthenticationError) showKeyCard();
    }
  } finally {
    status.remove();
    renderMarkdown(answer, full);
    addCopyButtons(answer);
    if (full.trim() && !failed) history.push({ role: 'assistant', content: full.trim() });
    else history.pop(); // 失败的提问不进历史，保持 user / assistant 交替
    controller = null;
    setBusy(false);
    scrollToBottom();
  }
}

renderMode();
renderKeyState();
