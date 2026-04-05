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
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
            "claude": bool(CLAUDE_KEY),
        },
        "keys_loaded": {
            "EXA": EXA_KEY[:8] + "..." if EXA_KEY else "MISSING",
            "SC": SC_KEY[:8] + "..." if SC_KEY else "MISSING",
            "BSKY": BSKY_HANDLE or "MISSING",
            "CLAUDE": CLAUDE_KEY[:12] + "..." if CLAUDE_KEY else "MISSING",
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
            "Create 5 actions for TBP Auto using ONLY the research data. DO NOT invent.\n\n"
            "For each action:\n"
            "- **Action**: One sentence — what to do\n"
            "- **Evidence**: Quote the exact result title/snippet that supports this action. "
            "Format: '[source] \"exact title from data\"'\n"
            "- **Priority**: HIGH / MED / LOW\n"
            "- **Timeline**: This week / This month / This quarter\n\n"
            "## Data Summary\n"
            "Before the actions, list: how many results total, from which sources, "
            "and the top 5 most mentioned phrases across all titles.\n\n"
            "If data is insufficient for 5 actions, give fewer. Do NOT pad with generic advice.\n"
            "End with 'Ket luan' in Vietnamese (3 bullets max)."
        ),
        "market_summary": (
            "Summarize the market ONLY from what the research data shows. DO NOT add outside knowledge.\n\n"
            "## Data Overview\n"
            "- Total results: [count]\n"
            "- Sources with data: [list which sources returned results]\n"
            "- Date range of results: [earliest to latest date in data]\n\n"
            "## Key Findings (from actual results)\n"
            "List 3-5 findings. Each MUST quote an actual result title.\n"
            "Format: '**Finding**: [your summary] — Source: [exact title from data]'\n\n"
            "## Opportunities for TBP Auto\n"
            "2 opportunities, each referencing a specific gap or trend visible in the data.\n\n"
            "## Risks\n"
            "2 risks found in the data (tariffs, competition, etc). Quote the source.\n\n"
            "If a section has no supporting data, write 'Khong du du lieu' instead of guessing."
        ),
        "competitor_watch": (
            "Extract competitor info DIRECTLY from the research data. DO NOT invent companies.\n\n"
            "## Companies/Brands Found in Data\n"
            "Scan every title and snippet. List every company or brand name that appears.\n"
            "Format: **Company Name** — mentioned in: [exact title from data]\n\n"
            "## What They're Doing (from data only)\n"
            "For each company found, summarize what the data says about them.\n"
            "Quote the relevant snippet.\n\n"
            "## TBP Auto Position\n"
            "Is TBP Auto mentioned in any result? If yes, quote it. If no, note that.\n\n"
            "## 3 Actions for TBP Auto\n"
            "Each action must respond to a specific competitor finding from above.\n\n"
            "If few competitors are found in data, say so. DO NOT fabricate competitor names."
        ),
        "seo_report": (
            "Extract SEO data DIRECTLY from the research results. DO NOT invent keywords.\n\n"
            "## Keywords (extracted from titles & snippets)\n"
            "Scan every title and snippet in the data. List the 10 most frequently appearing "
            "phrases (2-4 words). For each:\n"
            "- Exact phrase as it appears in the data\n"
            "- Count: appears in X out of Y results\n"
            "- Example source: [exact title from data]\n"
            "- Intent: informational / commercial / transactional\n\n"
            "## URLs/Domains ranking for this topic\n"
            "List every unique domain from the result URLs. Count how many results each domain has.\n"
            "Format: domain.com — X results\n\n"
            "## Content Gap for TBP Auto\n"
            "Based on the titles in results, what topics are covered that tbpauto.com likely doesn't have?\n"
            "List 3, each with the specific result title that shows the gap.\n\n"
            "## Blog Ideas (based on actual result titles)\n"
            "Rewrite 5 result titles as blog posts for tbpauto.com. Show: original title -> TBP version.\n\n"
            "IMPORTANT: If a keyword or claim doesn't appear in the data, don't include it."
        ),
        "sales_brief": (
            "Create Sales Brief using ONLY data from research results.\n\n"
            "## Customer Pain Points (from Reddit/X/forums in data)\n"
            "Quote actual posts. Format: '[source] user said: \"quote\"' — then what it means for Sales.\n"
            "If no social media posts in data, say 'No social media data available'.\n\n"
            "## Competitors Mentioned in Data\n"
            "List every company/brand name found in result titles/snippets. Include the exact title where found.\n\n"
            "## Talking Points for Sales\n"
            "5 points, each MUST reference a specific finding from the data.\n"
            "Format: 'When customer asks about [topic from data], say: [response based on data]'\n\n"
            "## Potential Leads / Interested Parties\n"
            "Any Reddit users, forum posters, or companies asking questions in the data.\n"
            "If none found, say 'No direct leads in current data'.\n\n"
            "DO NOT invent quotes or companies not in the data."
        ),
        "marketing_ideas": (
            "Create Marketing Brief using ONLY the research data.\n\n"
            "## Top Content in Data (by engagement)\n"
            "List the 5 results with highest engagement (likes/comments/points from the data).\n"
            "For each: title, source, engagement numbers, WHY it performed well.\n\n"
            "## Content Ideas (inspired by actual results)\n"
            "Take 5 actual titles from the data and create TBP Auto versions.\n"
            "Format: 'Original: [title] -> TBP version: [your idea] on [platform]'\n\n"
            "## Active Channels\n"
            "Count results per source (Reddit: X results, Web: Y results, etc).\n"
            "Recommend which channel has most relevant content for TBP Auto to participate in.\n\n"
            "## Quick Wins This Week\n"
            "3 specific actions based on what's trending in the data RIGHT NOW.\n"
            "Each must reference a specific result from the data.\n\n"
            "DO NOT suggest generic marketing tactics not supported by the data."
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
        "- Be SHORT. Max 500 words total.\n"
        "- Format in clean markdown.\n"
        "- Mix English and Vietnamese where natural."
    )

    msg = f"Topic: {req.topic}\n\nResearch data:\n{req.research_text[:20000]}\n\nTask:\n{type_prompt}"

    if not CLAUDE_KEY:
        return {"status": "completed", "topic": req.topic, "analysis_type": req.analysis_type,
            "results": {"claude": {"status": "skipped", "reason": "No API key"}}}
    try:
        async with httpx.AsyncClient(timeout=90.0) as c:
            r = await c.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 2048, "system": system,
                    "messages": [{"role": "user", "content": msg}]})
            d = r.json()
            if "content" in d:
                result = {"status": "ok", "model": "claude-sonnet-4", "analysis": d["content"][0]["text"]}
            else:
                result = {"status": "error", "error": json.dumps(d)}
    except Exception as e:
        result = {"status": "error", "error": str(e)}

    return {"status": "completed", "topic": req.topic, "analysis_type": req.analysis_type, "results": {"claude": result}}


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
