def test_registration_and_login_integration_uses_mocked_supabase(client):
    registered = client.post("/api/auth/register", json={"name": "Candidate", "email": "candidate@example.com", "password": "long-password"})
    logged_in = client.post("/api/auth/login", json={"email": "candidate@example.com", "password": "long-password"})
    assert registered.status_code == logged_in.status_code == 200
    assert logged_in.json()["access_token"] == "good-token"


def test_invalid_authentication(client):
    response = client.post("/api/auth/login", json={"email": "candidate@example.com", "password": "wrong-password"})
    assert response.status_code == 401


def test_protected_api_access(client, auth_headers):
    assert client.get("/api/me").status_code == 401
    response = client.get("/api/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["id"] == "11111111-1111-4111-8111-111111111111"


