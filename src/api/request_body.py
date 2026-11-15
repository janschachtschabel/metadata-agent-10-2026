"""
Reading and refusing request bodies, shared by the endpoints.

`invalid_body` is the 422 for a body its model refuses, and
`refuse_foreign_options` the one for an option the endpoint does not have.
`JsonObjectRoute` makes the endpoints that declare their body as a model read
it like those that take the raw request: through `parse_json_object`.
"""

from typing import Any, Callable, Coroutine, Iterable

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.responses import Response

from ..services.repository_support import ENVELOPE_KEYS
from ..utils.loggable import shortened
from .deps import parse_json_object


def invalid_body(error: ValidationError) -> HTTPException:
    """
    422 naming each refused field and the reason, nothing else.

    pydantic's own text repeats the input values and links its docs (audit
    S-06); the field path and the message are what a caller can act on.
    """
    problems = "; ".join(
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
        for problem in error.errors()
    )
    return HTTPException(status_code=422, detail=f"Ungültiger Request-Body: {problems}")


def refuse_foreign_options(
    data: dict[str, Any],
    endpoint: str,
    own: Iterable[str],
    other_endpoint: str,
    others: Iterable[str],
) -> None:
    """
    422 for an option a write endpoint does not have (audit 14.11., list R-N).

    /nodes took the options of /upload without a word - filed away as metadata
    fields in the flat form, dropped in the nested one - and wrote the node as
    if they had not been sent; a misspelt option beside `metadata` went the
    same way. Refused: an option only the other endpoint has, in either form,
    and beside a nested record any key that is neither an option nor part of a
    /generate answer. `repository` is ignored everywhere and stays so. In a
    flat body an unknown key cannot be told from a field: it stays one.
    """
    own = set(own)
    foreign = sorted((set(others) - own - {"repository"}) & data.keys())
    if foreign:
        raise HTTPException(
            status_code=422,
            detail=f"Ungültiger Request-Body: {', '.join(foreign)} gibt es nur bei "
            f"{other_endpoint}, {endpoint} kennt diese Option nicht.",
        )
    if not isinstance(data.get("metadata"), dict):
        return
    unknown = sorted(
        key
        for key in data
        if key not in own
        and key not in ENVELOPE_KEYS
        and key not in ("metadata", "repository")
        and not key.startswith("_")
    )
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Ungültiger Request-Body: unbekannte Option neben `metadata`: "
            f"{shortened(', '.join(unknown))}",
        )


class _JsonObjectRequest(Request):
    """A request whose JSON body is read by parse_json_object."""

    async def json(self) -> Any:
        if not hasattr(self, "_json"):
            self._json = parse_json_object(await self.body(), '{"…": "…"}')
        return self._json


class JsonObjectRoute(APIRoute):
    """
    Route of an endpoint whose body FastAPI validates against a model.

    FastAPI parses such a body itself, with Python's defaults: NaN and Infinity
    passed and ended as 500, 502 or a silent null, depending on the endpoint
    (audit 14.11., S-33), and a list was a 422 where the other endpoints say
    400. With this route every body goes through parse_json_object: 400 for
    invalid UTF-8, invalid JSON, NaN/Infinity and anything but an object. The
    OpenAPI document does not depend on it.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def read_as_json_object(request: Request) -> Response:
            request = _JsonObjectRequest(request.scope, request.receive)
            # Read here: FastAPI would turn a client that left before the end
            # of the body into a 400 (see UnexpectedErrorMiddleware).
            await request.body()
            return await handler(request)

        return read_as_json_object
