"""The translation endpoint serves exactly the languages the widget ships."""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api.routers import widget as widget_router


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_shipped_languages_are_served(lang):
    assert TestClient(main.app).get(f"/widget/i18n/{lang}.json").status_code == 200


# A name of more than 8 characters is refused as a parameter (audit 14.11., S-34).
@pytest.mark.parametrize(
    "lang, status",
    [("fr", 404), ("..%5C..%5Cpyproject", 422), ("..%5Cde", 404), ("DE", 404)],
)
def test_anything_else_is_refused_without_touching_the_filesystem(
    lang, status, monkeypatch
):
    # On Windows a backslash in the path parameter would walk out of the folder.
    touched = []
    monkeypatch.setattr(
        widget_router.Path, "exists", lambda self: touched.append(self) or False
    )

    response = TestClient(main.app).get(f"/widget/i18n/{lang}.json")

    assert response.status_code == status
    assert touched == []
