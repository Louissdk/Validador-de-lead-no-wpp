from app.web import CSRF_TOKEN, _validate_csrf, app


def test_draft_review_routes_are_registered():
    paths = {route.path for route in app.routes}
    assert "/drafts" in paths
    assert "/drafts/generate" in paths
    assert "/drafts/export.csv" in paths


def test_review_forms_require_csrf_token():
    _validate_csrf({"csrf_token": CSRF_TOKEN})


def test_review_forms_reject_invalid_csrf_token():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _validate_csrf({"csrf_token": "wrong"})
    assert exc.value.status_code == 403
