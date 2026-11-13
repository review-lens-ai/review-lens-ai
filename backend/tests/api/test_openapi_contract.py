"""The OpenAPI spec is what the frontend client is generated from (Orval), so it
must describe the API as it really behaves."""

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.errors import register_error_handlers
from app.main import app
from app.schemas.errors import ErrorResponse
from tests.conftest import auth

ERROR_REF = "#/components/schemas/ErrorResponse"


def _api_operations():
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/v1"):
            for method, op in ops.items():
                yield f"{method.upper()} {path}", op


def _schema_ref(op, status):
    return op["responses"][status]["content"]["application/json"]["schema"]["$ref"]


def test_every_api_operation_documents_401_and_422_as_error_response():
    for name, op in _api_operations():
        for status in ("401", "422"):
            assert _schema_ref(op, status) == ERROR_REF, f"{name} {status}"


def test_spec_has_no_fastapi_default_validation_error():
    assert "HTTPValidationError" not in app.openapi()["components"]["schemas"]


def test_operation_ids_are_route_function_names():
    spec = app.openapi()["paths"]
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                assert spec[route.path][method.lower()]["operationId"] == route.name


def test_route_function_names_are_unique():
    # Two routes with the same function name would generate clashing frontend hooks.
    names = [route.name for route in app.routes if isinstance(route, APIRoute)]
    assert len(names) == len(set(names)), names


def test_401_body_matches_error_response(client):
    response = client.get("/api/v1/targets")

    assert response.status_code == 401
    ErrorResponse.model_validate(response.json())


def test_422_body_matches_error_response(client, repo, make_token):
    response = client.get("/api/v1/targets/not-a-uuid", headers=auth(make_token()))

    assert response.status_code == 422
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "VALIDATION_ERROR"
    assert body.error.details[0].field == "target_id"


def test_unexpected_error_returns_generic_error_response():
    probe = FastAPI()
    register_error_handlers(probe)

    @probe.get("/boom")
    def boom():
        raise RuntimeError("db password=hunter2")

    response = TestClient(probe, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code == "INTERNAL_ERROR"
    assert "hunter2" not in response.text
