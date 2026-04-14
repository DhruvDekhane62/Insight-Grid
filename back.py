from fastapi import FastAPI, HTTPException, Request
from typing import List, Dict, Optional, Any
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import random
import asyncio
from datetime import datetime, timedelta, timezone
import re
import json
import os
from collections import defaultdict
import httpx
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
from urllib.parse import quote
import hashlib
import secrets

# Load environment variables
load_dotenv()

# Password hashing with hashlib
def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with salt."""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${pwd_hash}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    try:
        salt, pwd_hash = hashed_password.split('$')
        return hashlib.sha256((plain_password + salt).encode()).hexdigest() == pwd_hash
    except:
        return False

class NewsVerificationRequest(BaseModel):
    text: str
    source: Optional[str] = None

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

# NewsAPI Configuration
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
if not NEWSAPI_KEY:
    print("WARNING: NEWSAPI_KEY not found in .env file. News verification will not work.")
    NEWSAPI_KEY = ""  # Will cause a clear error if used without key

# Local news API key can reuse NEWSAPI_KEY unless overridden
LOCAL_NEWS_API_KEY = os.getenv("LOCAL_NEWS_API_KEY") or NEWSAPI_KEY

# Perplexity API (optional LLM fact-checking)
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "llama-3.1-sonar-large-128k-online")

# Simple cache to avoid rate limiting
news_cache = {}
CACHE_EXPIRY = 300  # 5 minutes

# Local news cache
local_news_cache: Dict[str, Dict] = {}
LOCAL_NEWS_CACHE_EXPIRY = 180  # 3 minutes


async def fetch_live_local_articles(place: str, desired: int) -> List[Dict]:
    """Fetch live news articles for a place using NewsAPI with RSS fallback."""
    if desired <= 0:
        return []

    api_key = LOCAL_NEWS_API_KEY
    if not api_key:
        return []

    params = {
        "q": f"{place} India",
        "pageSize": min(30, desired),
        "language": "en",
        "apiKey": api_key,
        "sortBy": "publishedAt",
    }

    search_url = "https://newsapi.org/v2/everything"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(search_url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            articles: List[Dict] = []
            for art in data.get("articles", [])[: params["pageSize"]]:
                articles.append(
                    {
                        "title": art.get("title", ""),
                        "description": art.get("description", ""),
                        "url": art.get("url", ""),
                        "source": art.get("source", {}).get("name", ""),
                        "publishedAt": art.get("publishedAt", ""),
                        "is_historical": False,
                    }
                )
            if articles:
                return articles
    except Exception as exc:
        print(f"Live local news error: {exc}")

    fallback = await rss_search_articles(f"{place} India", desired)
    # Map to same output shape
    return [
        {
            "title": item.get("title", ""),
            "description": item.get("description", ""),
            "url": item.get("url", ""),
            "source": item.get("source", "Google News"),
            "publishedAt": item.get("publishedAt", ""),
            "is_historical": False,
        }
        for item in fallback
    ]

async def rss_search_articles(query: str, desired: int) -> List[Dict]:
    try:
        rss_url = (
            "https://news.google.com/rss/search?q="
            + quote(query)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )
        async with httpx.AsyncClient(timeout=8.0) as client:
            rss_resp = await client.get(rss_url)
        items: List[Dict] = []
        if rss_resp.status_code == 200:
            root = ET.fromstring(rss_resp.text)
            channel = root.find("channel")
            if channel is not None:
                for item in channel.findall("item")[: desired]:
                    src_el = item.find("source")
                    src_name = src_el.text.strip() if src_el is not None and src_el.text else "Google News"
                    items.append(
                        {
                            "title": (item.findtext("title") or "").strip(),
                            "url": (item.findtext("link") or "").strip(),
                            "publishedAt": (item.findtext("pubDate") or "").strip(),
                            "source": src_name,
                            "description": "",
                        }
                    )
        return items
    except Exception as exc:
        print(f"rss_search_articles error: {exc}")
        return []



async def run_perplexity_fact_check(claim: str, sources: List[Dict], is_sensitive: bool = False) -> Optional[Dict]:
    """Use Perplexity's API to classify a claim if API key is provided."""
    if not PERPLEXITY_API_KEY:
        return None

    evidence_lines = []
    for article in sources[:8]:
        title = article.get("title") or "Untitled"
        desc = article.get("description", "")
        origin = article.get("source")
        if isinstance(origin, dict):
            origin = origin.get("name")
        origin = origin or "Unknown source"
        
        # Mark trusted sources in the prompt
        is_trusted = article.get("is_trusted", False)
        trusted_marker = " [TRUSTED SOURCE]" if is_trusted else ""
        
        published_at = article.get("publishedAt") or ""
        # Include description for better context
        evidence_lines.append(f"- {title} ({origin}){trusted_marker} {published_at}\n  Description: {desc[:150]}")

    # Different prompts for sensitive vs regular topics
    if is_sensitive:
        user_prompt = (
            "You are a professional fact-checker analyzing a SENSITIVE/CONTROVERSIAL claim.\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. This claim involves political figures, religion, or controversial topics. You MUST be extremely cautious.\n"
            "2. DO NOT verify based on opinions, interpretations, or subjective descriptions (like 'dancing to impress').\n"
            "3. ONLY verify objective, factual events with DIRECT evidence from multiple independent [TRUSTED SOURCE] outlets.\n"
            "4. If the claim is about someone's INTENT, MOTIVATION, or OPINION - mark as 'unclear' or 'misleading' unless explicitly stated by the person.\n"
            "5. Require at least 2-3 independent trusted sources with SPECIFIC details matching the claim.\n"
            "6. If sources only mention a general event but NOT the specific controversial detail - verdict should be 'misleading' or 'unclear'.\n"
            "7. Use your web search to find OFFICIAL statements, press releases, or verified video evidence.\n"
            "8. For sensitive topics, confidence should be LOWER (max 0.7) unless overwhelming evidence exists.\n"
            "9. Return JSON: {verdict: 'verified'/'misleading'/'false'/'unclear', confidence: 0.0-1.0, explanation: detailed reasoning}"
        )
    else:
        user_prompt = (
            "You are a professional fact-checker. Your goal is to verify the user's claim accurately.\n"
            "1. Analyze the claim using the provided headlines and descriptions.\n"
            "2. Pay extra attention to [TRUSTED SOURCE] outlets - they are highly authoritative.\n"
            "3. If multiple trusted sources confirm with specific details, verdict should be 'verified' with high confidence.\n"
            "4. If sources contradict or lack specific details, mark as 'misleading' or 'false'.\n"
            "5. Use your web search capabilities to find additional verification.\n"
            "6. Return JSON: {verdict: 'verified'/'misleading'/'false'/'unclear', confidence: 0.0-1.0, explanation: reasoning}"
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": PERPLEXITY_MODEL,
                    "messages": [
                        {"role": "system", "content": user_prompt},
                        {
                            "role": "user",
                            "content": f"Claim: {claim}\n\nRelevant headlines:\n" + "\n".join(evidence_lines or ["None provided"]),
                        },
                    ],
                    "temperature": 0.1,
                },
            )

        if resp.status_code != 200:
            print(f"Perplexity API error: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        message_content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not message_content:
            return None

        # Clean potential markdown formatting
        cleaned_content = message_content.replace("```json", "").replace("```", "").strip()
        
        parsed = None
        try:
            parsed = json.loads(cleaned_content)
        except json.JSONDecodeError:
            print(f"JSON parse error for content: {cleaned_content[:100]}...")
            # Fallback: simple text analysis if JSON fails
            lower_text = cleaned_content.lower()
            fallback_verdict = "unclear"
            if "false" in lower_text or "misleading" in lower_text or "fake" in lower_text:
                fallback_verdict = "false"
            elif "true" in lower_text or "verified" in lower_text or "accurate" in lower_text:
                fallback_verdict = "verified"
            
            parsed = {
                "verdict": fallback_verdict,
                "confidence": 0.8,
                "explanation": cleaned_content
            }
        except Exception as e:
            print(f"Unexpected error parsing Perplexity response: {e}")
            parsed = {"explanation": message_content, "verdict": "unclear", "confidence": 0.0}

        return parsed
    except Exception as exc:
        print(f"Perplexity request failed: {exc}")
        return None


def classify_perplexity_label(verdict_text: Optional[str]) -> str:
    if not verdict_text:
        return "uncertain"
    text = verdict_text.lower()
    false_keywords = ["false", "fake", "misleading", "incorrect", "hoax", "fabricated", "debunked"]
    true_keywords = ["true", "verified", "accurate", "correct", "authentic", "real"]
    if any(word in text for word in false_keywords):
        return "false"
    if any(word in text for word in true_keywords):
        return "true"
    return "uncertain"


STOPWORDS = {
    "the",
    "is",
    "are",
    "was",
    "were",
    "to",
    "of",
    "in",
    "for",
    "a",
    "an",
    "and",
    "or",
    "on",
    "with",
    "by",
    "about",
    "this",
    "that",
    "from",
    "will",
    "would",
    "should",
    "could",
    "india",
    "indian",
}


def tokenize(text: str) -> set:
    if not text:
        return set()
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def extract_focus_terms(text: str, limit: int = 5) -> List[str]:
    if not text:
        return []
    # Improved regex to capture years (2024, 2026) and alphanumeric terms
    # Matches words starting with letter OR 4-digit years
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9]+\b|\b\d{4}\b", text)
    filtered: List[str] = []
    seen = set()
    for tok in tokens:
        lower_tok = tok.lower()
        if lower_tok in STOPWORDS:
            continue
        # Allow 2-letter tokens if they are uppercase in original text (e.g. WC, AI, US, UK)
        if len(lower_tok) < 3 and not (tok.isupper() and len(tok) >= 2):
            if not tok.isdigit(): # Allow years even if short? 4 digits are >= 3 anyway
                continue
        
        if lower_tok in seen:
            continue
        seen.add(lower_tok)
        filtered.append(tok)
        if len(filtered) >= limit:
            break
    return filtered


