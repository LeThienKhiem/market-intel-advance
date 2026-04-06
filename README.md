# Market Intel Advance — TBP Auto

Web research intelligence tool for TBP Auto team. Searches 8 sources in parallel, shows real engagement metrics, and runs AI analysis with Gemini 2.5 Flash.

**Live:** https://market-intel-advance.vercel.app
**Repo:** https://github.com/LeThienKhiem/market-intel-advance.git

## What it does

1. User enters a topic (e.g., "commercial vehicle brake drum market")
2. Backend searches 8 sources in parallel via APIs
3. Results show real metrics (likes, comments, views, points) — no fake scores
4. 6 AI analysis tabs generate data-grounded insights using Gemini 2.5 Flash

## Data Sources

| Source | API | Notes |
|--------|-----|-------|
| Web | Exa API | General web search, 12 results |
| News | Exa API | Filtered to Reuters, Bloomberg, CNBC, Forbes, BBC, WSJ, TechCrunch |
| Reddit | ScrapeCreators `/v1/reddit/search` | Relevance-filtered, up to 15 results |
| X/Twitter | ScrapeCreators `/v1/twitter/search/tweets` | Relevance-filtered |
| TikTok | ScrapeCreators `/v1/tiktok/search/keyword` | Up to 10 results |
| Instagram | ScrapeCreators `/v2/instagram/reels/search` | Up to 10 results |
| Bluesky | AT Protocol (bsky.social) | Auth via app password |
| Hacker News | Algolia API | No key needed |

## AI Analysis Tabs

All tabs are data-grounded — AI only references actual search results, never invents.

- **Action Plan** — Specific actions for TBP Auto with evidence and priority
- **Market Summary** — Trends, opportunities, risks from actual data
- **Competitor Watch** — Companies/brands found in results with threat analysis
- **SEO** — Long-tail keywords (not generic), content gaps, blog ideas with buying intent
- **Sales** — Buying signals, customer pain points, talking points with data evidence
- **Marketing** — Top content by engagement, channel analysis, content ideas

## Project Structure

```
webapp/
├── api/index.py        # FastAPI backend (all search + AI logic)
├── public/index.html   # Single-file frontend (luxury dark theme)
├── vercel.json         # Vercel deployment config
├── requirements.txt    # Python deps: fastapi, httpx, pydantic
├── .env                # Local API keys (NOT in git)
└── .gitignore
```

## Environment Variables

Required in both local `.env` and Vercel dashboard:

```
EXA_API_KEY=...              # Exa web search
SCRAPECREATORS_API_KEY=...   # Reddit, X, TikTok, Instagram
BSKY_HANDLE=...              # Bluesky handle (e.g., user.bsky.social)
BSKY_APP_PASSWORD=...        # Bluesky app password
GEMINI_API_KEY=...           # Google Gemini 2.5 Flash
```

## Run Locally

### macOS / Linux
```bash
cd webapp
echo 'EXA_API_KEY=...' > .env   # create .env with your keys
export $(cat .env | xargs)
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
```
Open http://localhost:8000

### Windows (PowerShell)
```powershell
cd webapp
Get-Content .env | ForEach-Object { if ($_ -match '^([^#].+?)=(.+)$') { [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') } }
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
```

## Deploy to Vercel

1. Push to GitHub: `git push origin main`
2. Connect repo in Vercel dashboard
3. Set root directory to `webapp` (if repo includes parent folders)
4. Add all 5 environment variables in Vercel > Settings > Environment Variables
5. Deploy triggers automatically on push

## Tech Stack

- **Backend:** Python 3.9+, FastAPI, httpx (async HTTP)
- **Frontend:** Vanilla HTML/CSS/JS, luxury dark theme (Playfair Display + Inter)
- **AI:** Google Gemini 2.5 Flash (temperature 0.1 for consistency)
- **Hosting:** Vercel (serverless Python + static files)

## Key Design Decisions

- **Relevance filtering:** `is_relevant()` checks min 2 query words match in result text — removes unrelated results from social media
- **No fake scores:** Cards show real engagement metrics from each platform
- **Data-grounded AI:** All prompts enforce "DO NOT invent" rule with required source citations
- **Long-tail SEO:** SEO tab skips generic terms, focuses on 3-6 word phrases with buying intent
- **Single-file frontend:** Everything in one `index.html` for simplicity on Vercel static hosting
