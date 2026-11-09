# The Auto-Publishing Layer: How a Generated Video Actually Gets Posted

Research method note: `web_fetch`/`web_search` were unavailable in this environment; **all** evidence below was
gathered with `curl` + reading source from `codeload.github.com` tarballs. `raw.githubusercontent.com`,
`developers.google.com`, `googleapis.com`, `developers.tiktok.com`, `developers.facebook.com`, `youtube.com`,
`policies.google.com` are **network-blocked** from this sandbox. Consequence: platform API facts are sourced from
**third-party production code that hardcodes the real endpoints, quotas, and error strings**, not from primary docs.
Those are marked `[THIRD-PARTY SOURCE]`. Anything I could not confirm at all is marked `[UNVERIFIED]`.

---

## 0. TL;DR — the decision table

| Platform | Realistic auto-publish route | Hard blocker | Verdict |
|---|---|---|---|
| **YouTube** | Browser automation of `studio.youtube.com` **or** Data API v3 | API: unaudited project ⇒ forced `private`. Quota: **1,600 units/upload vs 10,000/day** = **6 uploads/day/project** | API viable at ≤6/day/channel with audits; browser automation is what practitioners actually ship |
| **TikTok** | Content Posting API (direct post) | Unaudited client: HTTP 403 `unaudited_client_can_only_post_to_private_accounts`, `SELF_ONLY` only | **Public posting is impossible pre-audit.** No workaround |
| **Instagram** | Graph API container→publish, **or** browser automation of the web composer | Graph API needs Business/Creator acct + **public HTTPS media URL**; browser route needs cookies + residential proxy | Graph API is the only durable route; browser route is fragile (`._ac76`) |
| **Douyin** | Official OpenAPI **or** browser automation (`creator.douyin.com`) | OpenAPI `aweme.share` is **default-off**, needs enterprise 营业执照 + stamped 入驻申请函. Browser route hits a **human SMS captcha mid-publish** | Browser route works per-post but **cannot be unattended at scale by design** |
| **Xiaohongshu** | Browser automation + **local signing server** (`x-s`/`x-t`) | No usable official publish API; signature generation is a reverse-engineered `window._webmsxyw` call | Works, but signature step fails often (retries 10×) |
| **视频号 (WeChat Channels)** | Browser automation | **Human 实名验证 via admin WeChat QR scan** can appear mid-publish | Same as Douyin: human in the loop |
| **Bilibili** | `biliup` (in `requirements.txt`) | Lower | The one genuinely scriptable target |

The recurring structural fact: **the "auto" in auto-publishing is real for about four platforms and fake for the
rest** — the fake part is a human solving a captcha or scanning a QR code at the exact moment of publish.

---

## 1. YouTube

