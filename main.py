import json
import os
import random
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "summer2024")
SECRET_KEY = os.getenv("SECRET_KEY", "summer-checklist-dev-key-change-me")

app = FastAPI(title="Summer Checklist")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="templates")

DATA_FILE = Path("data/activities.json")

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


def cat_style(cat: str) -> dict:
    return CATEGORY_META.get(cat, {"cls": "bg-gray-100 text-gray-600", "emoji": "☀️"})


templates.env.globals["cat_style"] = cat_style


def load_activities() -> list[dict]:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    activities = [
        {"id": str(uuid.uuid4()), "name": name, "category": cat, "completed": False}
        for name, cat in DEFAULT_ACTIVITIES
    ]
    save_activities(activities)
    return activities


def save_activities(activities: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(activities, indent=2), encoding="utf-8")


async def get_hero_image() -> str | None:
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
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == DASHBOARD_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Incorrect password"}, status_code=401
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=302)
    activities = load_activities()
    hero_image = await get_hero_image()
    completed = sum(1 for a in activities if a["completed"])
    return templates.TemplateResponse("index.html", {
        "request": request,
        "activities": activities,
        "categories": CATEGORIES,
        "completed": completed,
        "total": len(activities),
        "hero_image": hero_image,
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
