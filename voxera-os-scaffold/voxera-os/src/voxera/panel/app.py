from __future__ import annotations

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from typing import List, Dict, Any
from ..audit import tail

app = FastAPI(title="Voxera Panel", version="0.1.0")

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)

APPROVALS: List[Dict[str, Any]] = []

@app.get("/", response_class=HTMLResponse)
def home():
    tmpl = templates.get_template("home.html")
    return tmpl.render(approvals=APPROVALS, audit=tail(50))

@app.post("/approvals/add")
def add_approval(title: str = Form(...), detail: str = Form(...)):
    APPROVALS.append({"title": title, "detail": detail})
    return RedirectResponse(url="/", status_code=303)

@app.post("/approvals/clear")
def clear_approvals():
    APPROVALS.clear()
    return RedirectResponse(url="/", status_code=303)
