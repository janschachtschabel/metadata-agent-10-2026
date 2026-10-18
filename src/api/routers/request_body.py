"""The 422 for a request body its model refuses, shared by the write endpoints."""

from fastapi import HTTPException
from pydantic import ValidationError


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