def parse_published_at(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def score_article_relevance(claim_tokens: set, article: Dict, focus_terms: List[str]) -> float:
    combined_text = " ".join([
        article.get("title", ""),
        article.get("description", ""),
        article.get("content", ""),
    ])
    article_tokens = tokenize(combined_text)
    overlap = len(claim_tokens & article_tokens)

    title_text = (article.get("title") or "").lower()
    focus_bonus = 0
    for term in focus_terms[:2]:
        if term.lower() in title_text:
            focus_bonus += 2

    published_at = parse_published_at(article.get("publishedAt"))
    recency_bonus = 0
    if published_at:
        hours = max(1.0, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
        recency_bonus = max(0.0, 2.0 - (hours / 12.0))

    severity_bonus = 1.5 if is_severe_article(article) else 0

    return overlap * 1.8 + focus_bonus + recency_bonus + severity_bonus


def build_final_message(label: str, confidence: Optional[float]) -> str:
    conf_pct = f" ({int(confidence * 100)}% confidence)" if isinstance(confidence, (int, float)) else ""
    if label == "true":
        return f"Verdict: TRUE{conf_pct}"
    if label == "false":
        return f"Verdict: FALSE{conf_pct}"
    return f"Verdict: UNCERTAIN{conf_pct}"


import httpx

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Development ke liye, production me restrict karo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# User storage file
USERS_FILE = "users.json"

def load_users():
    """Load users from JSON file."""
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_users(users):
    """Save users to JSON file."""
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

# Authentication endpoints
@app.post("/api/signup")
async def signup(request: SignUpRequest):
    """Register a new user."""
    users = load_users()
    
    # Check if email already exists
    if request.email in users:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Validate password length
    if len(request.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    
    # Hash password and store user
    users[request.email] = {
        "password_hash": hash_password(request.password),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    save_users(users)
    
    return {"message": "Account created successfully", "email": request.email}

@app.post("/api/login")
async def login(request: LoginRequest):
    """Authenticate a user."""
    users = load_users()
    
    # Check if user exists
    if request.email not in users:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Verify password
    user = users[request.email]
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    return {"message": "Login successful", "email": request.email}

 
# --- Stream ingestion trigger (to Flink/Kafka microservices) ---
@app.post("/ingest")
async def ingest_stream(source: str, urls: List[str]):
    # In production: add Kafka publishing logic here
    return {"status": "ok", "source": source, "count": len(urls)}

# --- NLP/ML: Analyze a media item ---
@app.post("/analyze")
async def analyze_media_item(item: Dict):
    # Call your NLP pipeline (BERT/LLM, zero-shot, claim, sentiment, entity extraction)
    # Return structured output: entities, claims, risk score, sentiment
    return {
        "entities": ["election", "COVID-19"],
        "claims": ["Vaccine fake news!"],
        "risk": 0.85,  # Example score
        "sentiment": "negative"
    }

# Simple India state centroids for fallback risk-map when precise coords missing
INDIA_STATE_CENTROIDS = {
    "assam": {"lat": 26.2006, "lon": 92.9376},
    "maharashtra": {"lat": 19.7515, "lon": 75.7139},
    "gujarat": {"lat": 22.2587, "lon": 71.1924},
    "uttar pradesh": {"lat": 26.8467, "lon": 80.9462},
    "odisha": {"lat": 20.9517, "lon": 85.0985},
    "uttarakhand": {"lat": 30.0668, "lon": 79.0193},
    "bihar": {"lat": 25.0961, "lon": 85.3131},
    "jammu and kashmir": {"lat": 33.7782, "lon": 76.5762},
    "punjab": {"lat": 31.1471, "lon": 75.3412},
    "west bengal": {"lat": 22.9868, "lon": 87.8550},
    "karnataka": {"lat": 15.3173, "lon": 75.7139},
    "tamil nadu": {"lat": 11.1271, "lon": 78.6569},
    "andhra pradesh": {"lat": 15.9129, "lon": 79.7400},
    "telangana": {"lat": 18.1124, "lon": 79.0193},
    "kerala": {"lat": 10.8505, "lon": 76.2711},
    "rajasthan": {"lat": 27.0238, "lon": 74.2179},
}

CITY_NEWS_LOCATIONS = [
    {"name": "Delhi", "state": "Delhi", "lat": 28.6139, "lon": 77.2090},
    {"name": "Mumbai", "state": "Maharashtra", "lat": 19.0760, "lon": 72.8777},
    {"name": "Bengaluru", "state": "Karnataka", "lat": 12.9716, "lon": 77.5946},
    {"name": "Hyderabad", "state": "Telangana", "lat": 17.3850, "lon": 78.4867},
    {"name": "Chennai", "state": "Tamil Nadu", "lat": 13.0827, "lon": 80.2707},
    {"name": "Kolkata", "state": "West Bengal", "lat": 22.5726, "lon": 88.3639},
    {"name": "Pune", "state": "Maharashtra", "lat": 18.5204, "lon": 73.8567},
    {"name": "Ahmedabad", "state": "Gujarat", "lat": 23.0225, "lon": 72.5714},
    {"name": "Jaipur", "state": "Rajasthan", "lat": 26.9124, "lon": 75.7873},
    {"name": "Lucknow", "state": "Uttar Pradesh", "lat": 26.8467, "lon": 80.9462},
    {"name": "Indore", "state": "Madhya Pradesh", "lat": 22.7196, "lon": 75.8577},
    {"name": "Surat", "state": "Gujarat", "lat": 21.1702, "lon": 72.8311},
    {"name": "Kanpur", "state": "Uttar Pradesh", "lat": 26.4499, "lon": 80.3319},
    {"name": "Nagpur", "state": "Maharashtra", "lat": 21.1458, "lon": 79.0882},
    {"name": "Patna", "state": "Bihar", "lat": 25.5941, "lon": 85.1376},
    {"name": "Visakhapatnam", "state": "Andhra Pradesh", "lat": 17.6868, "lon": 83.2185},
    {"name": "Bhopal", "state": "Madhya Pradesh", "lat": 23.2599, "lon": 77.4126},
    {"name": "Vadodara", "state": "Gujarat", "lat": 22.3072, "lon": 73.1812},
    {"name": "Ghaziabad", "state": "Uttar Pradesh", "lat": 28.6692, "lon": 77.4538},
    {"name": "Ludhiana", "state": "Punjab", "lat": 30.9010, "lon": 75.8573},
    {"name": "Agra", "state": "Uttar Pradesh", "lat": 27.1767, "lon": 78.0081},
    {"name": "Nashik", "state": "Maharashtra", "lat": 19.9975, "lon": 73.7898},
    {"name": "Faridabad", "state": "Haryana", "lat": 28.4089, "lon": 77.3178},
    {"name": "Meerut", "state": "Uttar Pradesh", "lat": 28.9845, "lon": 77.7064},
    {"name": "Rajkot", "state": "Gujarat", "lat": 22.3039, "lon": 70.8022},
    {"name": "Varanasi", "state": "Uttar Pradesh", "lat": 25.3176, "lon": 82.9739},
    {"name": "Srinagar", "state": "Jammu and Kashmir", "lat": 34.0837, "lon": 74.7973},
    {"name": "Aurangabad", "state": "Maharashtra", "lat": 19.8762, "lon": 75.3433},
    {"name": "Amritsar", "state": "Punjab", "lat": 31.6340, "lon": 74.8723},
]

CITY_HOTSPOT_LIMIT = len(CITY_NEWS_LOCATIONS)
CITY_NEWS_LIMIT = 15
city_hotspot_cache = {
    "timestamp": datetime.min,
    "events": []
}
city_events_lock = asyncio.Lock()
CITY_HOTSPOT_CACHE_EXPIRY = 300
SEVERITY_KEYWORDS = [
    "alert",
    "warning",
    "violence",
    "flood",
    "storm",
    "cyclone",
    "earthquake",
    "riot",
    "fake",
    "false",
    "misinformation",
    "landslide",
    "tsunami",
    "wildfire",
    "heatwave",
    "drought",
    "mobbing",
    "mob",
    "lynching",
    "crowd",
    "bandh",
    "strike",
    "protest",
    "evacuation",
    "displacement",
    "migration",
    "refugee",
    "downpour",
    "heavy rain",
    "rainfall",
    "snowfall",
    "cold wave",
    "heat wave",
    # Security and terrorism keywords
    "terrorist",
    "terrorism",
    "terror",
    "encounter",
    "gunfight",
    "shootout",
    "militant",
    "extremist",
    "attack",
    "bombing",
    "blast",
    "explosion",
    "hostage",
    "kidnapping",
    "abduction",
    "armed",
    "security threat",
    "insurgency",
    "separatist",
]


async def summarize_articles_for_place(topic: str, articles: List[Dict]) -> Optional[Dict]:
    if not articles or not PERPLEXITY_API_KEY:
        return None

    summary = await run_perplexity_fact_check(topic, articles)
    if not summary:
        return None

    label = classify_perplexity_label(summary.get("verdict") or summary.get("label"))
    confidence = summary.get("confidence")
    message = summary.get("explanation") or build_final_message(label, confidence)
    return {
        "label": label,
        "confidence": confidence,
        "message": message,
    }


async def build_city_events() -> List[Dict]:
    events: List[Dict] = []
    tasks = [fetch_live_local_articles(city["name"], CITY_NEWS_LIMIT) for city in CITY_NEWS_LOCATIONS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for idx, city in enumerate(CITY_NEWS_LOCATIONS):
        res = results[idx]
        articles = []
        if isinstance(res, Exception):
            articles = []
        else:
            articles = res or []

        article_count = len(articles)
        severity = min(0.95, 0.35 + 0.12 * article_count)
        value = 250 + article_count * 140
        desc = f"Updates from {city['name']}"
        if articles:
            top_title = articles[0].get("title") or "News update"
            desc = f"{top_title} ({city['name']})"

        alert_articles: List[Dict] = []
        category_counts: Dict[str, int] = defaultdict(int)
        for art in articles:
            text = f"{art.get('title','')} {art.get('description','')}"
            cat = classify_alert_category(text)
            if cat != "General Alert":
                art_copy = dict(art)
                art_copy["category"] = cat
                art_copy["priority"] = compute_priority_score(text, art.get("source"))
                alert_articles.append(art_copy)
                category_counts[cat] += 1

        alert_articles.sort(key=lambda a: a.get("priority", 0.0), reverse=True)
        alert_count = len(alert_articles)
        has_alerts = alert_count > 0
        severity = min(0.98, (0.35 + 0.12 * article_count) + (0.25 if has_alerts else 0.0))

        # Determine Red/Blue flag status
        # Red Flag: Has Weather, Terrorism, or Crime articles
        red_flag_categories = {"Weather", "Terrorism", "Crime"}
        is_red_flag = any(cat in red_flag_categories for cat in category_counts.keys())
        
        # For Red Flag cities, prioritize the alert articles
        # For Blue Flag cities, show general local news
        display_articles = alert_articles if is_red_flag else articles

        events.append(
            {
                "lat": city["lat"],
                "lon": city["lon"],
                "severity": severity,
                "desc": desc,
                "value": value,
                "city": city["name"],
                "state": city["state"],
                "articles": articles,
                "article_count": article_count,
                "alert_articles": alert_articles,
                "alert_count": alert_count,
                "categories": category_counts,
                "is_red_flag": is_red_flag,
                "display_articles": display_articles,
            }
        )

    return events


async def get_city_events(force_refresh: bool = False) -> List[Dict]:
    now = datetime.now()
    if (
        not force_refresh
        and city_hotspot_cache["events"]
        and now - city_hotspot_cache["timestamp"] < timedelta(seconds=CITY_HOTSPOT_CACHE_EXPIRY)
    ):
        return city_hotspot_cache["events"]

    async with city_events_lock:
        now = datetime.now()
        if (
            not force_refresh
            and city_hotspot_cache["events"]
            and now - city_hotspot_cache["timestamp"] < timedelta(seconds=CITY_HOTSPOT_CACHE_EXPIRY)
        ):
            return city_hotspot_cache["events"]

        events = await build_city_events()
        if not events:
            events = [
                {"lat": 28.6139, "lon": 77.2090, "severity": 0.8, "desc": "Delhi alert", "value": 1200, "city": "Delhi", "state": "Delhi", "articles": [], "article_count": 0},
                {"lat": 19.0760, "lon": 72.8777, "severity": 0.7, "desc": "Mumbai flooding", "value": 900, "city": "Mumbai", "state": "Maharashtra", "articles": [], "article_count": 0},
            ]

        city_hotspot_cache.update({"timestamp": now, "events": events})
        return events


def is_severe_article(article: Dict) -> bool:
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    return any(keyword in text for keyword in SEVERITY_KEYWORDS)


def classify_alert_category(text: str) -> str:
    t = (text or "").lower()
    # Weather - HIGHEST PRIORITY for Red Flag
    if any(k in t for k in ("flood", "inundation", "flash flood", "cyclone", "hurricane", "typhoon", "storm", "earthquake", "landslide", "mudslide", "tsunami", "wildfire", "forest fire", "heatwave", "heat wave", "cold wave", "snowfall", "downpour", "heavy rain", "rainfall", "drought")):
        return "Weather"
    
    # Security and terrorism - MEDIUM PRIORITY for Red Flag
    if any(k in t for k in ("terrorist", "terrorism", "terror", "encounter", "gunfight", "shootout", "militant", "extremist", "insurgency", "attack", "bombing", "blast", "explosion", "hostage", "kidnapping", "abduction")):
        return "Terrorism"
        
    # Crime and Unrest - LOW PRIORITY for Red Flag
    if any(k in t for k in ("riot", "violence", "clashes", "mobbing", "lynching", "mob", "crowd", "crime", "robbery", "murder", "assault", "kidnap", "rape")):
        return "Crime"

    if any(k in t for k in ("evacuation", "displacement", "migration", "refugee")):
        return "Migration/Displacement"
        
    return "General Alert"


def compute_priority_score(text: str, source: Optional[str]) -> float:
    t = (text or "").lower()
    s = (source or "").lower()
    score = 0.0
    
    # Priority: Weather > Terrorism > Local Crime
    
    # Weather (Red Flag Priority 1)
    if any(k in t for k in ("flood", "cyclone", "storm", "earthquake", "tsunami", "landslide", "wildfire", "heatwave", "cold wave", "heavy rain", "downpour")):
        score += 10.0
        
    # Terrorism (Red Flag Priority 2)
    elif any(k in t for k in ("terrorist", "terrorism", "terror", "encounter", "gunfight", "shootout", "militant", "extremist", "bombing", "blast", "explosion")):
        score += 7.0
        
    # Crime/Unrest (Red Flag Priority 3)
    elif any(k in t for k in ("riot", "violence", "mob", "lynching", "crime", "murder", "assault", "kidnap", "rape")):
        score += 4.0
        
    # Other alerts
    elif any(k in t for k in ("protest", "strike", "bandh", "displacement")):
        score += 2.0
        
    # Source weighting
    if any(k in s for k in ("hindu", "ndtv", "times of india", "new indian express", "tatva")):
        score += 0.5
        
    return score

def build_alert_list(total_events: int, severe_events: int) -> List[Dict]:
    def scale(value: float, divisor: float, minimum: int = 1) -> int:
        if divisor <= 0:
            divisor = 1
        base = max(minimum, int(round(value / divisor)))
        return min(999, base)

    return [
        {"id": 1, "name": "Notifications", "icon": "profile", "count": 0, "type": "notifications"},
        {"id": 2, "name": "Critical National Notifications", "icon": "person", "count": scale(max(severe_events, 1), 1), "type": "critical"},
        {"id": 3, "name": "National Notifications", "icon": "document", "count": scale(max(total_events, 1), 2), "type": "national"},
        {"id": 4, "name": "Monitoring Notifications", "icon": "headphone", "count": scale(max(total_events, 1), 3), "type": "monitoring"},
        {"id": 5, "name": "Precautionary Restraint Notifications", "icon": "people", "count": scale(max(total_events, 1), 4), "type": "precautionary"},
        {"id": 6, "name": "Preventive Monitor Notifications", "icon": "water", "count": scale(max(total_events, 1), 5), "type": "preventive"},
        {"id": 7, "name": "Personal Responses", "icon": "monitor", "count": scale(max(total_events, 1), 6), "type": "personal"},
    ]

# --- Geospatial Map API ---
@app.get("/hotspots")
async def get_hotspots():
    events = await get_city_events()
    return {"hotspots": events}

@app.get("/risk-map")
async def get_risk_map():
    events = await get_city_events()
    if not events:
        return {"states": []}

    # Return city-wise events directly instead of aggregating by state
    states: List[Dict] = []
    for event in events:
        states.append({
            "state": event.get("city") or event.get("state") or "Unknown", # Label as city name
            "value": max(1, event.get("article_count") or len(event.get("articles") or [])),
            "lat": event["lat"],
            "lon": event["lon"],
            "alert_count": event.get("alert_count", 0),
            "city": event.get("city"), # Ensure city is passed explicitly
            "is_red_flag": event.get("is_red_flag", False),
        })

    states.sort(key=lambda s: s["value"], reverse=True)
    return {"states": states}


@app.get("/alerts")
async def get_alerts():
    events = await get_city_events()
    total_articles = sum(event.get("article_count") or len(event.get("articles") or []) for event in events)
    severe_count = 0
    top_articles: List[Dict] = []
    for event in events:
        articles = event.get("articles") or []
        top_articles.extend(articles[:1])
        for art in articles:
            if is_severe_article(art):
                severe_count += 1

    alerts = build_alert_list(max(total_articles, len(events)), severe_count)
    insight = None
    if top_articles:
        insight = await summarize_articles_for_place("India nationwide risk", top_articles[:5])

    result = {"alerts": alerts}
    if insight:
        result["insight"] = insight
    return result


@app.get("/alerts/by-region")
async def get_alerts_by_region(place: str):
    place = (place or "").strip()
    if not place:
        return {"place": place, "total_events": 0, "severe_events": 0, "top_articles": []}

    events = await get_city_events()
    place_lower = place.lower()
    matching_articles: List[Dict] = []
    for event in events:
        city = (event.get("city") or "").lower()
        state = (event.get("state") or "").lower()
        if place_lower not in city and place_lower not in state:
            continue
        for art in event.get("articles") or []:
            text = f"{art.get('title','')} {art.get('description','')}"
            cat = classify_alert_category(text)
            if cat != "General Alert":
                art_copy = dict(art)
                art_copy["category"] = cat
                art_copy["priority"] = compute_priority_score(text, art.get("source"))
                matching_articles.append(art_copy)

    severe_events = sum(1 for art in matching_articles if is_severe_article(art))
    category_counts: Dict[str, int] = defaultdict(int)
    for art in matching_articles:
        cat = art.get("category") or classify_alert_category(f"{art.get('title','')} {art.get('description','')}")
        category_counts[cat] += 1

    matching_articles.sort(key=lambda a: a.get("priority", 0.0), reverse=True)

    insight = await summarize_articles_for_place(f"Updates for {place}", matching_articles[:5]) if matching_articles else None

    return {
        "place": place,
        "total_events": len(matching_articles),
        "severe_events": severe_events,
        "categories": category_counts,
        "top_articles": matching_articles[:8],
        "insight": insight,
    }

# --- Live Streams API ---
@app.get("/live-streams")
async def get_live_streams():
    streams = [
        {
            "id": 1,
            "title": "Conference Stream",
            "thumbnail": "https://images.unsplash.com/photo-1540575467063-178a50c2df87?w=400",
            "duration": "04:10",
            "status": "live"
        },
        {
            "id": 2,
            "title": "Studio Camera",
            "thumbnail": "https://images.unsplash.com/photo-1492691527719-9d1e07e534b4?w=400",
            "duration": "03:58",
            "status": "live"
        },
        {
            "id": 3,
            "title": "Street View",
            "thumbnail": "https://images.unsplash.com/photo-1449824913935-59a10b8d2000?w=400",
            "duration": "03:00",
            "status": "live"
        },
        {
            "id": 4,
            "title": "Urban Transit",
            "thumbnail": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=400",
            "duration": "03:00",
            "status": "live"
        },
        {
            "id": 5,
            "title": "Street Monitoring",
            "thumbnail": "https://images.unsplash.com/photo-1477959858617-67f85cf4f1df?w=400",
            "duration": "03:00",
            "status": "live"
        },
    ]
    return {"streams": streams}

# --- Analytics API ---
@app.get("/analytics")
async def get_analytics():
    events = await get_city_events()
    
    # Category breakdown
    category_counts: Dict[str, int] = defaultdict(int)
    severity_counts: Dict[str, int] = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    
    # Source tracking
    source_counts: Dict[str, int] = defaultdict(int)
    
    # City impact
    city_alert_counts: Dict[str, int] = defaultdict(int)
    
    # Temporal patterns
    hourly_buckets: Dict[str, int] = defaultdict(int)
    
    # Trending keywords (extract from titles)
    keyword_counts: Dict[str, int] = defaultdict(int)
    
    total_articles = 0
    total_alerts = 0
    
    for event in events:
        city_name = event.get("city") or event.get("state") or "Unknown"
        alert_count = event.get("alert_count", 0)
        
        if alert_count > 0:
            city_alert_counts[city_name] = alert_count
        
        # Process all articles
        for article in event.get("articles") or []:
            total_articles += 1
            
            # Track source
            source = article.get("source", "Unknown")
            source_counts[source] += 1
            
            # Time tracking
            published = parse_published_at(article.get("publishedAt"))
            if published:
                bucket = published.strftime("%H:00")
                hourly_buckets[bucket] += 1
            
            # Extract keywords from title
            title = article.get("title", "")
            words = re.findall(r'\b[A-Z][a-z]+\b', title)
            for word in words[:3]:  # Top 3 capitalized words
                if len(word) > 3 and word.lower() not in STOPWORDS:
                    keyword_counts[word] += 1
        
        # Process alert articles
        for alert_art in event.get("alert_articles") or []:
            total_alerts += 1
            category = alert_art.get("category", "General Alert")
            category_counts[category] += 1
            
            # Severity classification
            priority = alert_art.get("priority", 0.0)
            if priority >= 3.0:
                severity_counts["Critical"] += 1
            elif priority >= 2.0:
                severity_counts["High"] += 1
            elif priority >= 1.0:
                severity_counts["Medium"] += 1
            else:
                severity_counts["Low"] += 1
    
    # Prepare category data
    category_data = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:6]
    
    # Prepare top cities by alerts
    top_cities = sorted(city_alert_counts.items(), key=lambda x: x[1], reverse=True)[:6]
    
    # Count cities with alerts (to match map red markers)
    cities_with_alerts = len([city for city, count in city_alert_counts.items() if count > 0])
    
    # Prepare top sources
    top_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Prepare trending keywords
    top_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    
    # Prepare hourly activity
    current_hour = datetime.now(timezone.utc).hour
    hourly_labels = [(current_hour - i) % 24 for i in range(11, -1, -1)]
    hourly_values = [hourly_buckets.get(f"{h:02d}:00", 0) for h in hourly_labels]
    hourly_labels_str = [f"{h:02d}:00" for h in hourly_labels]
    
    return {
        "summary": {
            "total_articles": total_articles,
            "total_alerts": cities_with_alerts,  # Changed to count cities with alerts
            "cities_monitored": len(events),
            "alert_rate": round((cities_with_alerts / max(len(events), 1)) * 100, 1)
        },
        "categories": {
            "labels": [cat for cat, _ in category_data],
            "values": [count for _, count in category_data]
        },
        "severity": {
            "labels": ["Critical", "High", "Medium", "Low"],
            "values": [severity_counts[s] for s in ["Critical", "High", "Medium", "Low"]]
        },
        "topCities": {
            "labels": [city for city, _ in top_cities],
            "values": [count for _, count in top_cities]
        },
        "topSources": {
            "labels": [src for src, _ in top_sources],
            "values": [count for _, count in top_sources]
        },
        "trending": {
            "keywords": [kw for kw, _ in top_keywords],
            "counts": [count for _, count in top_keywords]
        },
        "timeline": {
            "labels": hourly_labels_str,
            "values": hourly_values
        },
        # Legacy support
        "barChart": {
            "labels": [city for city, _ in top_cities] if top_cities else ["No Data"],
            "values": [count for _, count in top_cities] if top_cities else [0],
            "lineValue": max([count for _, count in top_cities], default=0)
        },
        "lineChart": {
            "labels": hourly_labels_str[-6:],
            "line1": hourly_values[-6:],
            "line2": [v * 0.8 for v in hourly_values[-6:]],
            "peakValue": max(hourly_values, default=1) or 1
        }
    }

@app.get("/news/search")
async def search_news(query: str = "", limit: int = 20):
    results: List[Dict] = []
    focus_terms = extract_focus_terms(query) or ["India"]
    search_query = " OR ".join(focus_terms)
    params = {
        "q": search_query,
        "language": "en",
        "pageSize": min(40, limit * 2),
        "sortBy": "publishedAt",
        "apiKey": NEWSAPI_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://newsapi.org/v2/everything", params=params)
        if resp.status_code == 200:
            payload = resp.json()
            claim_tokens = tokenize(query)
            scored: List[tuple] = []
            for art in payload.get("articles", []):
                score = score_article_relevance(claim_tokens, art, focus_terms)
                scored.append((score, art))
            scored.sort(key=lambda x: x[0], reverse=True)
            for score, art in scored[:limit]:
                results.append(
                    {
                        "title": art.get("title"),
                        "description": art.get("description"),
                        "source": art.get("source", {}).get("name"),
                        "publishedAt": art.get("publishedAt"),
                        "url": art.get("url"),
                        "score": score,
                    }
                )
    except Exception as exc:
        print(f"search_news error: {exc}")

    return {"results": results}

@app.post("/news/analyze")
async def analyze_news_item(payload: Dict):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    if not NEWSAPI_KEY:
        raise HTTPException(status_code=500, detail="NewsAPI key missing on server")

    focus_terms = extract_focus_terms(text) or [text[:60]]
    search_query = " OR ".join([term for term in focus_terms if term])
    params = {
        "q": search_query,
        "language": "en",
        "pageSize": 8,
        "sortBy": "publishedAt",
        "apiKey": NEWSAPI_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get("https://newsapi.org/v2/everything", params=params)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"News search failed: {exc}")

    if resp.status_code != 200:
        detail = resp.json().get("message", "NewsAPI error")
        raise HTTPException(status_code=resp.status_code, detail=detail)

    payload_json = resp.json()
    claim_tokens = tokenize(text)
    scored: List[tuple] = []
    for art in payload_json.get("articles", []):
        score = score_article_relevance(claim_tokens, art, focus_terms)
        scored.append((score, art))

    scored.sort(key=lambda x: x[0], reverse=True)
    normalized_articles: List[Dict] = []
    for score, art in scored[:6]:
        normalized_articles.append(
            {
                "title": art.get("title"),
                "description": art.get("description"),
                "url": art.get("url"),
                "source": art.get("source", {}).get("name"),
                "publishedAt": art.get("publishedAt"),
                "score": score,
            }
        )

    insight = await summarize_articles_for_place("News fact-check", normalized_articles[:5]) if normalized_articles else None
    label = insight.get("label") if insight else "uncertain"
    confidence = insight.get("confidence") if insight else None
    message = insight.get("message") if insight else "Not enough evidence to classify this claim."

    return {
        "label": label,
        "confidence": confidence,
        "message": message,
        "articles": normalized_articles,
    }


@app.post("/analyze-news")
async def analyze_news_alias(payload: Dict):
    return await analyze_news_item(payload)


# --- Genre Classification & Source Priority ---

NEWS_GENRES = {
    "Sports": ["match", "score", "win", "defeat", "cup", "tournament", "league", "cricket", "football", "soccer", "tennis", "nba", "nfl", "ipl", "t20", "odi", "fifa", "icc", "bcci", "team", "player", "athlete", "medal", "olympics", "championship"],
    "Politics": ["election", "vote", "parliament", "congress", "senate", "minister", "president", "law", "bill", "policy", "government", "campaign", "party", "modi", "biden", "trump", "democracy", "voter", "poll"],
    "Technology": ["ai", "apple", "google", "microsoft", "launch", "feature", "update", "software", "hardware", "chip", "robot", "cyber", "hack", "startup", "funding", "app", "model", "gpt", "crypto", "bitcoin", "blockchain"],
    "Entertainment": ["movie", "film", "actor", "actress", "song", "music", "concert", "award", "oscar", "grammy", "netflix", "trailer", "teaser", "celebrity", "gossip", "star", "director", "cinema", "box office"],
    "Finance": ["stock", "market", "share", "sensex", "nifty", "bank", "economy", "inflation", "rate", "tax", "budget", "investment", "fund", "revenue", "profit", "loss", "ipo"],
}

# Sensitive/Controversial Topic Detection
SENSITIVE_KEYWORDS = {
    "political_figures": ["modi", "trump", "biden", "putin", "xi jinping", "pm", "president", "minister", "politician"],
    "religion": ["hindu", "muslim", "christian", "sikh", "buddhist", "religious", "temple", "mosque", "church", "faith"],
    "controversy": ["scandal", "controversy", "accused", "alleged", "claim", "rumor", "gossip", "leaked", "secret"],
    "opinion_markers": ["dancing", "impress", "trying to", "attempting to", "reportedly", "allegedly", "supposedly"],
    "unverifiable": ["private", "behind closed doors", "secret meeting", "anonymous source"]
}

def is_sensitive_topic(text: str) -> tuple[bool, str]:
    """Detect if the claim is about sensitive/controversial topics requiring extra scrutiny."""
    text_lower = text.lower()
    reasons = []
    
    for category, keywords in SENSITIVE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                reasons.append(category)
                break
    
    is_sensitive = len(reasons) >= 2 or "political_figures" in reasons
    reason_str = ", ".join(set(reasons)) if reasons else ""
    return is_sensitive, reason_str

GENRE_TRUSTED_SOURCES = {
    "Sports": ["espn", "cricinfo", "icc", "fifa", "bcci", "nba", "skysports", "bbc sport", "sportskeeda", "cricbuzz", "goal.com", "yahoosports", "bleacher report", "star sports", "wisden"],
    "Politics": ["reuters", "ap news", "bbc", "pti", "ani", "pib", "press trust of india"],
    "Technology": ["techcrunch", "wired", "the verge", "engadget", "cnet", "arstechnica", "venturebeat", "9to5mac", "android authority", "gsmarena", "tom's guide"],
    "Entertainment": ["variety", "hollywood reporter", "deadline", "imdb", "tmz", "billboard", "rolling stone", "pinkvilla", "filmfare", "e! online"],
    "Finance": ["bloomberg", "cnbc", "financial times", "economist", "moneycontrol", "economic times", "business standard", "wall street journal", "forbes", "mint", "business insider"],
    "General": ["reuters", "ap news", "bbc", "cnn", "al jazeera", "ndtv", "indian express", "the hindu", "times of india", "pbs", "npr", "hindustan times", "india today", "dw", "france24"]
}


def classify_news_genre(text: str) -> str:
    """Detect the genre of the news claim."""
    text_lower = text.lower()
    scores = {genre: 0 for genre in NEWS_GENRES}
    
    for genre, keywords in NEWS_GENRES.items():
        for kw in keywords:
            if f" {kw} " in f" {text_lower} " or text_lower.startswith(kw) or text_lower.endswith(kw):
                scores[genre] += 1
                
    # Get genre with max score
    best_genre = max(scores, key=scores.get)
    if scores[best_genre] > 0:
        return best_genre
    return "General"


def is_trusted_source(source_name: str, genre: str) -> bool:
    """Check if the source is authoritative for the specific genre."""
    if not source_name:
        return False
    
    s_lower = source_name.lower()
    
    # Check specific genre list
    if genre in GENRE_TRUSTED_SOURCES:
        for trusted in GENRE_TRUSTED_SOURCES[genre]:
            if trusted in s_lower:
                return True
                
    # Always check General trusted sources as fallback backup
    for trusted in GENRE_TRUSTED_SOURCES["General"]:
        if trusted in s_lower:
            return True
            
    return False


@app.get("/city/news")
async def get_city_news(place: str, limit: int = 6):
    place = (place or "").strip()
    if not place:
        return {"articles": []}

    cache_key = f"{place.lower()}:{limit}"
    cached_entry = local_news_cache.get(cache_key)
    if cached_entry and datetime.now() - cached_entry["timestamp"] < timedelta(seconds=LOCAL_NEWS_CACHE_EXPIRY):
        return cached_entry["data"]

    articles = await fetch_live_local_articles(place, limit)
    insight = await summarize_articles_for_place(f"Recent news about {place}", articles[:5]) if articles else None
    payload = {"articles": articles[:limit], "insight": insight}
    local_news_cache[cache_key] = {"timestamp": datetime.now(), "data": payload}
    return payload

# News Verification Endpoint
@app.post("/api/verify-news")
async def verify_news(request: NewsVerificationRequest):
    """Verify news by searching recent articles from NewsAPI and using Perplexity AI"""
    print(f"Verifying claim: {request.text}")
    
    # 1. Sensitive Topic Detection
    is_sensitive, sensitivity_reason = is_sensitive_topic(request.text)
    print(f"Sensitive topic: {is_sensitive} ({sensitivity_reason})")
    
    # 2. Classification & Source Prioritization
    news_genre = classify_news_genre(request.text)
    print(f"Detected genre: {news_genre}")
    
    # 3. Gather Sources (NewsAPI + RSS Fallback) - Get MORE sources for sensitive topics
    sources = []
    
    # Try NewsAPI first
    # Try NewsAPI first
    if NEWSAPI_KEY:
        try:
            async with httpx.AsyncClient() as client:
                search_url = "https://newsapi.org/v2/everything"
                
                # Smart query for long text
                q_param = request.text
                if len(q_param) > 80:
                    terms = extract_focus_terms(q_param, limit=5)
                    if len(terms) >= 2:
                        q_param = " OR ".join(terms)
                    else:
                         q_param = request.text[:100]
                else:
                    q_param = request.text
                
                params = {
                    "q": q_param,
                    "apiKey": NEWSAPI_KEY,
                    "pageSize": 25 if is_sensitive else 15,
                    "sortBy": "relevancy",
                    "language": "en"
                }
                resp = await client.get(search_url, params=params, timeout=6.0)
                if resp.status_code == 200:
                    data = resp.json()
                    for art in data.get("articles", []):
                        if art.get("title") and art.get("url"):
                            src_name = art.get("source", {}).get("name", "Unknown")
                            # Boost priority if source matches genre
                            is_trusted = is_trusted_source(src_name, news_genre)
                            
                            sources.append({
                                "title": art.get("title"),
                                "url": art.get("url"),
                                "publishedAt": art.get("publishedAt"),
                                "source": src_name,
                                "description": art.get("description", ""),
                                "is_trusted": is_trusted
                            })
        except Exception as e:
            print(f"NewsAPI error: {e}")

    # Fallback to RSS if low results (require more sources for sensitive topics)
    min_sources = 5 if is_sensitive else 3
    if len(sources) < min_sources:
        print("Falling back to RSS search...")
        rss_sources = await rss_search_articles(request.text, 10 if is_sensitive else 5)
        # Deduplicate by URL and mark trusted sources
        existing_urls = {s["url"] for s in sources}
        for s in rss_sources:
            if s["url"] not in existing_urls:
                # Mark RSS sources as trusted if they match genre
                s["is_trusted"] = is_trusted_source(s.get("source", ""), news_genre)
                sources.append(s)

    # 4. AI Verification with Context and Sensitivity Flag
    ai_verdict = None
    if PERPLEXITY_API_KEY:
        print("Calling Perplexity for verification...")
        # Pass sensitivity flag to AI for stricter verification
        ai_verdict = await run_perplexity_fact_check(request.text, sources, is_sensitive=is_sensitive)

    # 5. Formulate Response with Ensemble Logic
    final_label = "uncertain"
    final_confidence = 0.0
    explanation = "Insufficient evidence found to verify this claim."

    # --- Ensemble Logic for Final Confidence ---
    
    # Check for trusted sources match count
    trusted_source_count = sum(1 for s in sources if s.get("is_trusted", False))
    print(f"Trusted sources found: {trusted_source_count}")
    
    if ai_verdict:
        # Perplexity response (trust high confidence)
        p_label = classify_perplexity_label(ai_verdict.get("verdict") or ai_verdict.get("label"))
        p_conf = ai_verdict.get("confidence")
        
        # Parse confidence safely
        try:
            p_conf = float(p_conf)
        except:
            p_conf = 0.0

        explanation = ai_verdict.get("explanation", explanation)
        
        # HYBRID ADJUSTMENT:
        # If AI says TRUE and we have trusted sources supporting it -> Boost to 95-100%
        # If AI says TRUE but no trusted sources -> Cap at 85%
        # If AI says FALSE -> Trust AI but check if trusted sources contradict (unlikely for Perplexity)
        
        # SENSITIVE TOPIC HANDLING - Much stricter requirements
        if is_sensitive:
            if p_label == "true":
                # For sensitive topics, require MULTIPLE trusted sources AND high AI confidence
                if trusted_source_count >= 3 and p_conf >= 0.8:
                    final_confidence = min(0.75, p_conf)  # Cap at 75% for sensitive topics
                    final_label = "true"
                    explanation += f" Verified by {trusted_source_count} independent authoritative sources."
                elif trusted_source_count >= 2:
                    final_confidence = 0.65
                    final_label = "true"
                    explanation += f" Confirmed by multiple sources, but treating cautiously due to sensitive nature."
                else:
                    # Not enough trusted sources for sensitive claim
                    final_confidence = 0.45
                    final_label = "uncertain"
                    explanation = f"Sensitive claim requires multiple independent trusted sources. Found only {trusted_source_count}. " + explanation
                    
            elif p_label == "false":
                final_confidence = min(0.85, p_conf + 0.1)
                final_label = "false"
                explanation += " Flagged as false/misleading for sensitive topic."
            else:
                final_label = "uncertain"
                final_confidence = 0.4
                explanation = f"Sensitive/controversial claim cannot be verified with available evidence. {explanation}"
        
        # REGULAR TOPIC HANDLING
        else:
            if p_label == "true":
                base_conf = max(p_conf, 0.7)
                if trusted_source_count >= 1:
                    final_confidence = min(0.99, base_conf + 0.15 + (trusted_source_count * 0.05))
                    explanation += f" Confirmed by authoritative {news_genre} sources."
                else:
                    final_confidence = min(0.85, base_conf)
                final_label = "true"
                
            elif p_label == "false":
                base_conf = max(p_conf, 0.7)
                if trusted_source_count >= 1:
                    final_confidence = 0.6
                    final_label = "uncertain"
                    explanation += f" AI flags this as partial/false despite some source matches."
                else:
                    final_confidence = min(0.95, base_conf + 0.1)
                final_label = "false"
                
            else:
                final_label = "uncertain"
                final_confidence = 0.5
                if trusted_source_count >= 2:
                    final_label = "true"
                    final_confidence = 0.82
                    explanation = f"AI was uncertain, but multiple trusted {news_genre} sources confirm this."

    else:
        # Fallback without AI - Even stricter for sensitive topics
        if is_sensitive:
            if trusted_source_count >= 3:
                final_label = "true"
                final_confidence = 0.70
                explanation = f"Sensitive claim verified by {trusted_source_count} independent trusted sources (AI unavailable)."
            else:
                final_label = "uncertain"
                final_confidence = 0.35
                explanation = f"Sensitive claim requires multiple trusted sources. Only {trusted_source_count} found. Cannot verify without AI."
        else:
            if trusted_source_count >= 2:
                final_label = "true"
                final_confidence = 0.92
                explanation = f"Verified by multiple trusted {news_genre} sources."
            elif trusted_source_count == 1:
                final_label = "true"
                final_confidence = 0.75
                explanation = f"Verified by a trusted {news_genre} source."
            elif sources:
                final_label = "uncertain"
                final_confidence = 0.55
                explanation = "Found sources but none from the high-priority trusted list."
            else:
                 explanation = "No verification available."

    # Final Calibration (Squash 40-60% to 0-20% or 80-100% where possible)
    if final_confidence > 0.7:
        # Push high confidence higher
        final_confidence = min(0.99, final_confidence + 0.05)
    elif final_confidence < 0.3:
        # Push low confidence lower (if false)
         pass
    elif final_label == "true" and final_confidence < 0.7:
        # If explicitly labeled true but low confidence, boost it if generic sources exist
        if len(sources) > 5:
            final_confidence = 0.8
            
    # Safe guard
    if final_confidence < 0.3:
        # Unless it's explicitly FALSE
        if final_label != "false":
             final_label = "uncertain"

    # Construct final object
    return {
        "verdict": final_label, # 'true', 'false', 'uncertain' (frontend maps this)
        "confidence": final_confidence,
        "explanation": explanation,
        "sources": sources[:10],
        # Frontend often expects these keys for the 'final' display
        "final_label": final_label,
        "final_confidence": final_confidence,
        "final_message": build_final_message(final_label, final_confidence)
    }


@app.get("/")
async def serve_frontend_root():
    build_index_path = os.path.join("frontend", "build", "index.html")
    if os.path.exists(build_index_path):
        return FileResponse(build_index_path)
    return {"message": "Frontend build not found. Please run 'npm run build' inside the frontend folder."}
