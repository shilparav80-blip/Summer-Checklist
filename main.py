import asyncio
import calendar as _calendar
import hmac
import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "summer2024")
SECRET_KEY = os.getenv("SECRET_KEY", "summer-checklist-dev-key-change-me")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE", "star_quest_profiles")

app = FastAPI(title="Summer Checklist")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

_BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

# Vercel vendors its own Jinja2 (via _vendor/) regardless of requirements.txt.
# That version has cascading argument-order bugs: get_template → _load_template →
# loader.load → loader.get_source all pass wrong types to each other.
# Fix: replace env.get_template with an implementation that reads template files
# directly from disk using pathlib, then compiles via env.compile + from_code.
# env.compile and Template.from_code are pure Jinja2 compiler methods — no loader
# involved — so they are unaffected by the vendored loader bugs.
import types as _types
import jinja2 as _jinja2

def _safe_get_template(env_self, name, parent=None, globals=None):
    if isinstance(name, _jinja2.Template):
        return name
    if not isinstance(name, str):
        raise _jinja2.TemplateNotFound(repr(name))
    if parent is not None:
        try:
            name = env_self.join_path(name, parent)
        except Exception:
            pass
    tpl_path = _BASE_DIR / "templates" / name
    if not tpl_path.is_file():
        raise _jinja2.TemplateNotFound(name)
    source = tpl_path.read_text(encoding="utf-8")
    code = env_self.compile(source, name, str(tpl_path))
    return env_self.template_class.from_code(
        env_self, code, dict(getattr(env_self, "globals", {})), None
    )

templates.env.get_template = _types.MethodType(_safe_get_template, templates.env)
templates.env.cache = None


def render_template(request: Request, name: str, context: dict, status_code: int = 200):
    context = {"request": request, **context}
    try:
        return templates.TemplateResponse(request, name, context, status_code=status_code)
    except TypeError:
        return templates.TemplateResponse(name, context, status_code=status_code)

# Vercel's project root is read-only; /tmp is writable (ephemeral between cold starts).
# Locally we keep data/ as before.
DATA_FILE = (
    Path("/tmp/activities.json") if os.getenv("VERCEL")
    else _BASE_DIR / "data" / "activities.json"
)
PROFILE_FILE = (
    Path("/tmp/star_quest_profiles.json") if os.getenv("VERCEL")
    else _BASE_DIR / "data" / "star_quest_profiles.json"
)

# In-memory fallback used when both /tmp and data/ are unavailable.
_activities_mem: Optional[list] = None

CATEGORIES = ["Beach", "Outdoors", "Food & Drinks", "Travel", "Social", "Water"]

CATEGORY_META = {
    "Beach":         {"cls": "bg-blue-100 text-blue-700",    "emoji": "🏖️"},
    "Outdoors":      {"cls": "bg-green-100 text-green-700",  "emoji": "🌿"},
    "Food & Drinks": {"cls": "bg-orange-100 text-orange-700","emoji": "🍦"},
    "Travel":        {"cls": "bg-purple-100 text-purple-700","emoji": "✈️"},
    "Social":        {"cls": "bg-pink-100 text-pink-700",    "emoji": "🎉"},
    "Water":         {"cls": "bg-cyan-100 text-cyan-700",    "emoji": "🏊"},
}

DEFAULT_ACTIVITIES = [
    ("Watch a sunset at the beach",   "Beach"),
    ("Try a new ice cream flavor",    "Food & Drinks"),
    ("Go on a hike",                  "Outdoors"),
    ("Have a bonfire night",          "Social"),
    ("Go swimming in the ocean",      "Water"),
    ("Visit a new city",              "Travel"),
    ("Have a picnic in the park",     "Outdoors"),
    ("Try paddleboarding",            "Water"),
    ("Eat at an outdoor restaurant",  "Food & Drinks"),
    ("Read a book on the beach",      "Beach"),
    ("Road trip with friends",        "Travel"),
    ("Host a backyard BBQ",           "Social"),
]

