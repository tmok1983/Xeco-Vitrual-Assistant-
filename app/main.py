from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(title="AI Insurance Sales Assistant", version="0.1.0")
app.include_router(router)
