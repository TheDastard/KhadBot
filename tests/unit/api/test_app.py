"""
tests/unit/api/test_app.py

The app as a whole: the committed OpenAPI contract matches the API (regenerate with
``uv run python scripts/dev/gen_contracts.py``),
``Evidence`` is a union over the ref grammar's resolvable kinds, every error response is documented as ``ErrorBody``, and
``create_app`` takes extra routers.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import orjson
from fastapi import APIRouter
from fastapi.testclient import TestClient

from khadbot.api import create_app, openapi_document
from khadbot.api.app import hosted_documentation_app
from khadbot.encounter import evidence
from khadbot.encounter.analyst.verify import check_finding
from khadbot.encounter.models import Finding

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"


def test_committed_contract_matches_the_api():
    assert CONTRACT.read_text(encoding="utf-8") == openapi_document(), (
        "contracts/openapi.json is out of date; run `uv run python scripts/dev/gen_contracts.py`"
    )


def test_contract_is_independent_of_the_data_directory(tmp_path):
    assert openapi_document(hosted_documentation_app(tmp_path)) == openapi_document()


def test_evidence_is_a_union_discriminated_on_every_resolvable_ref_kind():
    # One member per kind of the ref grammar that resolves; plan refs answer 501 and have none.
    resolvable = [k for k in get_args(evidence.RefKind) if k != "plan"]
    assert list(evidence.RECORD_MODELS) == resolvable
    schemas = orjson.loads(CONTRACT.read_bytes())["components"]["schemas"]
    discriminator = schemas["Evidence"]["discriminator"]
    assert discriminator["propertyName"] == "kind"
    assert sorted(discriminator["mapping"]) == sorted(resolvable)
    assert sorted(m["$ref"] for m in schemas["Evidence"]["oneOf"]) == sorted(discriminator["mapping"].values())
    for kind, target in discriminator["mapping"].items():
        member = schemas[target.rsplit("/", 1)[1]]
        assert member["properties"]["kind"]["const"] == kind
        assert set(member["required"]) == {"kind", "ref", "record"}
        record = member["properties"]["record"]["$ref"].rsplit("/", 1)[1]
        assert record == evidence.RECORD_MODELS[kind].__name__


def test_every_error_response_uses_the_error_body():
    doc = orjson.loads(CONTRACT.read_bytes())
    assert "HTTPValidationError" not in doc["components"]["schemas"]
    for path, ops in doc["paths"].items():
        for op in ops.values():
            for status, response in op["responses"].items():
                if status.startswith(("4", "5")):
                    schema = response["content"]["application/json"]["schema"]
                    # A 429 also says how long to wait: BudgetWaitBody is an ErrorBody with a wait_s.
                    allowed = "BudgetWaitBody" if status == "429" else "ErrorBody"
                    assert schema == {"$ref": f"#/components/schemas/{allowed}"}, (path, status)


def test_every_path_is_versioned_and_local_mode_is_read_only():
    doc = orjson.loads(CONTRACT.read_bytes())
    assert all(path.startswith("/v1/") for path in doc["paths"])
    # The contract is the hosted API's. Local mode (no sign-in, no jobs) stays read-only.
    local = create_app(mode="local", data_dir=Path(".")).openapi()
    assert {method for ops in local["paths"].values() for method in ops} == {"get"}


def test_extra_routers_are_mounted(tmp_path):
    extra = APIRouter(prefix="/v1")

    @extra.get("/extra")
    def _extra() -> dict:
        return {"ok": True}

    client = TestClient(create_app(mode="local", data_dir=tmp_path, routers=[extra]))
    assert client.get("/v1/extra").json() == {"ok": True}
    assert client.get("/v1/health").status_code == 200


def test_verify_check_rejects_a_wrong_briefing_number(synth_dataset):
    """Guards the briefing test: the check it relies on does fail an unsupported number."""
    ds = synth_dataset
    (section,) = ds.client.get(f"/v1/reports/{ds.code}/night").json()["sections"]
    line = section["briefing"][0]
    claim = line["text"].replace(f"of {section['pulls']}", "of 977")
    assert claim != line["text"]
    finding = Finding(id="brief-1", claim=claim, refs=line["refs"], confidence="high", scope="cross_pull")
    assert check_finding(finding, ds.ctx.data) == "unsupported number 977"