_hero_cache: dict = {"url": None, "expires": 0.0}

# Per-activity photo cache:  name -> (url, expires_at)
_photo_cache: dict[str, tuple[str, float]] = {}

# Better search terms for known activities
_PHOTO_QUERIES: dict[str, str] = {
    "rsm":    "children math tutoring classroom",
    "kumon":  "kids studying worksheets homework",
    "dance":  "children ballet dance class",
    "shloka": "kids meditation prayer yoga",
    "music":  "children music lesson singing",
    "piano":  "piano practice lesson keys",
    "swim":   "children swimming pool",
    "soccer": "kids soccer practice field",
    "art":    "children painting art class",
    "read":   "child reading book",
    "guitar": "kid playing guitar",
    "coding": "kids coding computer class",
}


async def get_activity_photo(name: str) -> Optional[str]:
    now = time.time()
    cached = _photo_cache.get(name)
    if cached and cached[1] > now:
        return cached[0]
    if not PEXELS_API_KEY:
        return None
    query = _PHOTO_QUERIES.get(name.lower(), name)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_API_KEY},
                params={"query": query, "per_page": 8, "orientation": "square"},
            )
            photos = resp.json().get("photos", [])
        if not photos:
            return None
        url: str = random.choice(photos)["src"]["small"]
        _photo_cache[name] = (url, now + 3600)
        return url
    except Exception:
        return None


def cat_style(cat: str) -> dict:
    return CATEGORY_META.get(cat, {"cls": "bg-gray-100 text-gray-600", "emoji": "☀️"})




def load_activities() -> list[dict]:
    global _activities_mem
    if _activities_mem is not None:
        return _activities_mem
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    activities = [
        {"id": str(uuid.uuid4()), "name": name, "category": cat, "completed": False}
        for name, cat in DEFAULT_ACTIVITIES
    ]
    save_activities(activities)
    return activities


def save_activities(activities: list[dict]) -> None:
    global _activities_mem
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(activities, indent=2), encoding="utf-8")
        _activities_mem = None  # disk is authoritative; clear memory copy
    except OSError:
        _activities_mem = activities  # filesystem unavailable — hold in memory


