"""Сборка процесса. После каркаса файл не переписывают.

ТЗ §3.5 «Архитектура», «API (OpenAPI/Swagger)», «Безопасность»; ТЗ §3.3 — стенд работает без сети.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from product.app.api import router as api_router
from product.app.secret import product_secret
from product.app.services.errors import ApiError, explain
from product.app.web.routes import router as web_router

_APP_DIR = Path(__file__).resolve().parent
_PKG_DIR = _APP_DIR.parent


def create_app() -> FastAPI:
    secret = product_secret()
    application = FastAPI(
        title="Агрегатор ИТ-вакансий ФСП",
        version="1.4.1",
        description="Кабинеты соискателя и работодателя, API и один файл SQLite.",
        docs_url=None,
        redoc_url=None,
    )
    application.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="product_session",
        max_age=7 * 24 * 60 * 60,
        same_site="lax",
        https_only=os.environ.get("PRODUCT_COOKIE_SECURE") == "1",
    )

    @application.exception_handler(ApiError)
    async def _api_error(_request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.error, "message": exc.message},
        )

    @application.exception_handler(RequestValidationError)
    async def _bad_json(_request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={"error": "json", "message": explain("json")},
        )

    application.include_router(api_router)
    application.include_router(web_router)
    application.mount("/static", StaticFiles(directory=_APP_DIR / "static"), name="static")
    application.mount("/fonts", StaticFiles(directory=_PKG_DIR / "static" / "fonts"), name="fonts")

    docs_dir = _APP_DIR / "static" / "docs"

    @application.get("/docs", include_in_schema=False)
    def swagger_ui():
        if not (docs_dir / "swagger-ui-bundle.js").is_file() or not (docs_dir / "swagger-ui.css").is_file():
            return JSONResponse(
                status_code=503,
                content={"error": "docs_assets", "message": explain("docs_assets")},
            )
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="ФСП",
            swagger_js_url="/static/docs/swagger-ui-bundle.js",
            swagger_css_url="/static/docs/swagger-ui.css",
            swagger_favicon_url="/static/logo_02w.svg",
            # null отключает онлайн-валидатор swagger.io: страница не зависит от сети.
            swagger_ui_parameters={"validatorUrl": None},
        )

    @application.get("/redoc", include_in_schema=False)
    def redoc_ui():
        if not (docs_dir / "redoc.standalone.js").is_file():
            return JSONResponse(
                status_code=503,
                content={"error": "docs_assets", "message": explain("docs_assets")},
            )
        return get_redoc_html(
            openapi_url="/openapi.json",
            title="ФСП",
            redoc_js_url="/static/docs/redoc.standalone.js",
            redoc_favicon_url="/static/logo_02w.svg",
            with_google_fonts=False,
        )

    def custom_openapi():
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            description=application.description,
            routes=application.routes,
        )
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes["product_session"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "product_session",
        }
        public = {
            ("/api/auth/register", "post"),
            ("/api/auth/confirm", "post"),
            ("/api/auth/confirm/resend", "post"),
            ("/api/auth/login", "post"),
        }
        for path, ops in schema.get("paths", {}).items():
            for method, operation in ops.items():
                if method not in {"get", "post", "put", "delete", "patch"}:
                    continue
                if path.startswith("/api") and (path, method) not in public:
                    operation["security"] = [{"product_session": []}]
        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi
    return application


app = create_app()
