from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

from fastapi import Depends

from app.api import (activity, auth, customers, ingest, items, orders, qra,
                     queue, review)
from app.api.deps import require_api_key

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Voice Order Intake", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
# /customers/all dumps the full ~40k-row customer table for the Android
# app's offline cache - gzip cuts that transfer size substantially on a
# field salesman's mobile connection. Cheap for every other (much smaller)
# response too, so applied app-wide rather than to one route.
app.add_middleware(GZipMiddleware, minimum_size=1000)
_guard = [Depends(require_api_key)]
app.include_router(ingest.router, dependencies=_guard)
app.include_router(items.router, dependencies=_guard)
app.include_router(customers.router, dependencies=_guard)
app.include_router(qra.router, dependencies=_guard)
app.include_router(orders.router, dependencies=_guard)
app.include_router(queue.router, dependencies=_guard)
app.include_router(review.router, dependencies=_guard)
app.include_router(activity.router, dependencies=_guard)
app.include_router(auth.router, dependencies=_guard)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/console")
def console():
    return FileResponse(STATIC_DIR / "console.html")


@app.get("/record")
def record():
    return FileResponse(STATIC_DIR / "record.html")
