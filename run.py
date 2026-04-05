"""
Last30Days Web App — Local Server
Run: python webapp/run.py
Opens at: http://localhost:8030
"""
import os
import sys
import webbrowser
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

# Set env vars from .config/last30days/.env if exists
env_file = Path.home() / ".config" / "last30days" / ".env"
if env_file.exists():
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                k, v = k.strip(), v.strip()
                if k and v:
                    os.environ.setdefault(k, v)

# Also set API keys if provided
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

# Import FastAPI app
from api.index import app
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8030))
    print(f"""
╔══════════════════════════════════════════════════════╗
║  LAST30DAYS — Research Intelligence for TBP Auto    ║
╠══════════════════════════════════════════════════════╣
║                                                      ║
║  Server:    http://localhost:{port}                    ║
║  API docs:  http://localhost:{port}/docs                ║
║                                                      ║
║  Claude:    {'✅ Connected' if ANTHROPIC_KEY else '❌ No ANTHROPIC_API_KEY'}                          ║
║  Gemini:    {'✅ Connected' if GEMINI_KEY else '❌ No GEMINI_API_KEY'}                          ║
║                                                      ║
║  Press Ctrl+C to stop                                ║
╚══════════════════════════════════════════════════════╝
""")
    webbrowser.open(f"http://localhost:{port}")
    uvicorn.run(
        "api.index:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
    )
