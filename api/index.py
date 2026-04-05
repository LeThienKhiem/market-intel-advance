"""
Last30Days Research Intelligence — Full Online API v4
All sources run on Vercel + Local: Exa + ScrapeCreators (Reddit/TikTok/Instagram/X) + Bluesky + HN + Claude AI
"""
import os, json, time, asyncio, pathlib
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

IS_VERCEL = bool(os.environ.get("VERCEL"))

app = FastAPI(title="Last30Days Research API", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Keys ──
EXA_KEY = os.environ.get("EXA_API_KEY", "")
SC_KEY = os.environ.get("SCRAPECREATORS_API_KEY", "")
BSKY_HANDLE = os.environ.get("BSKY_HANDLE", "")
BSKY_PASS = os.environ.get("BSKY_APP_PASSWORD", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

SC_BASE = "https://api.scrapecreators.com"

class ResearchRequest(BaseModel):
    topic: str
    days: int = 30

def is_relevant(text, query, min_match=2):
    """Check if text contains at least min_match words from the query."""
    if not text or not query:
        return False
    words = [w.lower() for w in query.split() if len(w) > 2]
    text_lower = text.lower()
    matched = sum(1 for w in words if w in text_lower)
    return matched >= min(min_match, len(words))

class AnalyzeRequest(BaseModel):
    research_text: str
    topic: str
    analysis_type: str = "action_plan"


# ═══════════════════════════════════
#  SOURCE: Exa Web Search
# ═══════════════════════════════════
async def search_exa(query, days, num=12, domains=None, label="web"):
    if not EXA_KEY:
        return []
    try:
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload = {
            "query": query, "type": "auto", "numResults": num,
            "startPublishedDate": start,
            "contents": {"highlights": {"maxCharacters": 2000}}
        }
        if domains:
            payload["includeDomains"] = domains
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.post("https://api.exa.ai/search",
                headers={"x-api-key": EXA_KEY, "content-type": "application/json"}, json=payload)
            data = r.json()
            out = []
            for item in data.get("results", []):
                hl = item.get("highlights", [])
                out.append({
                    "source": label, "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "date": (item.get("publishedDate") or "")[:10],
                    "snippet": hl[0] if hl else "",
                })
            return out
    except:
        return []


# ═══════════════════════════════════
#  SOURCE: Reddit (ScrapeCreators)
# ═══════════════════════════════════
async def search_reddit(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/4.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v1/reddit/search",
                params={"query": query, "sort": "relevance", "timeframe": "month"}, headers=headers)
            data = r.json()
            posts = data.get("posts") or data.get("data") or []
            out = []
            for p in posts[:30]:
                title = p.get("title", "")
                text = p.get("selftext") or ""
                # Skip irrelevant results
                if not is_relevant(title + " " + text, query):
                    continue
                pts = p.get("ups") or p.get("score") or 0
                cmt = p.get("num_comments") or 0
                sub = p.get("subreddit", "")
                created = ""
                if p.get("created_utc"):
                    try:
                        created = datetime.utcfromtimestamp(p["created_utc"]).strftime("%Y-%m-%d")
                    except:
                        pass
                out.append({
                    "source": "reddit", "title": title,
                    "subreddit": f"r/{sub}" if sub else "",
                    "url": f"https://reddit.com{p.get('permalink', '')}" if p.get("permalink") else p.get("url", ""),
                    "date": created, "points": pts, "comments": cmt,
                    "snippet": text[:250],
                })
                if len(out) >= 15:
                    break
            return out
    except:
        return []


# ═══════════════════════════════════
#  SOURCE: X/Twitter (ScrapeCreators)
# ═══════════════════════════════════
async def search_x(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/4.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v1/twitter/search/tweets",
                params={"query": query, "sort_by": "relevance"}, headers=headers)
            data = r.json()
            tweets = data.get("tweets") or data.get("data") or data.get("results") or []
            out = []
            for t in tweets[:30]:
                likes = t.get("favorite_count") or t.get("likes") or 0
                rts = t.get("retweet_count") or t.get("retweets") or 0
                user = t.get("user") or t.get("author") or {}
                handle = user.get("screen_name") or user.get("username") or ""
                text = t.get("full_text") or t.get("text") or ""
                if not is_relevant(text, query):
                    continue
                tid = t.get("id") or t.get("tweet_id") or t.get("id_str", "")
                out.append({
                    "source": "x", "title": text[:120],
                    "url": f"https://x.com/{handle}/status/{tid}" if handle and tid else "",
                    "handle": f"@{handle}" if handle else "",
                    "likes": likes, "retweets": rts,
                    "snippet": text[:300],
                })
                if len(out) >= 15:
                    break
            return out
    except:
        return []


# ═══════════════════════════════════
#  SOURCE: TikTok (ScrapeCreators)
# ═══════════════════════════════════
async def search_tiktok(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/4.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v1/tiktok/search/keyword",
                params={"query": query, "sort_by": "relevance"}, headers=headers)
            data = r.json()
            items = data.get("search_item_list") or data.get("data") or []
            out = []
            for v in items[:20]:
                info = v.get("aweme_info") or v
                desc = (info.get("desc") or "").strip()
                if not desc or not is_relevant(desc, query):
                    continue
                stats = info.get("statistics") or {}
                plays = stats.get("play_count") or 0
                likes = stats.get("digg_count") or 0
                author = info.get("author") or {}
                out.append({
                    "source": "tiktok", "title": desc[:120],
                    "url": info.get("share_url") or "",
                    "handle": f"@{author.get('unique_id', '')}" if author.get("unique_id") else "",
                    "plays": plays, "likes": likes,
                    "snippet": desc[:250],
                })
                if len(out) >= 10:
                    break
            return out
    except:
        return []


# ═══════════════════════════════════
#  SOURCE: Instagram (ScrapeCreators)
# ═══════════════════════════════════
async def search_instagram(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/4.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v2/instagram/reels/search",
                params={"query": query}, headers=headers)
            data = r.json()
            reels = data.get("reels") or data.get("items") or data.get("data") or []
            out = []
            for reel in reels[:20]:
                views = reel.get("video_play_count") or reel.get("video_view_count") or 0
                likes = reel.get("like_count") or 0
                owner = reel.get("owner") or {}
                caption = reel.get("caption")
                text = (caption.get("text", "") if isinstance(caption, dict) else (caption or "")).strip()
                # Skip empty or irrelevant results
                if not text or not is_relevant(text, query):
                    continue
                code = reel.get("shortcode") or reel.get("code") or ""
                out.append({
                    "source": "instagram", "title": text[:120],
                    "url": f"https://instagram.com/reel/{code}" if code else "",
                    "handle": f"@{owner.get('username', '')}" if owner.get("username") else "",
                    "views": views, "likes": likes,
                    "snippet": text[:250],
                })
                if len(out) >= 10:
                    break
            return out
    except:
        return []


# ═══════════════════════════════════
#  SOURCE: Bluesky (AT Protocol)
# ═══════════════════════════════════
async def search_bluesky(query, days):
    if not BSKY_HANDLE or not BSKY_PASS:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            auth = await c.post("https://bsky.social/xrpc/com.atproto.server.createSession",
                json={"identifier": BSKY_HANDLE, "password": BSKY_PASS})
            token = auth.json().get("accessJwt")
            if not token:
                return []
            r = await c.get("https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                params={"q": query, "limit": 20, "sort": "top"},
                headers={"Authorization": f"Bearer {token}"})
            posts = r.json().get("posts", [])
            out = []
            for p in posts[:25]:
                rec = p.get("record", {})
                text = rec.get("text", "").strip()
                if not text or not is_relevant(text, query):
                    continue
                author = p.get("author", {})
                handle = author.get("handle", "")
                uri = p.get("uri", "")
                rkey = uri.split("/")[-1] if "/" in uri else ""
                likes = p.get("likeCount") or 0
                reposts = p.get("repostCount") or 0
                out.append({
                    "source": "bluesky", "title": text[:120],
                    "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else "",
                    "handle": f"@{handle}" if handle else "",
                    "date": (p.get("indexedAt") or "")[:10],
                    "likes": likes, "reposts": reposts,
                    "snippet": text[:250],
                })
                if len(out) >= 15:
                    break
            return out
    except:
        return []


# ═══════════════════════════════════
#  SOURCE: Hacker News (Algolia)
# ═══════════════════════════════════
async def search_hn(query):
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://hn.algolia.com/api/v1/search",
                params={"query": query, "tags": "story", "hitsPerPage": 10})
            out = []
            for h in r.json().get("hits", []):
                pts = h.get("points") or 0
                cmt = h.get("num_comments") or 0
                out.append({
                    "source": "hackernews", "title": h.get("title", ""),
                    "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
                    "date": (h.get("created_at") or "")[:10],
                    "points": pts, "comments": cmt,
                })
            return out
    except:
        return []


# ═══════════════════════════════════
#  ENDPOINTS
# ═══════════════════════════════════

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "sources": {
            "exa": bool(EXA_KEY), "scrapecreators": bool(SC_KEY),
            "bluesky": bool(BSKY_HANDLE and BSKY_PASS), "hackernews": True,
            "gemini": bool(GEMINI_KEY),
        },
        "keys_loaded": {
            "EXA": EXA_KEY[:8] + "..." if EXA_KEY else "MISSING",
            "SC": SC_KEY[:8] + "..." if SC_KEY else "MISSING",
            "BSKY": BSKY_HANDLE or "MISSING",
            "GEMINI": GEMINI_KEY[:10] + "..." if GEMINI_KEY else "MISSING",
        }
    }


