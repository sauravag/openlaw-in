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
quota/rate-limit body on 429, a fixed generic string on 401 (see
`openlaw.api.auth`'s module docstring for why `AuthError.detail` must never
reach here), and -- for anything unforeseen (a database outage, a retrieval
or model-provider failure) -- a fixed generic 500 body. The real exception is
logged server-side with a traceback; the client never sees one.
"""

import logging
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from openlaw.api.routes import router
from openlaw.config import Settings

logger = logging.getLogger(__name__)

_GENERIC_SERVER_ERROR = "Something went wrong on our end. Please try again."


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

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Return structured quota/rate-limit errors rather than a bare string."""
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """Never let an unexpected failure leak a traceback or exception text.

        Everything that reaches here is, by definition, something this
        codebase did not anticipate and could not turn into a specific,
        user-meaningful HTTPException -- a database outage, an unreachable
        model provider, a bug. The caller gets one fixed, generic message;
        the real exception (with its traceback) is logged server-side, where
        it is actually actionable.
        """
        logger.exception("unhandled error while answering a request")
        return JSONResponse(
            status_code=500, content={"detail": _GENERIC_SERVER_ERROR}
        )

    return app
