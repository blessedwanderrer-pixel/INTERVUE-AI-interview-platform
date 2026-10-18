from conftest import USER_ID


def test_cv_validation_rejects_disguised_pdf(client, auth_headers):
    response = client.post("/api/cvs", headers=auth_headers, files={"file": ("fake.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_pdf"


def test_private_storage_authorization_and_owner_path(client, auth_headers, gateway):
    assert client.post("/api/cvs", files={"file": ("cv.txt", b"experience", "text/plain")}).status_code == 401
    response = client.post("/api/cvs", headers=auth_headers, files={"file": ("cv.txt", b"experience", "text/plain")})
    assert response.status_code == 201
    assert next(iter(gateway.uploads)).startswith(f"{USER_ID}/")
    assert gateway.cvs[0]["user_id"] == str(USER_ID)


