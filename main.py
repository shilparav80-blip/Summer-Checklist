import asyncio
import calendar as _calendar
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
    return RedirectResponse("/checklist", status_code=302)
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


@app.get("/checklist", response_class=HTMLResponse)
async def checklist_page(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    months = [
        {**m, "weeks": _cal.monthdayscalendar(2026, m["num"])}
        for m in _MONTHS
    ]
    return render_template(request, "checklist.html", {
        "months": months,
        "activities": CHECKLIST_ACTIVITIES,
        "activities_js": json.dumps(CHECKLIST_ACTIVITIES),
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
