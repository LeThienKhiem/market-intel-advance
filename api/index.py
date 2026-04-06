"""
Last30Days Research Intelligence — Full Online API v5
Ported from CLI: multi-dimensional scoring, relevance engine, Reddit enrichment,
cross-source dedup, query type detection, supplemental entity-driven searches.
"""
import os, json, time, asyncio, pathlib, re, math
from datetime import datetime, timedelta
from typing import List, Set, Optional, Dict, Any, Tuple
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

IS_VERCEL = bool(os.environ.get("VERCEL"))

app = FastAPI(title="Last30Days Research API", version="5.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Keys ──
EXA_KEY = os.environ.get("EXA_API_KEY", "")
SC_KEY = os.environ.get("SCRAPECREATORS_API_KEY", "")
BSKY_HANDLE = os.environ.get("BSKY_HANDLE", "")
BSKY_PASS = os.environ.get("BSKY_APP_PASSWORD", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

SC_BASE = "https://api.scrapecreators.com"


# ═══════════════════════════════════════════════════════════
#  PORTED FROM CLI: Token-overlap relevance scoring (relevance.py)
# ═══════════════════════════════════════════════════════════

STOPWORDS = frozenset({
    'the', 'a', 'an', 'to', 'for', 'how', 'is', 'in', 'of', 'on',
    'and', 'with', 'from', 'by', 'at', 'this', 'that', 'it', 'my',
    'your', 'i', 'me', 'we', 'you', 'what', 'are', 'do', 'can',
    'its', 'be', 'or', 'not', 'no', 'so', 'if', 'but', 'about',
    'all', 'just', 'get', 'has', 'have', 'was', 'will',
})

SYNONYMS = {
    'hip': {'rap', 'hiphop'}, 'hop': {'rap', 'hiphop'},
    'rap': {'hip', 'hop', 'hiphop'}, 'hiphop': {'rap', 'hip', 'hop'},
    'js': {'javascript'}, 'javascript': {'js'},
    'ts': {'typescript'}, 'typescript': {'ts'},
    'ai': {'artificial', 'intelligence'}, 'ml': {'machine', 'learning'},
    'react': {'reactjs'}, 'reactjs': {'react'},
    'auto': {'automotive', 'automobile'}, 'automotive': {'auto'},
    'brake': {'braking'}, 'braking': {'brake'},
    'drum': {'drums'}, 'drums': {'drum'},
}

LOW_SIGNAL_QUERY_TOKENS = frozenset({
    'advice', 'best', 'chance', 'compare', 'comparison', 'explain', 'guide',
    'how', 'latest', 'news', 'odds', 'opinion', 'prediction', 'review',
    'thoughts', 'tip', 'tips', 'tutorial', 'update', 'updates', 'use',
    'using', 'versus', 'vs', 'worth', 'market', 'industry',
})


def tokenize(text: str) -> Set[str]:
    words = re.sub(r'[^\w\s]', ' ', text.lower()).split()
    tokens = {w for w in words if w not in STOPWORDS and len(w) > 1}
    expanded = set(tokens)
    for t in tokens:
        if t in SYNONYMS:
            expanded.update(SYNONYMS[t])
    return expanded


def token_overlap_relevance(query: str, text: str, hashtags: list = None) -> float:
    """Compute query-centric relevance score 0.0-1.0 (ported from CLI relevance.py)."""
    q_tokens = tokenize(query)
    combined = text
    if hashtags:
        combined = f"{text} {' '.join(hashtags)}"
    t_tokens = tokenize(combined)

    if hashtags:
        for tag in hashtags:
            tag_lower = tag.lower()
            for qt in q_tokens:
                if qt in tag_lower and qt != tag_lower:
                    t_tokens.add(qt)

    if not q_tokens:
        return 0.5

    overlap_tokens = q_tokens & t_tokens
    overlap = len(overlap_tokens)
    if overlap == 0:
        return 0.0

    informative_q_tokens = {t for t in q_tokens if t not in LOW_SIGNAL_QUERY_TOKENS}
    if not informative_q_tokens:
        informative_q_tokens = q_tokens

    coverage = overlap / len(q_tokens)
    informative_overlap = len(informative_q_tokens & t_tokens) / len(informative_q_tokens)
    precision_denominator = min(len(t_tokens), len(q_tokens) + 4) or 1
    precision = overlap / precision_denominator

    # Phrase bonus
    phrase_bonus = 0.0
    nq = ' '.join(re.sub(r'[^\w\s]', ' ', query.lower()).split())
    nt = ' '.join(re.sub(r'[^\w\s]', ' ', combined.lower()).split())
    if nq and nq in nt:
        phrase_bonus = 0.12 if len(nq.split()) > 1 else 0.16

    base = 0.55 * (coverage ** 1.35) + 0.25 * informative_overlap + 0.20 * precision

    # Cap score if only generic tokens matched
    if informative_q_tokens and not (informative_q_tokens & t_tokens):
        return round(min(0.24, base), 2)

    return round(min(1.0, base + phrase_bonus), 2)


# ═══════════════════════════════════════════════════════════
#  PORTED FROM CLI: Query type detection (query_type.py)
# ═══════════════════════════════════════════════════════════

_COMPARISON_PAT = re.compile(r"\b(vs\.?|versus|compared to|comparison|better than|difference between|switch from)\b", re.I)
_HOWTO_PAT = re.compile(r"\b(how to|tutorial|step by step|setup|install|configure|deploy|implement|build a|create a|best practices|tips)\b", re.I)
_PRODUCT_PAT = re.compile(r"\b(price|pricing|cost|buy|purchase|deal|discount|subscription|alternative|supplier|wholesale|OEM)\b", re.I)
_OPINION_PAT = re.compile(r"\b(worth it|thoughts on|opinion|review|experience with|recommend|should i|pros and cons)\b", re.I)
_PREDICTION_PAT = re.compile(r"\b(predict|forecast|odds|chance|probability|election|outcome|bet on|market for)\b", re.I)
_CONCEPT_PAT = re.compile(r"\b(what is|what are|explain|definition|how does|overview|introduction|guide to)\b", re.I)
_BREAKING_PAT = re.compile(r"\b(latest|breaking|announced|launched|released|new|update|news|today|this week)\b", re.I)

WEBSEARCH_PENALTY_BY_TYPE = {
    "product": 15, "concept": 0, "opinion": 15, "how_to": 5,
    "comparison": 10, "breaking_news": 10, "prediction": 15,
}

TIEBREAKER_BY_TYPE = {
    "product":       {"reddit": 0, "x": 1, "tiktok": 2, "instagram": 3, "hackernews": 4, "bluesky": 5, "web": 6, "news": 7},
    "concept":       {"hackernews": 0, "reddit": 1, "web": 2, "news": 3, "x": 4, "bluesky": 5, "tiktok": 6, "instagram": 7},
    "opinion":       {"reddit": 0, "x": 1, "bluesky": 2, "hackernews": 3, "tiktok": 4, "web": 5, "news": 6, "instagram": 7},
    "how_to":        {"reddit": 0, "hackernews": 1, "web": 2, "x": 3, "tiktok": 4, "news": 5, "instagram": 6, "bluesky": 7},
    "comparison":    {"reddit": 0, "hackernews": 1, "x": 2, "web": 3, "news": 4, "tiktok": 5, "instagram": 6, "bluesky": 7},
    "breaking_news": {"x": 0, "reddit": 1, "news": 2, "web": 3, "hackernews": 4, "bluesky": 5, "tiktok": 6, "instagram": 7},
    "prediction":    {"x": 0, "reddit": 1, "web": 2, "hackernews": 3, "bluesky": 4, "news": 5, "tiktok": 6, "instagram": 7},
}

DEFAULT_TIEBREAKER = {"reddit": 0, "x": 1, "tiktok": 2, "instagram": 3, "hackernews": 4, "bluesky": 5, "web": 6, "news": 7}


def detect_query_type(topic: str) -> str:
    if _COMPARISON_PAT.search(topic): return "comparison"
    if _HOWTO_PAT.search(topic): return "how_to"
    if _PRODUCT_PAT.search(topic): return "product"
    if _OPINION_PAT.search(topic): return "opinion"
    if _PREDICTION_PAT.search(topic): return "prediction"
    if _CONCEPT_PAT.search(topic): return "concept"
    if _BREAKING_PAT.search(topic): return "breaking_news"
    return "breaking_news"


# ═══════════════════════════════════════════════════════════
#  PORTED FROM CLI: Multi-dimensional scoring (score.py)
# ═══════════════════════════════════════════════════════════

WEIGHT_RELEVANCE = 0.45
WEIGHT_RECENCY = 0.25
WEIGHT_ENGAGEMENT = 0.30

WEBSEARCH_WEIGHT_RELEVANCE = 0.55
WEBSEARCH_WEIGHT_RECENCY = 0.45
WEBSEARCH_SOURCE_PENALTY = 15
DEFAULT_ENGAGEMENT = 35


def log1p_safe(x) -> float:
    if x is None or x < 0: return 0.0
    return math.log1p(x)


def recency_score(date_str: str, max_days: int = 30) -> int:
    """Score 0-100 based on how recent the date is."""
    if not date_str:
        return 30  # Unknown date gets low-mid score
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        age = (datetime.utcnow() - d).days
        if age < 0: age = 0
        if age > max_days: return 0
        return int(100 * (1 - age / max_days))
    except:
        return 30


def compute_engagement_raw(item: dict) -> Optional[float]:
    """Compute raw engagement score based on source type."""
    source = item.get("source", "")

    if source == "reddit":
        pts = item.get("points") or 0
        cmt = item.get("comments") or 0
        ratio = item.get("upvote_ratio") or 0.5
        top_cmt = item.get("top_comment_score") or 0
        if pts == 0 and cmt == 0: return None
        return 0.50 * log1p_safe(pts) + 0.35 * log1p_safe(cmt) + 0.05 * (ratio * 10) + 0.10 * log1p_safe(top_cmt)

    elif source == "x":
        likes = item.get("likes") or 0
        rts = item.get("retweets") or 0
        replies = item.get("replies") or 0
        if likes == 0 and rts == 0: return None
        return 0.55 * log1p_safe(likes) + 0.25 * log1p_safe(rts) + 0.15 * log1p_safe(replies) + 0.05 * 0

    elif source in ("tiktok", "instagram"):
        views = item.get("views") or item.get("plays") or 0
        likes = item.get("likes") or 0
        if views == 0 and likes == 0: return None
        return 0.50 * log1p_safe(views) + 0.30 * log1p_safe(likes) + 0.20 * log1p_safe(item.get("comments") or 0)

    elif source == "hackernews":
        pts = item.get("points") or 0
        cmt = item.get("comments") or 0
        if pts == 0 and cmt == 0: return None
        return 0.55 * log1p_safe(pts) + 0.45 * log1p_safe(cmt)

    elif source == "bluesky":
        likes = item.get("likes") or 0
        reposts = item.get("reposts") or 0
        if likes == 0 and reposts == 0: return None
        return 0.40 * log1p_safe(likes) + 0.30 * log1p_safe(reposts) + 0.20 * 0 + 0.10 * 0

    return None


def normalize_to_100(values: list) -> list:
    valid = [v for v in values if v is not None]
    if not valid:
        return [DEFAULT_ENGAGEMENT if v is None else 50 for v in values]
    min_val, max_val = min(valid), max(valid)
    range_val = max_val - min_val
    if range_val == 0:
        return [None if v is None else 50 for v in values]
    return [None if v is None else ((v - min_val) / range_val) * 100 for v in values]


def score_and_sort_results(results: list, query: str, query_type: str) -> list:
    """Apply multi-dimensional scoring and sort results (ported from CLI score.py)."""
    if not results:
        return results

    # Step 1: Compute relevance for each item
    for item in results:
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        item["_relevance"] = token_overlap_relevance(query, text)

    # Step 2: Compute raw engagement scores
    eng_raw = [compute_engagement_raw(item) for item in results]
    eng_normalized = normalize_to_100(eng_raw)

    # Step 3: Compute overall score per item
    for i, item in enumerate(results):
        rel_score = int(item["_relevance"] * 100)
        rec_score = recency_score(item.get("date", ""))

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        source = item.get("source", "")

        if source in ("web", "news"):
            # WebSearch: no engagement data, different weights
            penalty = WEBSEARCH_PENALTY_BY_TYPE.get(query_type, WEBSEARCH_SOURCE_PENALTY)
            overall = WEBSEARCH_WEIGHT_RELEVANCE * rel_score + WEBSEARCH_WEIGHT_RECENCY * rec_score - penalty
        else:
            overall = (WEIGHT_RELEVANCE * rel_score + WEIGHT_RECENCY * rec_score + WEIGHT_ENGAGEMENT * eng_score)
            if eng_raw[i] is None:
                overall -= 3  # Unknown engagement penalty

        item["score"] = max(0, min(100, int(overall)))
        item["_rel_score"] = rel_score
        item["_rec_score"] = rec_score
        item["_eng_score"] = eng_score

    # Step 4: Relevance filter — drop items below threshold (keep min 3 per source)
    by_source = {}
    for item in results:
        by_source.setdefault(item["source"], []).append(item)

    filtered = []
    for source, items in by_source.items():
        if len(items) <= 3:
            filtered.extend(items)
        else:
            passed = [i for i in items if i["_relevance"] >= 0.3]
            if not passed:
                passed = sorted(items, key=lambda x: x["_relevance"], reverse=True)[:3]
            filtered.extend(passed)

    # Step 5: Sort by score, then date, then source tiebreaker
    tiebreaker = TIEBREAKER_BY_TYPE.get(query_type, DEFAULT_TIEBREAKER)

    def sort_key(item):
        return (
            -item["score"],
            -int((item.get("date") or "0000-00-00").replace("-", "")),
            tiebreaker.get(item["source"], 99),
        )

    filtered.sort(key=sort_key)
    return filtered


# ═══════════════════════════════════════════════════════════
#  PORTED FROM CLI: Near-duplicate detection (dedupe.py)
# ═══════════════════════════════════════════════════════════

DEDUP_STOPWORDS = frozenset({
    'the', 'a', 'an', 'to', 'for', 'how', 'is', 'in', 'of', 'on',
    'and', 'with', 'from', 'by', 'at', 'this', 'that', 'it', 'show', 'hn',
})


def get_ngrams(text: str, n: int = 3) -> Set[str]:
    text = re.sub(r'[^\w\s]', ' ', text.lower()).strip()
    text = re.sub(r'\s+', ' ', text)
    if len(text) < n:
        return {text}
    return {text[i:i+n] for i in range(len(text) - n + 1)}


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    if not set1 or not set2: return 0.0
    return len(set1 & set2) / len(set1 | set2)


def token_jaccard(text_a: str, text_b: str) -> float:
    words_a = {w for w in re.sub(r'[^\w\s]', ' ', text_a.lower()).split() if w not in DEDUP_STOPWORDS and len(w) > 1}
    words_b = {w for w in re.sub(r'[^\w\s]', ' ', text_b.lower()).split() if w not in DEDUP_STOPWORDS and len(w) > 1}
    if not words_a or not words_b: return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def hybrid_similarity(text_a: str, text_b: str) -> float:
    return max(jaccard_similarity(get_ngrams(text_a), get_ngrams(text_b)), token_jaccard(text_a, text_b))


def get_item_text(item: dict) -> str:
    source = item.get("source", "")
    if source in ("x", "bluesky"):
        return (item.get("title") or item.get("snippet") or "")[:100]
    if source in ("tiktok", "instagram"):
        return (item.get("title") or item.get("snippet") or "")[:100]
    if source == "hackernews":
        title = item.get("title", "")
        if title.startswith("Show HN:"): title = title[8:].strip()
        elif title.startswith("Ask HN:"): title = title[7:].strip()
        return title
    return item.get("title", "")


def dedupe_within_source(items: list, threshold: float = 0.7) -> list:
    """Remove near-duplicates within same source, keeping highest-scored."""
    if len(items) <= 1:
        return items
    ngrams_list = [get_ngrams(get_item_text(item)) for item in items]
    to_remove = set()
    for i in range(len(items)):
        if i in to_remove: continue
        for j in range(i + 1, len(items)):
            if j in to_remove: continue
            if jaccard_similarity(ngrams_list[i], ngrams_list[j]) >= threshold:
                if items[i].get("score", 0) >= items[j].get("score", 0):
                    to_remove.add(j)
                else:
                    to_remove.add(i)
    return [item for idx, item in enumerate(items) if idx not in to_remove]


def dedupe_all_results(results: list) -> list:
    """Dedupe within each source, then URL-based cross-source."""
    # Per-source near-duplicate removal
    by_source = {}
    for item in results:
        by_source.setdefault(item["source"], []).append(item)

    deduped = []
    for source, items in by_source.items():
        deduped.extend(dedupe_within_source(items))

    # Cross-source URL dedup
    seen_urls = set()
    final = []
    for item in deduped:
        url = item.get("url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        final.append(item)

    return final


def cross_source_link(results: list, threshold: float = 0.40) -> list:
    """Annotate items with cross-source references (ported from CLI dedupe.py)."""
    texts = [get_item_text(item) for item in results]
    for i in range(len(results)):
        results[i].setdefault("cross_refs", [])
        for j in range(i + 1, len(results)):
            if results[i]["source"] == results[j]["source"]:
                continue
            if hybrid_similarity(texts[i], texts[j]) >= threshold:
                ref_j = f"{results[j]['source']}: {results[j].get('title', '')[:60]}"
                ref_i = f"{results[i]['source']}: {results[i].get('title', '')[:60]}"
                if ref_j not in results[i]["cross_refs"]:
                    results[i]["cross_refs"].append(ref_j)
                if ref_i not in results[j].setdefault("cross_refs", []):
                    results[j]["cross_refs"].append(ref_i)
    return results


# ═══════════════════════════════════════════════════════════
#  PORTED FROM CLI: Reddit comment enrichment (reddit_enrich.py)
# ═══════════════════════════════════════════════════════════

async def enrich_reddit_comments(items: list, max_items: int = 5, per_timeout: float = 10.0) -> list:
    """Enrich top Reddit items with comment data via ScrapeCreators."""
    if not SC_KEY or not items:
        return items

    headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/5.0"}

    async def fetch_comments(item: dict) -> dict:
        url = item.get("url", "")
        if not url:
            return item
        try:
            async with httpx.AsyncClient(timeout=per_timeout) as c:
                r = await c.get(f"{SC_BASE}/v1/reddit/post/comments",
                    params={"url": url, "sort": "top"}, headers=headers)
                data = r.json()
                comments = data.get("comments") or data.get("data") or []

                top_comments = []
                insights = []
                for cmt in comments[:10]:
                    body = cmt.get("body", "")
                    if not body or body in ("[deleted]", "[removed]"):
                        continue
                    score = cmt.get("ups") or cmt.get("score") or 0
                    author = cmt.get("author", "[deleted]")

                    top_comments.append({
                        "score": score,
                        "author": author,
                        "excerpt": body[:200],
                    })

                    # Extract insights: skip low-value comments
                    if len(body) >= 30:
                        skip = any(re.match(p, body.lower()) for p in [
                            r'^(this|same|agreed|exactly|yep|nope|yes|no|thanks)\.?$',
                            r'^lol|lmao|haha', r'^\[deleted\]', r'^\[removed\]',
                        ])
                        if not skip:
                            insight = body[:150]
                            if len(body) > 150:
                                for k, ch in enumerate(insight):
                                    if ch in '.!?' and k > 50:
                                        insight = insight[:k+1]
                                        break
                                else:
                                    insight = insight.rstrip() + "..."
                            insights.append(insight)

                top_comments.sort(key=lambda c: c.get("score", 0), reverse=True)
                item["top_comments"] = top_comments[:5]
                item["comment_insights"] = insights[:5]
                if top_comments:
                    item["top_comment_score"] = top_comments[0].get("score", 0)

        except:
            pass
        return item

    # Enrich top N Reddit items (by points) in parallel
    reddit_items = sorted(
        [i for i in items if i.get("source") == "reddit"],
        key=lambda x: (x.get("points") or 0) + (x.get("comments") or 0),
        reverse=True
    )[:max_items]

    if reddit_items:
        await asyncio.gather(*[fetch_comments(item) for item in reddit_items], return_exceptions=True)

    return items


# ═══════════════════════════════════════════════════════════
#  PORTED FROM CLI: Entity extraction + supplemental search
# ═══════════════════════════════════════════════════════════

def extract_entities(results: list) -> dict:
    """Extract handles, hashtags, subreddits from Phase 1 results."""
    handles = {}    # handle -> count
    subreddits = {} # subreddit -> count

    GENERIC_HANDLES = frozenset({'elonmusk', 'openai', 'google', 'microsoft', 'apple', 'amazon', 'meta', 'nvidia'})

    for item in results:
        source = item.get("source", "")

        if source == "x":
            h = (item.get("handle") or "").lstrip("@").lower()
            if h and h not in GENERIC_HANDLES:
                handles[h] = handles.get(h, 0) + 1

        elif source == "reddit":
            sub = (item.get("subreddit") or "").replace("r/", "").lower()
            if sub:
                subreddits[sub] = subreddits.get(sub, 0) + 1

    # Sort by frequency, return top N
    top_handles = sorted(handles, key=handles.get, reverse=True)[:3]
    top_subs = sorted(subreddits, key=subreddits.get, reverse=True)[:3]

    return {"x_handles": top_handles, "subreddits": top_subs}


async def supplemental_reddit_search(subreddits: list, query: str) -> list:
    """Phase 2: Targeted search in discovered subreddits."""
    if not SC_KEY or not subreddits:
        return []

    headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/5.0"}
    results = []

    async def search_sub(sub: str):
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"{SC_BASE}/v1/reddit/search",
                    params={"query": f"{query} subreddit:{sub}", "sort": "relevance", "timeframe": "month"},
                    headers=headers)
                data = r.json()
                posts = data.get("posts") or data.get("data") or []
                out = []
                for p in posts[:5]:
                    title = p.get("title", "")
                    text = p.get("selftext") or ""
                    if not token_overlap_relevance(query, f"{title} {text}") >= 0.3:
                        continue
                    pts = p.get("ups") or p.get("score") or 0
                    cmt = p.get("num_comments") or 0
                    created = ""
                    if p.get("created_utc"):
                        try: created = datetime.utcfromtimestamp(p["created_utc"]).strftime("%Y-%m-%d")
                        except: pass
                    out.append({
                        "source": "reddit", "title": title,
                        "subreddit": f"r/{sub}",
                        "url": f"https://reddit.com{p.get('permalink', '')}" if p.get("permalink") else p.get("url", ""),
                        "date": created, "points": pts, "comments": cmt,
                        "snippet": text[:250], "_supplemental": True,
                    })
                return out
        except:
            return []

    tasks = [search_sub(sub) for sub in subreddits]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    for result in gathered:
        if isinstance(result, list):
            results.extend(result)
    return results


async def supplemental_x_search(handles: list, query: str) -> list:
    """Phase 2: Targeted search for specific X handles."""
    if not SC_KEY or not handles:
        return []

    headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/5.0"}
    results = []

    async def search_handle(handle: str):
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"{SC_BASE}/v1/twitter/search/tweets",
                    params={"query": f"from:{handle} {query}", "sort_by": "relevance"},
                    headers=headers)
                data = r.json()
                tweets = data.get("tweets") or data.get("data") or data.get("results") or []
                out = []
                for t in tweets[:3]:
                    text = t.get("full_text") or t.get("text") or ""
                    likes = t.get("favorite_count") or t.get("likes") or 0
                    rts = t.get("retweet_count") or t.get("retweets") or 0
                    tid = t.get("id") or t.get("tweet_id") or t.get("id_str", "")
                    out.append({
                        "source": "x", "title": text[:120],
                        "url": f"https://x.com/{handle}/status/{tid}" if tid else "",
                        "handle": f"@{handle}",
                        "likes": likes, "retweets": rts,
                        "snippet": text[:300], "_supplemental": True,
                    })
                return out
        except:
            return []

    tasks = [search_handle(h) for h in handles]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    for result in gathered:
        if isinstance(result, list):
            results.extend(result)
    return results


# ═══════════════════════════════════════════════════════════
#  SOURCE SEARCH FUNCTIONS (same APIs, now with relevance scoring)
# ═══════════════════════════════════════════════════════════

class ResearchRequest(BaseModel):
    topic: str
    days: int = 30

class AnalyzeRequest(BaseModel):
    research_text: str
    topic: str
    analysis_type: str = "action_plan"


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


async def search_reddit(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/5.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v1/reddit/search",
                params={"query": query, "sort": "relevance", "timeframe": "month"}, headers=headers)
            data = r.json()
            posts = data.get("posts") or data.get("data") or []
            out = []
            for p in posts[:30]:
                title = p.get("title", "")
                text = p.get("selftext") or ""
                rel = token_overlap_relevance(query, f"{title} {text}")
                if rel < 0.25:
                    continue
                pts = p.get("ups") or p.get("score") or 0
                cmt = p.get("num_comments") or 0
                ratio = p.get("upvote_ratio") or 0.5
                sub = p.get("subreddit", "")
                created = ""
                if p.get("created_utc"):
                    try: created = datetime.utcfromtimestamp(p["created_utc"]).strftime("%Y-%m-%d")
                    except: pass
                out.append({
                    "source": "reddit", "title": title,
                    "subreddit": f"r/{sub}" if sub else "",
                    "url": f"https://reddit.com{p.get('permalink', '')}" if p.get("permalink") else p.get("url", ""),
                    "date": created, "points": pts, "comments": cmt,
                    "upvote_ratio": ratio,
                    "snippet": text[:250],
                })
                if len(out) >= 15:
                    break
            return out
    except:
        return []


async def search_x(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/5.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v1/twitter/search/tweets",
                params={"query": query, "sort_by": "relevance"}, headers=headers)
            data = r.json()
            tweets = data.get("tweets") or data.get("data") or data.get("results") or []
            out = []
            for t in tweets[:30]:
                text = t.get("full_text") or t.get("text") or ""
                rel = token_overlap_relevance(query, text)
                if rel < 0.25:
                    continue
                likes = t.get("favorite_count") or t.get("likes") or 0
                rts = t.get("retweet_count") or t.get("retweets") or 0
                replies = t.get("reply_count") or 0
                user = t.get("user") or t.get("author") or {}
                handle = user.get("screen_name") or user.get("username") or ""
                tid = t.get("id") or t.get("tweet_id") or t.get("id_str", "")
                out.append({
                    "source": "x", "title": text[:120],
                    "url": f"https://x.com/{handle}/status/{tid}" if handle and tid else "",
                    "handle": f"@{handle}" if handle else "",
                    "likes": likes, "retweets": rts, "replies": replies,
                    "snippet": text[:300],
                })
                if len(out) >= 15:
                    break
            return out
    except:
        return []


async def search_tiktok(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/5.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v1/tiktok/search/keyword",
                params={"query": query, "sort_by": "relevance"}, headers=headers)
            data = r.json()
            items = data.get("search_item_list") or data.get("data") or []
            out = []
            for v in items[:20]:
                info = v.get("aweme_info") or v
                desc = (info.get("desc") or "").strip()
                if not desc:
                    continue
                hashtags = re.findall(r'#(\w+)', desc)
                rel = token_overlap_relevance(query, desc, hashtags=hashtags)
                if rel < 0.25:
                    continue
                stats = info.get("statistics") or {}
                plays = stats.get("play_count") or 0
                likes = stats.get("digg_count") or 0
                comments = stats.get("comment_count") or 0
                author = info.get("author") or {}
                out.append({
                    "source": "tiktok", "title": desc[:120],
                    "url": info.get("share_url") or "",
                    "handle": f"@{author.get('unique_id', '')}" if author.get("unique_id") else "",
                    "plays": plays, "likes": likes, "comments": comments,
                    "snippet": desc[:250],
                })
                if len(out) >= 10:
                    break
            return out
    except:
        return []


async def search_instagram(query, days):
    if not SC_KEY:
        return []
    try:
        headers = {"x-api-key": SC_KEY, "Content-Type": "application/json", "User-Agent": "last30days/5.0"}
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{SC_BASE}/v2/instagram/reels/search",
                params={"query": query}, headers=headers)
            data = r.json()
            reels = data.get("reels") or data.get("items") or data.get("data") or []
            out = []
            for reel in reels[:20]:
                views = reel.get("video_play_count") or reel.get("video_view_count") or 0
                likes = reel.get("like_count") or 0
                comments = reel.get("comment_count") or 0
                owner = reel.get("owner") or {}
                caption = reel.get("caption")
                text = (caption.get("text", "") if isinstance(caption, dict) else (caption or "")).strip()
                if not text:
                    continue
                hashtags = re.findall(r'#(\w+)', text)
                rel = token_overlap_relevance(query, text, hashtags=hashtags)
                if rel < 0.25:
                    continue
                code = reel.get("shortcode") or reel.get("code") or ""
                out.append({
                    "source": "instagram", "title": text[:120],
                    "url": f"https://instagram.com/reel/{code}" if code else "",
                    "handle": f"@{owner.get('username', '')}" if owner.get("username") else "",
                    "views": views, "likes": likes, "comments": comments,
                    "snippet": text[:250],
                })
                if len(out) >= 10:
                    break
            return out
    except:
        return []


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
                if not text:
                    continue
                rel = token_overlap_relevance(query, text)
                if rel < 0.25:
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


