"""The agent's own API document: exactly the claim agent's six operations, for the Gateway to make tools of."""
import pytest

from app.api import GATEWAY_OPERATIONS, create_app
from app.config import Settings
from tests.fakes import MINT, TEST_KEY, api_client

KEY = {"X-API-Key": TEST_KEY}
SIX = {"get_claim", "get_policy", "get_claim_history", "calculate_payout", "record_decision", "pay_claim"}


@pytest.fixture
def client(db_path, data_dir):
    settings = Settings(_env_file=None, agent_provider="scripted", db_path=db_path, data_dir=data_dir)
    with api_client(create_app(settings, jwks=MINT)) as c:
        yield c


def document(client):
    answer = client.get("/agent/openapi.json", headers=KEY)
    assert answer.status_code == 200
    return answer.json()


def operations(doc):
    return {op["operationId"]: (path, method, op) for path, ops in doc["paths"].items() for method, op in ops.items()}


def test_the_gateway_refusal_bookkeeping_stays_off_the_gateway(client):
    assert "hold_after_gateway_refusal" not in set(operations(document(client)))


def test_the_document_has_exactly_the_six_operations_and_no_servers(client):
    doc = document(client)
    assert set(operations(doc)) == SIX == set(GATEWAY_OPERATIONS)
    assert "servers" not in doc


def test_the_document_has_no_key_input_and_keeps_amount_and_docstrings(client):
    doc = document(client)
    for name, (path, method, op) in operations(doc).items():
        assert not [p for p in op.get("parameters", []) if p["name"].lower() in ("x-api-key", "authorization")], name
        assert "security" not in op, name
        assert op["description"] and "\n" not in op["description"].strip(), f"{name}: one-line docstring"
    assert "securitySchemes" not in doc.get("components", {})
    _, _, pay = operations(doc)["pay_claim"]
    schema = pay["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
    assert list(doc["components"]["schemas"][schema]["properties"]) == ["amount"]  # the name the Gateway's Policy reads
    paths = {name: path for name, (path, _, _) in operations(doc).items()}
    assert paths["get_claim"] == "/api/claims/{claim_id}" and paths["calculate_payout"] == "/api/claims/{claim_id}/payout"


def test_the_document_needs_the_key_or_a_sign_in(client):
    assert client.get("/agent/openapi.json").status_code == 401
    assert client.get("/agent/openapi.json", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/agent/openapi.json", headers=MINT.header()).status_code == 200
    assert client.get("/agent/openapi.json", headers=KEY).status_code == 200


def inputs_of(doc, op):
    """Every schema an operation takes as input, parameters and body, with the schemas they point at."""
    schemas = doc.get("components", {}).get("schemas", {})
    roots = [p["schema"] for p in op.get("parameters", [])]
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body:
        roots.append(body)

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                yield from walk(schemas[node["$ref"].rsplit("/", 1)[1]])
                return
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    for root in roots:
        yield from walk(root)


def test_no_input_of_the_document_is_nullable_or_optional(client):
    """The Gateway cannot read a type that is "this or null" (anyOf): it takes the field for an object and refuses every value,
    so the claim agent cannot call the tool at all. A limit that does not apply is 0."""
    doc = document(client)
    for name, (_, _, op) in operations(doc).items():
        for node in inputs_of(doc, op):
            assert not {"anyOf", "oneOf", "allOf", "nullable"} & set(node), f"{name}: {sorted({'anyOf', 'oneOf', 'allOf', 'nullable'} & set(node))}"
            assert node.get("type") != "null", name
            if "properties" in node:
                assert set(node.get("required", [])) == set(node["properties"]), f"{name}: an input is optional"


CALCULATE = {"items": [{"description": "x", "amount": 100.0, "item_limit": 0}], "third_party_paid": 0, "deductible": 0, "benefit_max": 0}


def test_the_payout_route_takes_numbers_only(client):
    ok = client.post("/claims/CLM-1001/payout", json=CALCULATE, headers=KEY)
    assert ok.status_code == 200 and ok.json()["payout"] == 100.0
    for broken in (
        {**CALCULATE, "benefit_max": None},
        {**CALCULATE, "items": [{"description": "x", "amount": 100.0, "item_limit": None}]},
        {**CALCULATE, "benefit_max": -5},
        {**CALCULATE, "items": [{"description": "x", "amount": 100.0, "item_limit": -1}]},
        {key: value for key, value in CALCULATE.items() if key != "benefit_max"},
    ):
        assert client.post("/claims/CLM-1001/payout", json=broken, headers=KEY).status_code == 422, broken