@app.post("/api/research")
async def run_research(req: ResearchRequest):
    t0 = time.time()

    tasks = await asyncio.gather(
        search_exa(req.topic, req.days, 12, label="web"),
        search_exa(req.topic, req.days, 8,
            domains=["reuters.com", "bloomberg.com", "cnbc.com", "forbes.com", "bbc.com", "wsj.com", "techcrunch.com"],
            label="news"),
        search_reddit(req.topic, req.days),
        search_x(req.topic, req.days),
        search_tiktok(req.topic, req.days),
        search_instagram(req.topic, req.days),
        search_bluesky(req.topic, req.days),
        search_hn(req.topic),
        return_exceptions=True,
    )

    labels = ["web", "news", "reddit", "x", "tiktok", "instagram", "bluesky", "hackernews"]
    results_by_source = {}
    for label, result in zip(labels, tasks):
        results_by_source[label] = result if isinstance(result, list) else []

    # Dedupe by URL
    seen = set()
    all_results = []
    for label in labels:
        for item in results_by_source.get(label, []):
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                all_results.append(item)
            elif not url:
                all_results.append(item)

    elapsed = round(time.time() - t0, 1)
    counts = {}
    for r in all_results:
        s = r["source"]
        counts[s] = counts.get(s, 0) + 1

    sources_status = {}
    nice = {
        "web": "Exa Web", "news": "News", "reddit": "Reddit", "x": "X/Twitter",
        "tiktok": "TikTok", "instagram": "Instagram", "bluesky": "Bluesky", "hackernews": "Hacker News"
    }
    for label in labels:
        sources_status[nice.get(label, label)] = len(results_by_source.get(label, [])) > 0

    return {
        "status": "completed", "topic": req.topic, "research_time": elapsed,
        "days": req.days, "counts": counts, "total": len(all_results),
        "sources": sources_status, "results": all_results[:50]
    }


