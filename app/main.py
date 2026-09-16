"""FastAPI application: upload → map → review → export."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__, services
from app.ai import UNAVAILABLE_MESSAGE, GeminiClient
from app.categories import ALL_CATEGORIES
from app.columns import ColumnMapping
from app.config import settings
from app.db import PendingUpload, Statement, Transaction, get_session, init_db
from app.export import export_workbook
from app.importer import SUPPORTED_EXTENSIONS, StatementImportError
from app.transform import TransformError

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(categories=ALL_CATEGORIES, version=__version__)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# Quiet down access logs so request bodies/filenames never end up in logs by accident.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Statement Splitter", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

DB = Annotated[Session, Depends(get_session)]


def _ctx(request: Request, session: Session, **extra) -> dict:
    return {
        "request": request,
        "ai_configured": settings.ai_configured,
        "ai_enabled": services.ai_enabled(session),
        "ai_unavailable_message": UNAVAILABLE_MESSAGE,
        "ai_model": settings.gemini_model,
        **extra,
    }


def _statement_or_404(session: Session, statement_id: str) -> Statement:
    statement = session.get(Statement, statement_id)
    if statement is None:
        raise HTTPException(404, "Statement not found")
    return statement


def _filter(value: str | None) -> str:
    return value if value in services.FILTERS else "all"


# ---------------------------------------------------------------- home / upload
@app.get("/", response_class=HTMLResponse)
def home(request: Request, session: DB, error: str | None = None):
    statements = session.scalars(select(Statement).order_by(Statement.created_at.desc()).limit(20)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        _ctx(request, session, statements=statements, error=error, extensions=SUPPORTED_EXTENSIONS),
    )


@app.post("/upload")
async def upload(session: DB, file: UploadFile = File(...)):
    filename = file.filename or "statement.csv"
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        return RedirectResponse(f"/?error={quote(f'File is larger than {settings.max_upload_mb} MB')}", 303)
    if not data:
        return RedirectResponse(f"/?error={quote('The uploaded file is empty')}", 303)
    try:
        pending, mapping = services.create_pending_upload(session, filename, data)
    except StatementImportError as exc:
        return RedirectResponse(f"/?error={quote(str(exc))}", 303)
    del data  # drop the raw bytes as early as possible
    if mapping.confident:
        try:
            statement = services.finalize_upload(session, pending, mapping)
        except TransformError:
            return RedirectResponse(f"/uploads/{pending.id}/map", 303)
        return RedirectResponse(f"/statements/{statement.id}", 303)
    return RedirectResponse(f"/uploads/{pending.id}/map", 303)


# ---------------------------------------------------------------- column mapping
@app.get("/uploads/{pending_id}/map", response_class=HTMLResponse)
def mapping_form(request: Request, session: DB, pending_id: str, error: str | None = None):
    pending = session.get(PendingUpload, pending_id)
    if pending is None:
        raise HTTPException(404, "Upload not found or already processed")
    columns, rows = services.pending_table(pending)
    detected = ColumnMapping.model_validate_json(pending.detected_json)
    return templates.TemplateResponse(
        request,
        "mapping.html",
        _ctx(
            request,
            session,
            pending=pending,
            columns=columns,
            preview=rows[:8],
            row_count=len(rows),
            detected=detected.model_dump(),
            error=error,
        ),
    )


@app.post("/uploads/{pending_id}/map")
def mapping_submit(
    session: DB,
    pending_id: str,
    date: str = Form(""),
    description: str = Form(""),
    amount: str = Form(""),
    currency: str = Form(""),
    debit: str = Form(""),
    credit: str = Form(""),
    direction: str = Form(""),
    default_currency: str = Form("ILS"),
):
    pending = session.get(PendingUpload, pending_id)
    if pending is None:
        raise HTTPException(404, "Upload not found or already processed")
    mapping = ColumnMapping(
        date=date or None,
        description=description or None,
        amount=amount or None,
        currency=currency or None,
        debit=debit or None,
        credit=credit or None,
        direction=direction or None,
        default_currency=(default_currency or "ILS")[:3],
        confident=True,
    )
    if not mapping.is_complete:
        msg = "Please map: " + ", ".join(mapping.missing())
        return RedirectResponse(f"/uploads/{pending_id}/map?error={quote(msg)}", 303)
    try:
        statement = services.finalize_upload(session, pending, mapping)
    except TransformError as exc:
        return RedirectResponse(f"/uploads/{pending_id}/map?error={quote(str(exc))}", 303)
    return RedirectResponse(f"/statements/{statement.id}", 303)


# ---------------------------------------------------------------- review
@app.get("/statements/{statement_id}", response_class=HTMLResponse)
def review(request: Request, session: DB, statement_id: str, filter: str | None = None):
    statement = _statement_or_404(session, statement_id)
    flt = _filter(filter)
    return templates.TemplateResponse(
        request,
        "review.html",
        _ctx(
            request,
            session,
            statement=statement,
            summary=services.statement_summary(session, statement_id),
            transactions=services.list_transactions(session, statement_id, flt),
            current_filter=flt,
        ),
    )


@app.get("/statements/{statement_id}/table", response_class=HTMLResponse)
def review_table(request: Request, session: DB, statement_id: str, filter: str | None = None):
    _statement_or_404(session, statement_id)
    flt = _filter(filter)
    return templates.TemplateResponse(
        request,
        "partials/table.html",
        {
            "request": request,
            "statement_id": statement_id,
            "transactions": services.list_transactions(session, statement_id, flt),
            "summary": services.statement_summary(session, statement_id),
            "current_filter": flt,
        },
    )


@app.post("/transactions/{tx_id}/category", response_class=HTMLResponse)
def change_category(
    request: Request,
    session: DB,
    tx_id: int,
    category: str = Form(...),
    remember: str = Form("off"),
    filter: str = Form("all"),
):
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, "Transaction not found")
    try:
        services.set_category(session, tx, category, remember=remember == "on")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    flt = _filter(filter)
    return templates.TemplateResponse(
        request,
        "partials/table.html",
        {
            "request": request,
            "statement_id": tx.statement_id,
            "transactions": services.list_transactions(session, tx.statement_id, flt),
            "summary": services.statement_summary(session, tx.statement_id),
            "current_filter": flt,
            "flash": f"'{tx.normalized_merchant}' → {category}"
            + (" · merchant rule saved" if remember == "on" else " (this transaction only)"),
        },
    )


@app.post("/statements/{statement_id}/apply-similar", response_class=HTMLResponse)
def apply_similar(
    request: Request,
    session: DB,
    statement_id: str,
    merchant_key: str = Form(...),
    category: str = Form(...),
    filter: str = Form("all"),
):
    _statement_or_404(session, statement_id)
    try:
        count = services.apply_to_similar(session, statement_id, merchant_key, category)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    flt = _filter(filter)
    return templates.TemplateResponse(
        request,
        "partials/table.html",
        {
            "request": request,
            "statement_id": statement_id,
            "transactions": services.list_transactions(session, statement_id, flt),
            "summary": services.statement_summary(session, statement_id),
            "current_filter": flt,
            "flash": f"Applied '{category}' to {count} transaction(s) · rule saved for {merchant_key}",
        },
    )


@app.get("/statements/{statement_id}/export")
def export(session: DB, statement_id: str):
    _statement_or_404(session, statement_id)
    data = export_workbook(services.list_transactions(session, statement_id, "all"))
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="categorized-expenses.xlsx"'},
    )


@app.post("/statements/{statement_id}/delete")
def remove_statement(session: DB, statement_id: str):
    services.delete_statement(session, _statement_or_404(session, statement_id))
    return RedirectResponse("/", 303)


# ---------------------------------------------------------------- rules & settings
@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, session: DB):
    return templates.TemplateResponse(request, "rules.html", _ctx(request, session, rules=services.list_rules(session)))


@app.post("/rules/{rule_id}/delete")
def remove_rule(session: DB, rule_id: int):
    services.delete_rule(session, rule_id)
    return RedirectResponse("/rules", 303)


@app.post("/settings/ai")
def toggle_ai(session: DB, use_ai: str = Form("off"), next: str = Form("/")):
    services.set_setting(session, services.USE_AI_KEY, "on" if use_ai == "on" else "off")
    return RedirectResponse(next if next.startswith("/") else "/", 303)


@app.get("/health")
def health(session: DB):
    return {
        "status": "ok",
        "version": __version__,
        "ai_configured": settings.ai_configured,
        "ai_enabled": services.ai_enabled(session),
        "ai_client_available": GeminiClient().available,
    }
