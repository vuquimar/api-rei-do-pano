import pytest
from fastapi.testclient import TestClient
import os

# Adiciona o diretório raiz ao path para que `main` possa ser importado
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app

client = TestClient(app)

# Chave de API para os testes, pode ser qualquer valor, desde que seja consistente
TEST_API_KEY = "test-key-123"
os.environ["SERVER_API_KEY"] = TEST_API_KEY


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_tool_call_missing_api_key():
    response = client.post("/tool_call", json={
        "tool_name": "search_products",
        "params": {"query": "toalha"}
    })
    assert response.status_code == 403
    assert response.json() == {"detail": "Not authenticated"}


def test_tool_call_invalid_api_key():
    response = client.post(
        "/tool_call",
        json={"tool_name": "search_products", "params": {"query": "toalha"}},
        headers={"X-API-Key": "invalid-key"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Could not validate credentials"}


def test_tool_call_success_with_valid_key():
    response = client.post(
        "/tool_call",
        json={"tool_name": "search_products", "params": {"query": "toalha", "page": 1}},
        headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert "items" in data["tools"][0]
    assert "page" in data["tools"][0]


def test_search_returns_empty_for_no_results():
    response = client.post(
        "/tool_call",
        json={"tool_name": "search_products", "params": {"query": "xyz_a_b_c_d_e_f_g", "page": 1}},
        headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["tools"][0]["items"] == []
    assert data["tools"][0]["has_more"] is False
