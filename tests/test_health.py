"""The process manager's liveness probe."""


def test_health_is_a_public_liveness_probe(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_model_discovery_probe_answers_without_models(client):
    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}