### 1.1 The quota math is the whole story
From `Magerko/tiktok-to-youtube-bot`, [services/quota.py](https://github.com/Magerko/tiktok-to-youtube-bot):

```python
UPLOAD_COST = 1600
THUMBNAIL_COST = 50
LIST_COST = 1
DEFAULT_DAILY_QUOTA = 10_000
_RESET_TZ = ZoneInfo("America/Los_Angeles")

class QuotaExceeded(RuntimeError): ...
# "YouTube API квота исчерпана: {used}/{daily}, нужно ещё {units}. Сброс в полночь PT."
```

`videos.insert` = **1,600 units** against a **10,000 units/day/project** default. `10000 // 1600 = 6`.
**⇒ 6 uploads per day per Google Cloud project, resetting at midnight America/Los_Angeles.**
Setting a thumbnail costs 50, listing costs 1 — those are noise; the insert is the wall.

That repo's README ([README.md:120](https://github.com/Magerko/tiktok-to-youtube-bot/blob/main/README.md#L120))
states the operational consequence: separate Google Cloud project **per channel**, otherwise all channels share
one 10,000-unit pool. `[THIRD-PARTY SOURCE]`

### 1.2 The audit lock — why nobody uses the API
Documented verbatim in `dreammis/social-auto-upload`,
[uploader/youtube_uploader/main.py](https://github.com/dreammis/social-auto-upload/blob/main/uploader/youtube_uploader/main.py):
the module deliberately drives a browser instead of the Data API because

> videos uploaded through an *unaudited* API project are force-locked to private and cannot be made public
> without passing Google's compliance audit (which is impractical for personal/single-channel use).
> Browser automation has no such restriction.

Confirmed independently by the other repo's README: add your channel's Google account under **Test users**,
otherwise "Google форсит `privacyStatus=private`" (Google forces `privacyStatus=private`).

So the two locks compound: **quota caps you at 6/day, and the audit gate caps you at zero public uploads** until
Google manually approves an OAuth app whose only purpose is uploading to your own channel. `[THIRD-PARTY SOURCE]`

### 1.3 What the browser route actually does
`uploader/youtube_uploader/main.py` (333 lines), constants:

```python
STUDIO_URL = "https://studio.youtube.com"
UPLOAD_URL = "https://www.youtube.com/upload"
VISIBILITY = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}
```

- `channel="chrome"` (real Chrome, not bundled Chromium).
- Login is **interactive**: polls `"/channel/" in page.url` for up to 600 iterations (~10 min), i.e. the operator
  logs in by hand once; the session is then persisted.
- Optional `YT_PROXY` from `conf.py`, e.g. `http://127.0.0.1:7890`, with the source comment explaining why:
  "国内直连 youtube.com 会超时，且 patchright 启的 chromium 不吃系统代理" (direct connection times out, and the
  Chromium launched by patchright ignores the system proxy).

### 1.4 API route details, for completeness
`youtube.videos().insert(part="snippet,status", body=body, media_body=media)`,
`_CHUNK_SIZE = 1024*1024*4` (4 MiB resumable chunks), `YT_TITLE_MAX = 100`, `YT_DESC_MAX = 5000`,
`YT_TAGS_MAX = 30`, default `privacy_status = "public"` via `os.getenv("YT_PRIVACY")`.
Retry set is `{500,502,503,504}` with `_MAX_RETRIES = 5` and exponential backoff — **`quotaExceeded` (403) is
excluded on purpose** because it is not transient. `_ensure_shorts()` force-appends `#Shorts`.
Scopes: `https://www.googleapis.com/auth/youtube.upload` + `.../youtube.readonly`, via `InstalledAppFlow`
(OAuth client type must be **Desktop app**, consent screen User Type **External**).

---

## 2. TikTok Content Posting API

Source: `ParishSynthMind/tiktok-content-posting-client`,
[tt_poster/tiktok.py](https://github.com/ParishSynthMind/tiktok-content-posting-client/blob/main/tt_poster/tiktok.py).
Base URL `https://open.tiktokapis.com`.

```python
CREATOR_INFO_PATH = "/v2/post/publish/creator_info/query/"
DIRECT_INIT_PATH  = "/v2/post/publish/video/init/"
INBOX_INIT_PATH   = "/v2/post/publish/inbox/video/init/"
STATUS_PATH       = "/v2/post/publish/status/fetch/"
CANCEL_PATH       = "/v2/post/publish/cancel/"
SCOPES = ("user.info.basic", "video.publish", "video.upload")
PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE","MUTUAL_FOLLOW_FRIENDS","FOLLOWER_OF_CREATOR","SELF_ONLY")
```
OAuth: `/v2/oauth/token/`, `/v2/oauth/revoke/`. Scopes must be **comma-joined** — the source notes
"TikTok wants commas, not spaces". If `video.publish` is missing from the consent screen, the library raises
`ScopeError`; this is the most common setup mistake.

### 2.1 The audit gate, in TikTok's own error vocabulary
```python
"unaudited_client_can_only_post_to_private_accounts": UnauditedApp,
"scope_not_authorized": ScopeError,
"privacy_level_option_mismatch": PrivacyMismatch,
"spam_risk_too_many_posts": ...,
"rate_limit_exceeded": ...,
```
Plain-English message the library surfaces:
> TikTok has not audited this app yet, so it can only post as SELF_ONLY to an account set to private.

Note two things that are easy to miss and matter operationally:
- `"spam_risk_*"` errors are returned with **HTTP 200** — a naive `if resp.status_code == 200: success` client
  will silently treat a rejected post as published.
- `"spam_risk_too_many_posts"` **exists as a first-class error**, i.e. volume-based suppression is built into the
  posting API itself, not just an anti-bot heuristic.

`tt_poster/publisher.py` blocks the request client-side before it is even sent:
```python
if mode == "direct" and not audited and settings.get("privacy_level") != "SELF_ONLY":
    block(...)  # "approve again with --privacy SELF_ONLY, or set TIKTOK_APP_AUDITED=true"
```
`--privacy` is "required for direct posts, no default". The batch stops entirely on exit codes 3,4,6,7.

### 2.2 Chunking rules (`tt_poster/video.py`)
Each chunk **5 MB – 64 MB**; the last chunk carries the remainder up to **128 MB**; files <5 MB go as one chunk;
**1–1000 chunks**; `total_chunk_count = video_size // chunk_size`; uploads via `Content-Range: bytes a-b/size` PUTs.

**Bottom line: a fresh developer app cannot post publicly to TikTok. At all.** It can post `SELF_ONLY` to a
private account, which means nobody sees it. There is no code-level workaround — `TIKTOK_APP_AUDITED=true` is a
lie you tell the library, not TikTok.

---

## 3. Instagram

Two entirely different code paths exist in the wild, and the popular automation repo picked the fragile one.

### 3.1 The Graph API route (durable)
Source: `remc0r/instagram-publish`, [instagram-publish-master](https://github.com/remc0r/instagram-publish).

```python
DEFAULT_API_VERSION = "v26.0"
HOSTS = {"instagram": "https://graph.instagram.com", "facebook": "https://graph.facebook.com"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
CAROUSEL_MIN, CAROUSEL_MAX = 2, 10
```
Flow: `POST {user_id}/media` → poll `GET {container_id}?fields=status_code,status` until `FINISHED`
(raises on `ERROR`/`EXPIRED`) → `POST {user_id}/media_publish` with `data={"creation_id": container}`.
Videos use `media_type: "REELS"`; carousels use `media_type: "CAROUSEL"` + `children` + per-child
`is_carousel_item: "true"`; stories use `media_type: "STORIES"` and accept **no caption**. An
`is_ai_generated: "true"` flag is supported.

**The operational catch:** media must be **public HTTPS URLs** — "Instagram downloads them itself". Redirects are
not followed. Publishing is deliberately **not auto-retried** to avoid duplicate posts. So your pipeline is:
generate video → upload to public object storage → hand Instagram the URL. The generated file never goes straight
from disk to Instagram.

Prerequisites (README): Instagram **Business or Creator** account (personal accounts cannot use the API at all).
- **Option A (Instagram Login):** app needs the **Instagram Tester** role; the tester accepts at
  `instagram.com/accounts/manage_access`; then "API setup with Instagram business login → Generate token". Token
  valid **60 days**, refreshable after 24h, each refresh adds another 60 days.
- **Option B (Facebook Login):** IG account linked to a Facebook Page; scopes `instagram_basic`,
  `instagram_content_publish`, `pages_show_list`. Explorer tokens last ~1 hour.
- Resolve the IG user id: `GET me/accounts?fields=instagram_business_account`.

Useful nuance: the Meta app may stay in **Development mode** for posting to your own account — you do not need
Meta's app review. This is the single most important practical difference from TikTok/YouTube.

Corroborated by `ralphcrisostomo/n8n-nodes-meta-publisher`:
`nodes/MetaPublisher/lib/ig.ts` lines 71 & 231 (`/{igUserId}/media`, `/{igUserId}/media_publish`),
`lib/fb.ts` lines 144 & 223 (`/{pageId}/video_reels`), `lib/ops.ts:357`
(`https://graph.instagram.com/refresh_access_token`).

### 3.2 What `social-auto-upload` actually does for Instagram — NOT the Graph API
`uploader/meta_uploader/insgraph_uploader/main.py` drives the **private web composer**:
```python
INSTAGRAM_HOME_URL     = "https://www.instagram.com/"
INSTAGRAM_MEDIA_INPUT  = 'input[type="file"][accept*="video/mp4"]'
INSTAGRAM_PRIMARY_ACTION = '._ac76 [role="button"]:not(:has(svg))'
INSTAGRAM_PROXY = os.environ.get("SAU_INSTAGRAM_PROXY")
```
Identity is verified by comparing the `ds_user_id` cookie between stored `storage_state` and live cookies — a
cheap but real session-integrity check. Failures dump evidence to
`.local/instagram-errors/<uuid4hex>/page.png` + `page.html`.

Repo-wide grep for `graph.facebook`, `graph.instagram`, `access_token`, `www.googleapis.com` across all `*.py`:
**0 matches.** The most-starred Chinese social auto-upload project uses **zero official publishing APIs**. That is
the single most informative fact in this whole investigation — it tells you what actually works in practice versus
what is documented.

Note also `._ac76` and `img.css-wemwzq` (XHS) — **hashed CSS class names**. Instagram regenerates these on every
frontend deploy. Any selector-based uploader is one deploy away from breaking, which is why these repos ship
frequent fix commits and why the "demo works" video you saw is not evidence of durability.

### 3.3 Facebook Page
`uploader/meta_uploader/facebook_page_uploader/main.py` uses Meta Business Suite's UI:
`META_BUSINESS_URL = "https://business.facebook.com/latest/composer/"`, `META_PLATFORMS = {"facebook-page"}`,
env `SAU_FACEBOOK_PAGE_PROXY`, dismisses geo-guidance cards via
`[data-surface$="lib:GeoGuidanceCard"]`, detects logout via `/login`, `checkpoint`, `two_step_verification`.

---

## 4. Douyin (抖音)

### 4.1 Official OpenAPI — the permission wall
From `developer.open-douyin.com` pages that render server-side (the API reference bodies are client-rendered and
**could not be retrieved** — see §8):
- 投稿能力 `aweme.share` is **默认关闭 (default off)** and requires a console application plus a `抖音数据权限`.
- 入驻 requires an **enterprise entity**: 营业执照 (business license) or 事业单位法人证书, plus a 入驻申请函
  stamped with the company 公章, scanned and uploaded. Individuals cannot get publish access.
- Two developer types: 自研开发者 (self-developed) / 系统服务商 (system service provider).
- 企业号认证 unlocks 企业号开放能力.
- 运营规范 §4 penalties escalate: 沟通警告 → 限期整改 (permissions unusable during rectification) → **账号封禁**.
- Entity limits: 小程序 5 (个体工商户) / 10 (企业); 移动/网站应用 ≤10 per 超管账号.

### 4.2 Browser automation — and the human captcha
`uploader/douyin_uploader/main.py` (1,261 lines) is the most instructive file in the repo.

**Platform actively rejects synthetic clicks.** The uploader dispatches a full event sequence because a plain
`.click()` is not accepted:
```python
_native_click(page, locator)  # pointerover, pointerenter, pointerdown, mousedown, pointerup, mouseup, click
# source comment: 抖音身份验证组件（uc_verification_component）只认这整套事件，不认单纯 click
```

**The human-in-the-loop step, mid-publish:**
```python
# detects: input[placeholder*="验证码"], input[type="tel"]
# clicks:  get_by_text("获取验证码")
# reads code from: os.path.join(BASE_DIR, "verify_code.txt")
# or interactive: input("请输入抖音短信验证码（直接回车可稍后重试）")
# submits via: div.uc-ui-verify_sms-verify_button:has-text("验证")
```
This is a **real SMS sent to a real phone**, required *after* the video is uploaded and *before* publish completes.
`verify_code.txt` exists so an external process can inject the code — i.e. the architecture is "automation pauses,
human relays a code". **Unattended scale is broken by design, and that is a platform decision, not a bug.**

Other mechanics:
- Upload progress poll watches `[class^="long-card"] div:has-text("重新上传")` and
  `div.progress-div > div:has-text("上传失败")`.
- Overlays removed by
  `document.querySelectorAll('.shepherd-element, .shepherd-modal-overlay-container, [class*="mention-wrapper"]').forEach(e => e.remove())`.
- Publish: `page.get_by_role("button", name="发布", exact=True)` then
  `wait_for_url("https://creator.douyin.com/creator-micro/content/manage**", timeout=3000)`.
- Session persisted with `context.storage_state(path=self.account_file)`; `connect_over_cdp(cdp_url)` at line 275
  lets it attach to an already-running real Chrome.
- Class `DouYinNote` handles image-text notes.

### 4.3 The AI self-declaration is mandatory
```python
if not self.declaration:
    self.declaration = "内容由AI生成"
```
and it **refuses to publish if the declaration cannot be set**:
```python
if not await self.set_self_declaration(page, self.declaration):
    raise RuntimeError(f"自主声明「{self.declaration}」设置失败，拒绝继续发布")
```
Same pattern across platforms — this is a legal compliance surface, not a feature:
- 视频号: `apply_original_statement()` selects `含AI生成内容` from the 「视频标注」dropdown, with the comment
  "依平台合规要求如实选「含AI生成内容」" and a warning that it is a *different field* from 声明原创.
- 支付宝生活号: radio group defaults to `内容无需标注`; code checks
  `label.antd5-radio-wrapper` with text `内容由AI生成`.
- 大鱼号: `DAYU_SOURCE_TYPES = ("无需标注", "AI生成", "虚构演绎", "营销信息", "转载", "个人观点", "不适宜未成年人")`.

**Implication for an AI video pipeline:** you are legally and contractually required to label the output as
AI-generated on every Chinese platform, and the mature tooling enforces this by *refusing to publish* otherwise.

---

## 5. Xiaohongshu (小红书)

No usable official publish API in this stack. `uploader/xhs_uploader/main.py` requires a **local signing server**:
```python
XHS_SERVER = "http://127.0.0.1:11901"          # conf.example.py
sign(uri, data, a1, web_session) -> POST f"{XHS_SERVER}/sign" -> {"x-s": ..., "x-t": ...}
```
Local fallback `sign_local` launches Chromium, sets the `a1` cookie on `.xiaohongshu.com`, `sleep(2)`, then:
```python
context_page.evaluate("([url, data]) => window._webmsxyw(url, data)", [uri, data])
```
Retries **10×**, then raises: `"重试了这么多次还是无法签名成功，寄寄寄"` (a candid admission that signature
acquisition frequently fails).

The newer browser version, `uploader/xiaohongshu_uploader/main.py` (849 lines):
```python
XHS_DEFAULT_CREATOR_BASE_URL = "https://creator.xiaohongshu.com"   # env override SAU_XHS_CREATOR_BASE_URL
XHS_PUBLISH_SUCCESS_URL_PATTERN = "**/publish/success?**"
```
- QR login via `div[class*='login-box']` and `img.css-wemwzq`.
- `_js_click_by_text` brute-force clicks matching leaf elements **and 4 ancestor levels**, because
  "小红书很多可点项文字在 `<span class="d-text">` 里，pointer-events 常被禁用" — the platform sets
  `pointer-events: none` specifically to break automation.
- **标签上限为 10 个，超过会导致死循环卡住发布** (tags capped at 10; more causes an infinite loop).
- Publish click: `button:has-text("发布")`, wait for the success URL with `timeout=3000`, retried in a loop with
  `asyncio.sleep(0.5)`.

---

## 6. 视频号 (WeChat Channels) and the rest

`uploader/tencent_uploader/main.py` (1,244 lines) — same human-in-the-loop pattern:
```python
dialog = page.locator("div.weui-desktop-dialog__wrp:visible").filter(has_text="实名验证").first
# screenshots the QR to <account>_verification_qr.png
tencent_logger.warning(f"需要管理员微信扫码完成实名验证: {output_path}")
# polls until the dialog disappears; raises TimeoutError("等待视频号管理员实名验证超时")
```
An admin must scan a WeChat QR with a phone, mid-publish. Also note the form-iframe problem:
"发布表单 iframe 只加载空壳（Vue 不挂载），页面上没有任何 input[type=file]" — so they navigate to the home page
and click `button.weui-desktop-btn` with text `发表视频` instead.
Scheduling: `set_schedule_time_tencent` clicks through a calendar by typing the month/day/hour.

- **Bilibili:** `biliup==0.4.98` in `requirements.txt` — a real library, the one clean API-ish target.
- **支付宝生活号:** striking counter-evidence on stealth — "不能用 `set_init_script(stealth)` —— 实验证明 stealth
  会阻止支付宝登录 iframe 注入" and "会阻止支付宝内容创作平台(qiankun 微应用)渲染". **Anti-detection is not free**;
  it can break legitimate page rendering. Its captcha detection keys on
  `captcha.alipay.com/api/v1/captcha/verify` and `publishShortVideo` network events, with
  `no_submit_signal = not (saw_captcha_verify or saw_publish_submit)`.
- **虎扑:** inline anti-webdriver script `Object.defineProperty(navigator, 'webdriver', { get: () => undefined })`.

---

## 7. The anti-bot stack, and why it is a treadmill

`utils/base_social_media.py`:
```python
def set_init_script(context):
    context.add_init_script(Path(BASE_DIR / "utils/stealth.min.js"))
```
- `utils/stealth.min.js` — **180,462 bytes**, generated by `puppeteer-extra`'s `extract-stealth-evasions`,
  header dated **Mon, 10 Jun 2024**. Patches `navigator.webdriver`, fabricates `window.chrome.app`.
  **A static 2024 script is already stale** against 2026 detection.
- Playwright → **`patchright`** (a stealth-patched Playwright fork) across 43 Python files;
  `pyproject.toml` pins `patchright==1.58.2`. Legacy `from playwright` imports survive in
  `uploader/tk_uploader/main.py`, `main_chrome.py`, `uploader/xhs_uploader/main.py`, `myUtils/auth.py`.
  Notably **`patchright` is absent from `requirements.txt`** — only in `pyproject.toml`.
- `myUtils/login.py`: `'--disable-blink-features=AutomationControlled',  # 核心防爬屏蔽：去掉 window.navigator.webdriver 标签`
- `connect_over_cdp` to attach to a genuine user Chrome — the strongest available signal, since it inherits a
  real profile.
- Residential-proxy escape hatches: `SAU_INSTAGRAM_PROXY`, `SAU_FACEBOOK_PAGE_PROXY`, `SAU_FACEBOOK_PROXY`,
  `YT_PROXY`.
- `cf_clearance==0.31.0` is a declared dependency — i.e. Cloudflare clearance handling is part of the baseline.
- Headless is a config flag: `LOCAL_CHROME_HEADLESS = True`, `DEBUG_MODE = True`, `LOCAL_CHROME_PATH`.

**Read this as an arms race with published casualty counts.** Every mechanism above is a response to a platform
countermeasure that already shipped: Douyin's `uc_verification_component` rejecting `.click()`; XHS's
`pointer-events: none`; TikTok's `spam_risk_too_many_posts`; SMS captchas; QR 实名验证. The direction of travel is
clear — defenses are getting *semantic* (is this a human?) rather than *technical* (is this a bot?), and the
automation side is answering with stolen human sessions.

---

## 8. Scheduling

### 8.1 What the reference repo does: platform-side scheduling, not cron
`docs/CLI.md` documents `--schedule "YYYY-MM-DD HH:MM"`, which is **passed to the platform's own scheduler UI**
rather than held in a local queue. Coverage is uneven, and the repo documents the gaps honestly:
- Supported: 抖音, 快手, 小红书, 视频号, Bilibili, 大鱼号 (大鱼号: Beijing time, **2h–7 days**, 5-min steps).
- Not supported: 百家号, 支付宝, 微博, 虎扑.
- 知乎: schedule is marked **实验性** (30 min–48 h).
- Instagram web implementation: **立即发布 only, no `--schedule`.**
- YouTube: `--visibility ∈ {public, unlisted, private}` + `--playlist` + `--schedule` via the Studio Schedule control.
- Facebook Page: `--schedule` uses `Asia/Shanghai`.

`uploader/base_video.py` enforces a lead time:
```python
MIN_SCHEDULE_LEAD_TIME = timedelta(hours=2)
# raises: "定时发布时间必须大于当前时间 2 小时"
```
So `DOUYIN_PUBLISH_STRATEGY_SCHEDULED if args.schedule else ..._IMMEDIATE`. Platform-side scheduling is
**strictly better** than local cron: no machine has to be awake at publish time, and the post is emitted by the
platform's own trusted client rather than by a script the platform may flag.

### 8.2 External orchestrators (cron / n8n / Airflow)
`n8n-nodes-meta-publisher` is a real n8n node package, so n8n is a live pattern for the Meta platforms.
`db/createTable.py` (SQLite, tables `user_info` and `file_records`) has **no scheduling or quota columns** — the
reference repo genuinely does not keep a local job queue. `schedule==1.2.2` is a declared dependency but the
scheduling of record is platform-side. `[UNVERIFIED]` — I did not find a production Airflow DAG doing
multi-platform publish in any repo I downloaded; treat Airflow/cron as an orchestration choice layered on top,
not as an observed implementation.

Practical shape for a real pipeline: **cron/n8n decides *when to attempt*; the platform decides *when to publish*.**
The local scheduler's job is only to respect the per-platform lead-time window and the daily quota.

---

## 9. Legal / TOS position, and what actually happens to accounts

### 9.1 What I could and could not verify
YouTube's, TikTok's, Meta's, and Google's policy pages are **all network-blocked** from this sandbox
(`policies.google.com`, `developers.tiktok.com`, `developers.facebook.com`, `youtube.com` all unreachable). Bing
was region-censored and returned navigation pages instead of query matches. So I **cannot quote TOS text
verbatim**, and you should not treat the following as legal advice. What I *can* do is report what the shipping
code encodes, which is evidence of how platforms behave in practice.

### 9.2 The ToS position, as encoded in real tooling
Every platform in this stack prohibits automated posting except through its approved API surface. The evidence
that this is enforced rather than theoretical is in the error strings and UI behaviors themselves:

1. **YouTube** forces `privacyStatus=private` for unaudited API clients — a *technical* enforcement of "you must be
   reviewed to publish publicly". Bypassing it via browser automation bypasses the review, i.e. it is a knowing
   circumvention, not a gray area.
2. **TikTok** returns `unaudited_client_can_only_post_to_private_accounts` — the platform explicitly refuses
   public reach to unreviewed automation. `spam_risk_too_many_posts` and `rate_limit_exceeded` are first-class
   API errors, meaning **volume-triggered suppression is an intentional, built-in mechanic**.
3. **Douyin** 运营规范 §4 defines escalating penalties ending in **账号封禁** (account ban), and inserts SMS
   verification mid-publish — a control whose entire purpose is to distinguish a human operator from a bot farm.
4. **Xiaohongshu** sets `pointer-events: none` on interactive text and requires a reverse-engineered signature
   (`window._webmsxyw`) — active technical countermeasures against exactly this automation.
5. **视频号** requires an admin's WeChat QR scan for 实名验证 — identity binding at publish time.

### 9.3 What actually happens — the honest read
- **Small scale, single account, human present:** this works. Thousands of people run it. The dominant failure
  mode is not a ban, it is **breakage**: a hashed CSS class changes (`._ac76`, `img.css-wemwzq`), an iframe stops
  mounting, the 2024-era `stealth.min.js` gets fingerprinted, and your pipeline stops publishing until you patch
  it. Plan for maintenance, not for fire-and-forget.
- **Shadowbanning:** the platforms do not publish shadowban mechanics, and I could not verify any specific case
  from a primary source. `[UNVERIFIED]` What *is* verifiable is the class of control that produces the effect:
  `spam_risk_too_many_posts` (TikTok), 限期整改 (Douyin, permissions unusable during rectification), and Douyin's
  **forced AI-content self-declaration**. A post that is published but labeled AI-generated and de-prioritized is
  functionally a shadowban from the uploader's point of view, and it is achievable with zero TOS violation at all —
  which is the more likely outcome for an AI video pipeline than a dramatic ban.
- **At scale (many accounts, many posts/day):** this is where it breaks, and the platform designs it to. Douyin's
  SMS captcha and 视频号's QR 实名验证 require a human at publish time; TikTok's audit gate caps you at
  `SELF_ONLY`; YouTube caps you at 6 uploads/day/project *and* forces private. Running N accounts means N phone
  numbers, N WeChat identities, N Google Cloud projects, and N residential proxies — and that combination
  (many accounts + many proxies + automation) is precisely the signature that anti-abuse systems are built to
  cluster on. **"The demo works" and "this survives at scale" are separated by exactly the controls documented in
  §2.1, §4.2, and §6.**
- **The AI-labeling point cuts both ways.** Complying with AI-disclosure rules (which the tooling forcibly does)
  is the right thing and is required — but on Chinese platforms it also means every post carries a machine-readable
  marker that a ranking system could plausibly deprioritize. You cannot opt out of the labeling to avoid that;
  the repos literally refuse to publish without it.

---

## 10. Confidence ledger

**High confidence (read directly from source, exact strings/numbers):** YouTube quota constants and the
private-lock rationale; TikTok endpoint paths, scope names, `SELF_ONLY` audit gate, chunk rules; Instagram Graph
API container→publish flow and account prerequisites; Douyin browser mechanics, SMS captcha, forced AI
declaration; XHS signing server requirement; 视频号 QR verification; the Playwright→patchright migration and
stealth script provenance; the `--schedule` per-platform coverage matrix; Douyin OpenAPI permission model
(`aweme.share` default-off, enterprise-only onboarding, §4 penalty ladder).

**Third-party-sourced, not primary docs (`[THIRD-PARTY SOURCE]`):** all platform API facts above, since
`developers.*` and `googleapis.com` were network-blocked. These come from production code that hardcodes the real
values and from repo READMEs, which is strong but not authoritative evidence.

**`[UNVERIFIED]`:** Douyin `/video/create/` and related OpenAPI request/response bodies (the docs SPA is
client-rendered — URL path, `docs_api/resource`, query params, and `page-data.json` all returned the same Gatsby
shell); Xiaohongshu official open-platform API access requirements; verbatim ToS text for any platform; specific
documented ban/shadowban case outcomes; Airflow/cron multi-platform publish as an observed production pattern.

**Meta-lesson:** the most-starred project in this space uses **zero official publishing APIs**. When the 16.3k-star
MIT reference implementation would rather reverse-engineer `x-s` signatures and inject 180KB of stealth JS than
call a documented endpoint, the documented endpoint is not the answer to your question. It is the *audit process
behind* the endpoint that is the real product — and that process exists to make sure a human, accountable to the
platform, is on the other end.