# ═══════════════════════════════════════════════════════════
#  ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    return {
        "status": "ok", "version": "5.0.0",
        "features": ["relevance_scoring", "multi_dim_scoring", "reddit_enrichment",
                      "cross_source_dedup", "supplemental_search", "query_type_detection"],
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

    # Detect query type for scoring adjustments
    query_type = detect_query_type(req.topic)

    # ── Phase 1: Parallel broad search across all sources ──
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
    all_results = []
    for label, result in zip(labels, tasks):
        if isinstance(result, list):
            all_results.extend(result)

    # ── Phase 2: Supplemental entity-driven searches ──
    entities = extract_entities(all_results)
    existing_urls = {r.get("url", "") for r in all_results if r.get("url")}

    supp_reddit, supp_x = [], []
    if entities["subreddits"] or entities["x_handles"]:
        supp_tasks = await asyncio.gather(
            supplemental_reddit_search(entities["subreddits"], req.topic),
            supplemental_x_search(entities["x_handles"], req.topic),
            return_exceptions=True,
        )
        if isinstance(supp_tasks[0], list):
            supp_reddit = [r for r in supp_tasks[0] if r.get("url") not in existing_urls]
        if isinstance(supp_tasks[1], list):
            supp_x = [r for r in supp_tasks[1] if r.get("url") not in existing_urls]

    all_results.extend(supp_reddit)
    all_results.extend(supp_x)

    # ── Reddit comment enrichment ──
    all_results = await enrich_reddit_comments(all_results, max_items=5)

    # ── Deduplication (near-duplicate + URL) ──
    all_results = dedupe_all_results(all_results)

    # ── Multi-dimensional scoring & sorting ──
    all_results = score_and_sort_results(all_results, req.topic, query_type)

    # ── Cross-source linking ──
    all_results = cross_source_link(all_results)

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
        sources_status[nice.get(label, label)] = counts.get(label, 0) > 0

    # Clean internal fields before returning
    for r in all_results:
        for key in ("_relevance", "_rel_score", "_rec_score", "_eng_score", "_supplemental"):
            r.pop(key, None)

    return {
        "status": "completed", "topic": req.topic, "research_time": elapsed,
        "days": req.days, "query_type": query_type, "counts": counts,
        "total": len(all_results),
        "supplemental": {"reddit_subs": entities["subreddits"], "x_handles": entities["x_handles"],
                         "extra_reddit": len(supp_reddit), "extra_x": len(supp_x)},
        "sources": sources_status, "results": all_results[:60]
    }


# ═══════════════════════════════════════════════════════════
#  AI ANALYSIS (Gemini 2.5 Flash)
# ═══════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════
#  LOCAL DEV: Serve frontend
# ═══════════════════════════════════════════════════════════
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
