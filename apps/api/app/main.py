from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.errors import install_error_handlers
from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.v1.router import api_router
from app.auth.rate_limit import SlidingWindowLimiter
from app.auth.tokens import get_signing_keys
from app.core.config import get_settings
from app.core.crypto import get_keyring
from app.core.logging import configure_logging
from app.db.session import dispose_engine

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Fail fast at startup on malformed keys instead of on the first request.
    get_keyring()
    get_signing_keys()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.login_limiter = SlidingWindowLimiter(settings.login_rate_per_minute, 60)
    app.state.password_limiter = SlidingWindowLimiter(5, 15 * 60)

    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "If-Match",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Requested-With",
        ],
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith((settings.api_prefix, "/internal/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(internal_router)
    return app


app = create_app()