async def get_hero_image() -> Optional[str]:
    now = time.time()
    if _hero_cache["url"] and _hero_cache["expires"] > now:
        return _hero_cache["url"]
    if not PEXELS_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_API_KEY},
                params={"query": "summer beach", "per_page": 10, "orientation": "landscape"},
            )
            photos = resp.json().get("photos", [])
        if not photos:
            return None
        url = random.choice(photos)["src"]["large2x"]
        _hero_cache.update({"url": url, "expires": now + 3600})
        return url
    except Exception:
        return None


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=403, detail="Not authenticated")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse("/checklist", status_code=302)
    return render_template(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == DASHBOARD_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse("/checklist", status_code=302)
    return render_template(request, "login.html", {"error": "Incorrect password"}, status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/manifest.webmanifest")
async def app_manifest():
    return FileResponse(_BASE_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js")
async def service_worker():
    return FileResponse(_BASE_DIR / "static" / "service-worker.js", media_type="application/javascript")


@app.get("/app-icon.svg")
async def app_icon():
    return FileResponse(_BASE_DIR / "static" / "app-icon.svg", media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    return render_template(request, "index.html", {})
    activities = load_activities()
    hero_image = await get_hero_image()
    completed = sum(1 for a in activities if a["completed"])
    return render_template(request, "index.html", {
        "activities": activities,
        "categories": CATEGORIES,
        "completed": completed,
        "total": len(activities),
        "hero_image": hero_image,
        "cat_style": cat_style,
    })


@app.post("/activities")
async def add_activity(request: Request, name: str = Form(...), category: str = Form(...)):
    require_auth(request)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    activities = load_activities()
    activities.append({"id": str(uuid.uuid4()), "name": name, "category": category, "completed": False})
    save_activities(activities)
    return RedirectResponse("/", status_code=302)


@app.post("/activities/{activity_id}/toggle")
async def toggle_activity(activity_id: str, request: Request):
    require_auth(request)
    activities = load_activities()
    for a in activities:
        if a["id"] == activity_id:
            a["completed"] = not a["completed"]
            save_activities(activities)
            return JSONResponse({"ok": True, "completed": a["completed"]})
    raise HTTPException(status_code=404, detail="Activity not found")


@app.delete("/activities/{activity_id}")
async def delete_activity(activity_id: str, request: Request):
    require_auth(request)
    activities = load_activities()
    new_list = [a for a in activities if a["id"] != activity_id]
    if len(new_list) == len(activities):
        raise HTTPException(status_code=404, detail="Activity not found")
    save_activities(new_list)
    return JSONResponse({"ok": True})


# ── Checklist page ────────────────────────────────────────────────────────────

CHECKLIST_ACTIVITIES = [
    {"name": "Make bed", "badge": "BED", "icon": "lotus", "frequency": "daily", "stars": 2},
    {"name": "Brush before bed", "badge": "BRUSH", "icon": "diya", "frequency": "daily", "stars": 2},
    {"name": "Bath", "badge": "BATH", "icon": "lotus", "frequency": "daily", "stars": 2},
    {"name": "Unload dishwasher", "badge": "DISHES", "icon": "thali", "frequency": "daily", "stars": 2},
    {"name": "RSM", "badge": "MATH", "icon": "mandala", "frequency": "daily", "stars": 3},
    {"name": "Kumon", "badge": "STUDY", "icon": "book", "frequency": "daily", "stars": 3},
    {"name": "Dance", "badge": "DANCE", "icon": "ghungroo", "frequency": "daily", "stars": 3},
    {"name": "Shloka", "badge": "CHANT", "icon": "om", "frequency": "daily", "stars": 3},
    {"name": "Music", "badge": "MUSIC", "icon": "tabla", "frequency": "daily", "stars": 3},
    {"name": "Piano", "badge": "PIANO", "icon": "raga", "frequency": "daily", "stars": 3},
    {"name": "Read a book", "badge": "READ", "icon": "book", "frequency": "daily", "stars": 3},
    {"name": "Mop the house", "badge": "MOP", "icon": "rangoli", "frequency": "weekly", "stars": 5},
    {"name": "Laundry", "badge": "WASH", "icon": "diya", "frequency": "weekly", "stars": 5},
]

_cal = _calendar.Calendar(firstweekday=6)  # Sunday-first

_MONTHS = [
    {"num": 6, "name": "June",   "gradient": "from-rose-400 to-orange-400"},
    {"num": 7, "name": "July",   "gradient": "from-amber-400 to-yellow-300"},
    {"num": 8, "name": "August", "gradient": "from-teal-400 to-cyan-400"},
]

PLAYERS = {
    "aretha": "Aretha",
    "aarav": "Aarav",
    "arjun": "Arjun",
    "adi": "Adi",
}

PLAYER_PINS = {
    "aretha": os.getenv("ARETHA_PIN", "1111"),
    "aarav": os.getenv("AARAV_PIN", "2222"),
    "arjun": os.getenv("ARJUN_PIN", "3333"),
    "adi": os.getenv("ADI_PIN", "4444"),
}

PENALTY_RULES = [
    {"name": "iPad watched for more than 1 hour", "stars": 5},
    {"name": "TV watched for more than 1 hour", "stars": 5},
]


def default_profile_state() -> dict:
    return {"tasks": None, "done": {}, "penalties": {}, "schedule": {}}


def _date_add_days(key: str, days: int) -> str:
    from datetime import date, timedelta

    y, m, d = [int(part) for part in key.split("-")]
    return (date(y, m, d) + timedelta(days=days)).isoformat()


def _week_start_key(key: str) -> str:
    from datetime import date, timedelta

    y, m, d = [int(part) for part in key.split("-")]
    current = date(y, m, d)
    return (current - timedelta(days=current.weekday())).isoformat()


def compute_total_stars(state: dict) -> int:
    tasks = state.get("tasks") or CHECKLIST_ACTIVITIES
    done = state.get("done") or {}
    penalties = state.get("penalties") or {}
    all_keys = set(done) | set(penalties)

    def task_done(task: dict, key: str) -> bool:
        bucket_key = _week_start_key(key) if task.get("frequency") == "weekly" else key
        return task.get("name") in (done.get(bucket_key) or [])

    def penalty_stars(key: str) -> int:
        marked = penalties.get(key) or []
        return sum(int(rule["stars"]) for rule in PENALTY_RULES if rule["name"] in marked)

    total = 0
    for key in all_keys:
        earned = sum(int(task.get("stars") or 1) for task in tasks if task_done(task, key))
        total += max(0, earned - penalty_stars(key))
    return total


def _local_profiles() -> dict:
    if PROFILE_FILE.exists():
        try:
            return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_local_profiles(data: dict) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def _supabase_request(method: str, path: str, json_body: Optional[dict] = None):
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if method.upper() == "POST":
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.request(
            method,
            f"{SUPABASE_URL}/rest/v1/{path}",
            headers=headers,
            json=json_body,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return None


async def load_profile(player_slug: str) -> dict:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        rows = await _supabase_request(
            "GET",
            f"{SUPABASE_TABLE}?select=player_slug,player_name,state,total_stars,updated_at&player_slug=eq.{player_slug}",
        )
        if rows:
            row = rows[0]
            return {
                "player_slug": row["player_slug"],
                "player_name": row["player_name"],
                "state": row.get("state") or default_profile_state(),
                "total_stars": int(row.get("total_stars") or 0),
                "updated_at": row.get("updated_at"),
            }

    local = _local_profiles()
    profile = local.get(player_slug)
    if profile:
        return profile
    return {
        "player_slug": player_slug,
        "player_name": PLAYERS[player_slug],
        "state": default_profile_state(),
        "total_stars": 0,
        "updated_at": None,
    }


async def save_profile(player_slug: str, state: dict) -> dict:
    cleaned_state = {
        "tasks": state.get("tasks"),
        "done": state.get("done") or {},
        "penalties": state.get("penalties") or {},
        "schedule": state.get("schedule") or {},
    }
    total_stars = compute_total_stars(cleaned_state)
    profile = {
        "player_slug": player_slug,
        "player_name": PLAYERS[player_slug],
        "state": cleaned_state,
        "total_stars": total_stars,
    }
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        await _supabase_request(
            "POST",
            f"{SUPABASE_TABLE}?on_conflict=player_slug",
            profile,
        )
    else:
        local = _local_profiles()
        local[player_slug] = {**profile, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _save_local_profiles(local)
    return profile


async def leaderboard() -> list[dict]:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        rows = await _supabase_request(
            "GET",
            f"{SUPABASE_TABLE}?select=player_slug,player_name,total_stars,updated_at&order=total_stars.desc",
        )
        if rows is not None:
            existing = {row["player_slug"]: row for row in rows}
            return [
                {
                    "player_slug": slug,
                    "player_name": existing.get(slug, {}).get("player_name", name),
                    "total_stars": int(existing.get(slug, {}).get("total_stars") or 0),
                    "updated_at": existing.get(slug, {}).get("updated_at"),
                }
                for slug, name in PLAYERS.items()
            ]
    local = _local_profiles()
    return [
        {
            "player_slug": slug,
            "player_name": local.get(slug, {}).get("player_name", name),
            "total_stars": int(local.get(slug, {}).get("total_stars") or 0),
            "updated_at": local.get(slug, {}).get("updated_at"),
        }
        for slug, name in PLAYERS.items()
    ]


def player_unlocked(request: Request, player_slug: str) -> bool:
    return bool(request.session.get(f"player_unlocked:{player_slug}"))


def require_player_unlocked(request: Request, player_slug: str) -> None:
    require_auth(request)
    if player_slug not in PLAYERS or not player_unlocked(request, player_slug):
        raise HTTPException(status_code=403, detail="Player profile is locked")


@app.get("/checklist", response_class=HTMLResponse)
async def checklist_page(request: Request):
    return await checklist_player_page(request, "aretha")


@app.get("/checklist/{player_slug}", response_class=HTMLResponse)
async def checklist_player_page(request: Request, player_slug: str):
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    player_name = PLAYERS.get(player_slug.lower())
    if not player_name:
        return RedirectResponse("/checklist/aretha", status_code=302)
    player_slug = player_slug.lower()
    board = await leaderboard()
    if not player_unlocked(request, player_slug):
        return render_template(request, "child_login.html", {
            "player_name": player_name,
            "player_slug": player_slug,
            "leaderboard": board,
            "error": None,
        })
    profile = await load_profile(player_slug)
    months = [
        {**m, "weeks": _cal.monthdayscalendar(2026, m["num"])}
        for m in _MONTHS
    ]
    return render_template(request, "checklist.html", {
        "months": months,
        "activities": CHECKLIST_ACTIVITIES,
        "activities_js": json.dumps(CHECKLIST_ACTIVITIES),
        "player_name": player_name,
        "player_slug": player_slug,
        "players_js": json.dumps(PLAYERS),
        "state_js": json.dumps(profile["state"]),
        "leaderboard_js": json.dumps(board),
    })


@app.post("/checklist/{player_slug}/unlock")
async def unlock_child_profile(request: Request, player_slug: str, pin: str = Form(...)):
    require_auth(request)
    player_slug = player_slug.lower()
    player_name = PLAYERS.get(player_slug)
    if not player_name:
        return RedirectResponse("/checklist/aretha", status_code=302)
    expected = PLAYER_PINS[player_slug]
    if hmac.compare_digest(pin.strip(), expected):
        request.session[f"player_unlocked:{player_slug}"] = True
        return RedirectResponse(f"/checklist/{player_slug}", status_code=302)
    return render_template(request, "child_login.html", {
        "player_name": player_name,
        "player_slug": player_slug,
        "leaderboard": await leaderboard(),
        "error": "Incorrect PIN",
    }, status_code=401)


@app.get("/api/leaderboard")
async def api_leaderboard(request: Request):
    require_auth(request)
    return JSONResponse({"players": await leaderboard()})


@app.get("/api/players/{player_slug}/state")
async def api_player_state(player_slug: str, request: Request):
    player_slug = player_slug.lower()
    require_player_unlocked(request, player_slug)
    profile = await load_profile(player_slug)
    return JSONResponse({
        "state": profile["state"],
        "total_stars": profile["total_stars"],
        "leaderboard": await leaderboard(),
    })


@app.post("/api/players/{player_slug}/state")
async def api_save_player_state(player_slug: str, request: Request):
    player_slug = player_slug.lower()
    require_player_unlocked(request, player_slug)
    payload = await request.json()
    profile = await save_profile(player_slug, payload.get("state") or {})
    return JSONResponse({
        "ok": True,
        "total_stars": profile["total_stars"],
        "leaderboard": await leaderboard(),
    })


@app.get("/checklist/photos")
async def checklist_photos(request: Request, names: str = ""):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=403, detail="Not authenticated")
    name_list = [n.strip() for n in names.split(",") if n.strip()][:20]
    results: dict[str, str] = {}

    async def _fetch(name: str) -> None:
        url = await get_activity_photo(name)
        if url:
            results[name] = url

    await asyncio.gather(*[_fetch(n) for n in name_list])
    return JSONResponse(results)