# ═══════════════════════════════════
#  AI ANALYSIS
# ═══════════════════════════════════

@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    analysis_prompts = {
        "action_plan": (
            "Create a SPECIFIC action plan for TBP Auto using ONLY the research data. DO NOT invent.\n\n"
            "## Tình hình thị trường (30-second summary)\n"
            "In 3 sentences: What is the data telling us? What's the dominant theme across sources?\n"
            "Quote 2 result titles as evidence.\n\n"
            "## 5 Hành động cụ thể\n"
            "For each action:\n"
            "- **Hành động**: One specific sentence — WHO at TBP Auto does WHAT by WHEN\n"
            "  (e.g., 'Sales team contacts 3 Australian distributors mentioned in data this week')\n"
            "- **Bằng chứng**: Quote the EXACT result: '[source] \"title\"' with engagement numbers if available\n"
            "- **Kết quả mong đợi**: What specific outcome this should produce\n"
            "- **Ưu tiên**: CAO / TRUNG BÌNH / THẤP\n\n"
            "RULES:\n"
            "- Actions must be SPECIFIC to TBP Auto's brake drum business, not generic business advice\n"
            "- 'Improve SEO' is BAD. 'Write comparison article: TBP brake drums vs [competitor from data]' is GOOD\n"
            "- 'Monitor competitors' is BAD. 'Track [specific company from data]'s pricing on [platform]' is GOOD\n"
            "- If data is insufficient for 5 actions, give fewer. NEVER pad with generic advice.\n\n"
            "## Kết luận\n"
            "3 bullets max, in Vietnamese. What's the ONE thing TBP Auto should do first and why?"
        ),
        "market_summary": (
            "Analyze market trends ONLY from the research data. DO NOT add outside knowledge.\n\n"
            "## Tổng quan dữ liệu\n"
            "- Total results, which sources had data, date range\n"
            "- One-line verdict: Is this market growing, stable, or declining based on the data?\n\n"
            "## Xu hướng chính (from actual results)\n"
            "3-5 trends. Each MUST:\n"
            "1. Name the specific trend (not generic like 'market is growing')\n"
            "2. Quote the exact result title as evidence\n"
            "3. Explain what it means for TBP Auto's brake drum / auto parts business specifically\n"
            "Example: 'Tariff pressure on Chinese imports — Source: [exact title] — This creates opportunity "
            "for TBP Auto as Vietnamese manufacturer to position as tariff-free alternative'\n\n"
            "## Cơ hội cho TBP Auto\n"
            "2-3 opportunities. Each must:\n"
            "- Reference a specific data point (title, engagement number, or trend from results)\n"
            "- Explain HOW TBP Auto can capture this opportunity (specific channel, product, or market)\n"
            "- Estimate urgency: act now vs monitor\n\n"
            "## Rủi ro\n"
            "2 risks visible in the data. Quote the source. Rate: HIGH / MEDIUM / LOW impact.\n\n"
            "## Số liệu đáng chú ý\n"
            "Any surprising engagement numbers? A Reddit post with 500+ upvotes? A news article trending?\n"
            "List the top 3 most-engaged results and what that tells us.\n\n"
            "If a section has no supporting data, write 'Không đủ dữ liệu' instead of guessing."
        ),
        "competitor_watch": (
            "Extract competitor intelligence DIRECTLY from the research data. DO NOT invent companies.\n\n"
            "## Đối thủ xuất hiện trong dữ liệu\n"
            "Scan EVERY title and snippet. List every company, brand, or manufacturer name that appears.\n"
            "For each:\n"
            "- **Company**: Name\n"
            "- **Mentioned in**: '[exact title from data]'\n"
            "- **Context**: What are they doing? (launching product, getting mentioned in comparison, pricing, etc.)\n"
            "- **Threat level for TBP Auto**: CAO / TRUNG BÌNH / THẤP — and why\n\n"
            "## Phân tích chi tiết đối thủ\n"
            "For the top 3 most-mentioned competitors:\n"
            "- What products/markets are they focused on?\n"
            "- What channels are they active on? (appeared on Reddit? News? Web?)\n"
            "- What's their apparent strategy based on the data?\n"
            "- TBP Auto's advantage or disadvantage vs this competitor\n\n"
            "## TBP Auto trong dữ liệu\n"
            "Is TBP Auto or tbpauto.com mentioned in ANY result? If yes, quote it and analyze sentiment.\n"
            "If no, this is itself a finding — TBP Auto has no organic visibility for this topic.\n\n"
            "## 3 Hành động phản ứng\n"
            "Each action must DIRECTLY respond to a specific competitor finding above.\n"
            "Format: 'Because [competitor] is doing [X from data], TBP Auto should [specific action]'\n\n"
            "If few competitors found, say so honestly. DO NOT fabricate competitor names."
        ),
        "seo_report": (
            "Extract SEO intelligence from the research results. Focus on ACTIONABLE keywords, not generic terms.\n\n"
            "## Long-tail Keywords (extracted from titles & snippets)\n"
            "Scan every title and snippet. Extract keywords in TWO tiers:\n\n"
            "### Tier 1: High-value long-tail keywords (3-6 words, buying intent)\n"
            "These are SPECIFIC phrases a buyer would search. Find 5-8.\n"
            "SKIP generic terms like 'aftermarket parts', 'supply chain', 'truck parts' — these are too broad.\n"
            "KEEP phrases like 'commercial vehicle brake drum supplier', 'heavy duty drum brake replacement', "
            "'OEM quality brake drum wholesale'.\n"
            "For each:\n"
            "- Exact phrase (or close variation found in data)\n"
            "- Source: [exact title where found]\n"
            "- Search intent: informational / commercial / transactional\n"
            "- Competition estimate: HIGH (generic) / MEDIUM / LOW (niche, long-tail)\n"
            "- TBP Auto fit: Can TBP rank for this? Why?\n\n"
            "### Tier 2: Topic clusters\n"
            "Group related keywords from the data into 3-4 clusters.\n"
            "Example: 'brake drum' cluster: [list related terms found in data]\n"
            "Each cluster = potential content pillar for tbpauto.com\n\n"
            "## Domains ranking for this topic\n"
            "List every unique domain from result URLs. Count results per domain.\n"
            "Flag: which are competitors? Which are media? Which are forums?\n"
            "Note: domains with 3+ results are dominating this topic.\n\n"
            "## Content Gaps for tbpauto.com\n"
            "3-5 topics covered in results that tbpauto.com likely doesn't have.\n"
            "For each: the result title that shows the gap + a specific blog title TBP Auto should write.\n"
            "Focus on topics with BUYING INTENT, not just informational.\n\n"
            "## Blog Ideas (with keyword targeting)\n"
            "5 blog posts for tbpauto.com. Each must:\n"
            "- Target a specific long-tail keyword from Tier 1 above\n"
            "- Be inspired by an actual result title from the data\n"
            "- Format: Target keyword: [X] | Original: [title] → TBP version: [your title]\n\n"
            "IMPORTANT: If a keyword doesn't appear in the data, don't include it. "
            "Generic terms without buying intent are USELESS for TBP Auto's SEO."
        ),
        "sales_brief": (
            "Create a Sales Intelligence Brief using ONLY the research data.\n\n"
            "## Tín hiệu mua hàng (Buying Signals from data)\n"
            "Scan ALL results for signals that someone is looking to buy, compare, or switch products.\n"
            "Look for: questions about pricing, comparison posts, 'best X for Y', supplier requests, RFQs.\n"
            "For each signal:\n"
            "- **Signal**: '[source] \"exact quote or title\"'\n"
            "- **What it means**: Why this matters for TBP Auto sales\n"
            "- **Sales action**: What should the sales rep do with this info?\n\n"
            "## Nỗi đau khách hàng (Customer Pain Points)\n"
            "From Reddit/X/forums: What are people complaining about?\n"
            "Quote actual posts with engagement numbers.\n"
            "For each: pain point → how TBP Auto solves it → suggested sales pitch (1 sentence)\n"
            "If no social data, say 'Không có dữ liệu từ mạng xã hội'.\n\n"
            "## Đối thủ trong dữ liệu\n"
            "Companies/brands mentioned. For each:\n"
            "- Where mentioned and in what context\n"
            "- How TBP Auto compares (based on data, not assumptions)\n"
            "- Sales objection this might create + how to handle it\n\n"
            "## Kịch bản bán hàng (5 Talking Points)\n"
            "Each must reference specific data:\n"
            "Format: 'Khi khách hỏi về [topic from data]: [specific response using data as evidence]'\n"
            "Example: 'Khi khách hỏi về tariffs: Theo [article title], Chinese imports face 25% tariff — "
            "TBP Auto as Vietnamese manufacturer offers tariff-free alternative'\n\n"
            "## Khách hàng tiềm năng\n"
            "Reddit users asking questions, companies mentioned needing suppliers, forums with active discussions.\n"
            "For each: who, where (URL if available), what they need.\n"
            "If none, say 'Không tìm thấy leads trực tiếp trong dữ liệu hiện tại'.\n\n"
            "DO NOT invent quotes, companies, or pain points not in the data."
        ),
        "marketing_ideas": (
            "Create a Marketing Intelligence Brief using ONLY the research data.\n\n"
            "## Top performing content (by engagement)\n"
            "List the 5 results with HIGHEST engagement (likes/comments/points/views).\n"
            "For each:\n"
            "- Title, source, exact engagement numbers\n"
            "- WHY it performed well (topic? format? timing? controversy?)\n"
            "- Lesson for TBP Auto: what can we learn from this content's success?\n\n"
            "## Ý tưởng nội dung cho TBP Auto\n"
            "Take 5 high-performing results and create TBP Auto versions.\n"
            "For each:\n"
            "- Original: [title] ([engagement]) on [platform]\n"
            "- TBP version: [specific content idea] on [recommended platform]\n"
            "- Target audience: [who this content reaches]\n"
            "- Estimated effort: LOW (1 day) / MEDIUM (1 week) / HIGH (ongoing)\n\n"
            "## Phân tích kênh (Channel Analysis)\n"
            "Count results per source. For each active channel:\n"
            "- Number of results and average engagement\n"
            "- Type of content that works there (questions? news? reviews? videos?)\n"
            "- Should TBP Auto be active here? YES/NO and specific reason\n"
            "- If YES: what type of content should TBP Auto post?\n\n"
            "## Xu hướng nội dung (Content Trends)\n"
            "What FORMAT is getting engagement? (video? long article? short post? comparison?)\n"
            "What ANGLE is trending? (price? quality? tariff? sustainability?)\n"
            "How should TBP Auto's content strategy reflect these trends?\n\n"
            "## 3 Quick Wins tuần này\n"
            "3 specific actions TBP Auto marketing can do THIS WEEK.\n"
            "Each must reference a specific result and be completable in 1-2 days.\n"
            "Format: 'Do [specific action] because [result title] shows [insight]'\n\n"
            "DO NOT suggest generic marketing tactics. Every suggestion must trace back to actual data."
        ),
    }

    type_prompt = analysis_prompts.get(req.analysis_type, analysis_prompts["action_plan"])

    system = (
        "You are a concise market analyst for TBP Auto (Vietnamese brake drum manufacturer, exports to US/Australia/Asia).\n"
        "CRITICAL RULES:\n"
        "- ONLY use information that appears in the research data below. DO NOT invent or assume.\n"
        "- Every keyword, trend, or claim MUST be a direct quote or extract from the data.\n"
        "- If you mention a source, include the exact title or URL from the data.\n"
        "- If data is insufficient for a section, say 'Not enough data' instead of making things up.\n"
        "- Be thorough but concise. Max 800 words total.\n"
        "- Format in clean markdown.\n"
        "- Mix English and Vietnamese where natural."
    )

        # Build structured summary for AI
    lines = req.research_text.strip().split('\n\n')
    source_counts = {}
    for line in lines:
        if line.startswith('['):
            src = line.split(']')[0].replace('[','').strip()
            source_counts[src] = source_counts.get(src, 0) + 1
    summary_header = f"Total results: {len(lines)}\nResults by source: {', '.join(f'{k}: {v}' for k,v in source_counts.items())}\n\n"

    msg = f"Topic: {req.topic}\n\n{summary_header}Research data:\n{req.research_text[:30000]}\n\nTask:\n{type_prompt}"

    if not GEMINI_KEY:
        return {"status": "completed", "topic": req.topic, "analysis_type": req.analysis_type,
            "results": {"gemini": {"status": "skipped", "reason": "No API key"}}}
    try:
        async with httpx.AsyncClient(timeout=90.0) as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
                headers={"content-type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": f"{system}\n\n{msg}"}]}],
                    "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.1},
                })
            d = r.json()
            if "candidates" in d:
                text = d["candidates"][0]["content"]["parts"][0]["text"]
                result = {"status": "ok", "model": "gemini-2.5-flash", "analysis": text}
            else:
                result = {"status": "error", "error": json.dumps(d)}
    except Exception as e:
        result = {"status": "error", "error": str(e)}

    return {"status": "completed", "topic": req.topic, "analysis_type": req.analysis_type, "results": {"gemini": result}}


# ═══════════════════════════════════
#  LOCAL DEV: Serve frontend
# ═══════════════════════════════════
if not IS_VERCEL:
    from fastapi.responses import FileResponse
    _public = pathlib.Path(__file__).resolve().parent.parent / "public"

    @app.get("/")
    async def root():
        index = _public / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"message": "API running. Frontend not found."}

    @app.get("/{filename}")
    async def static_file(filename: str):
        fp = _public / filename
        if fp.exists() and fp.is_file():
            return FileResponse(str(fp))
        return {"detail": "Not Found"}
