"""FastAPI application factory.

Dependencies are injected rather than imported, so tests substitute a stub
answer function and an in-memory database without touching a model or a
vector store. `answer_fn` in particular is expected to be built ONCE, at
process startup, by the caller (Task 5's `build_answer_fn`) -- it closes over
a `Retriever` whose query embedder (`openlaw.retrieval.factory.
build_query_embedder`, local `voyage-4-nano` by default) takes roughly 2.5
seconds to load. `create_app` never constructs or reloads that embedder
itself; it only ever calls the `answer_fn` it was handed, once per request,
so the expensive load happens exactly once for the life of the process
rather than once per question.

What a caller sees when something goes wrong, deliberately: a structured
quota/rate-limit body on 429 (`escalation_footer`, and `crisis_response` when
warranted, on BOTH kinds of 429 -- see `openlaw.api.refusal`'s module
docstring), an invalid or expired bearer token falling back to the anonymous
allowance rather than a bare 401 (see `routes._authenticated_decision`), and
-- for anything unforeseen (a database outage, a retrieval or model-provider
failure) -- a fixed generic 500 body that STILL carries the same
`escalation_footer`/`crisis_response` (Task 4 review, M4: a retrieval or
model outage is the failure most likely to be sustained, so "try again" is
not enough on its own). The real exception is always logged server-side
with a traceback; the client never sees one.

**Anonymous quota is keyed by an opaque cookie, not the caller's IP (Task 4
review, C1/C1b).** `_ensure_anonymous_token` middleware below reads (or, on
a caller's first visit, mints and sets) a long-lived, `HttpOnly` cookie
before the request reaches any route, and stores it on `request.state
.anon_token` for `routes._anonymous_decision` to key the allowance by. Using
middleware rather than setting the cookie inside the `/ask` handler is what
makes the token survive a refusal: a route that raises `HTTPException` never
gets to touch the response object FastAPI would otherwise let it decorate,
but middleware wraps the ENTIRE call, success or exception, and always sees
the final response -- so a first-time caller gets their token even on the
very request that refuses them.
"""

import logging
import secrets
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from openlaw.api.refusal import crisis_extras, refusal_detail
from openlaw.api.routes import router
from openlaw.config import Settings

logger = logging.getLogger(__name__)

#: Cookie name and lifetime for the anonymous-quota token (see the module
#: docstring). A year is generous on purpose: this is a cost-control
#: convenience, not a security boundary -- a cleared cookie simply starts a
#: fresh allowance, which is an accepted trade-off (the IP rate limit still
#: bounds abuse regardless).
ANON_COOKIE_NAME = "openlaw_anon_id"
_ANON_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def _register_anonymous_token_middleware(app: FastAPI) -> None:
    """Attach (and, on a first visit, mint) the anonymous-quota cookie.

    Middleware, not code inside the `/ask` handler -- see the module
    docstring's "Anonymous quota is keyed by an opaque cookie" note for why
    that is what makes the token survive a refusal.
    """

    @app.middleware("http")
    async def _ensure_anonymous_token(request: Request, call_next: Callable):
        existing = request.cookies.get(ANON_COOKIE_NAME)
        request.state.anon_token = existing or secrets.token_urlsafe(16)
        response = await call_next(request)
        if not existing:
            response.set_cookie(
                ANON_COOKIE_NAME,
                request.state.anon_token,
                max_age=_ANON_COOKIE_MAX_AGE_SECONDS,
                httponly=True,
                samesite="lax",
            )
        return response


def _http_error_content(exc: StarletteHTTPException) -> dict:
    """Return the JSON body for `exc.detail` -- structured (quota/rate-limit)
    or a bare string, wrapped the same way FastAPI's default handler would."""
    return exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}


def _unhandled_error_content(request: Request) -> dict:
    """Build the fixed 500 body -- generic detail plus the same escalation
    footer (and crisis helplines, when warranted) every refusal path
    carries (Task 4 review, M4): an outage is the failure most likely to be
    sustained, so "try again" alone would leave the caller with no route to
    a human. Reads `request.state.question`/`.state`/`.language`, stashed by
    `routes.ask` before it could fail -- absent for a failure outside that
    handler (e.g. `/health`), in which case the footer alone still goes out.
    """
    question = getattr(request.state, "question", "")
    state = getattr(request.state, "state", None)
    language = getattr(request.state, "language", None)
    footer, crisis = crisis_extras(question, state)
    return {
        "detail": refusal_detail("server_error", language),
        "escalation_footer": footer,
        "crisis_response": crisis,
    }


def _register_error_handlers(app: FastAPI) -> None:
    """Wire up the two response shapes described in the module docstring:
    structured HTTPExceptions (with their headers, e.g. `Retry-After`,
    intact) and a fixed, crisis-aware body for anything unforeseen."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_http_error_content(exc),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """Never let an unexpected failure leak a traceback or exception
        text. The real exception (with its traceback) is logged
        server-side, where it is actually actionable; nothing from it
        reaches the response body."""
        logger.exception("unhandled error while answering a request")
        return JSONResponse(status_code=500, content=_unhandled_error_content(request))


def create_app(
    answer_fn: Callable, session_factory: Callable, settings: Settings
) -> FastAPI:
    """Build the application with its dependencies wired in."""
    app = FastAPI(title="OpenLaw.in", version="0.1.0")
    app.state.answer_fn = answer_fn
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.anonymous_used = {}
    app.state.rate_limit_hits = {}
    app.include_router(router)

    _register_anonymous_token_middleware(app)
    _register_error_handlers(app)

    return app
