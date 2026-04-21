"""
xPts Engine — FastAPI application.

Run with:
    uvicorn api.main:app --reload --port 8001

Or from project root:
    py -3 -m uvicorn api.main:app --reload --port 8001
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from api.routes import router

app = FastAPI(
    title="xPts Engine",
    description="Expected Fantasy Points calculator for UCL Fantasy & FIFA WC Fantasy",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {"status": "ok", "message": "xPts Engine API v1.0"}
