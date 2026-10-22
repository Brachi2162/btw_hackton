from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from vera import __version__
from vera.api.ops_routes import router as ops_router
from vera.api.routes import router
from vera.assistant import Assistant
from vera.batch import BatchService
from vera.config import get_settings
from vera.services import Services, build_services


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    services = build_services(get_settings())
    app.state.services = services
    app.state.batches = BatchService(services)
    app.state.assistant = Assistant(app.state.batches)
    try:
        yield
    finally:
        await services.close()


app = FastAPI(title="VERA API", version=__version__, lifespan=lifespan)
app.include_router(router)
app.include_router(ops_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    version: str
    database: bool
    storage_backend: str
    job_backend: str
    vlm_mode: str


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    services: Services = request.app.state.services
    s = services.settings
    return HealthResponse(
        status="ok",
        version=__version__,
        database=services.repo.ping(),
        storage_backend=s.storage_backend,
        job_backend=s.job_backend,
        vlm_mode=s.vlm_mode,
    )
