from conftest import USER_ID

CREATE_BODY = {"role": "Product Designer", "seniority": "Mid-level", "focus": "behavioral", "duration_questions": 4}


def create_session(client, auth_headers):
    response = client.post("/api/interviews", headers=auth_headers, json=CREATE_BODY)
    assert response.status_code == 201
    return response.json()


def test_interview_creation(client, auth_headers):
    session = create_session(client, auth_headers)
    assert session["user_id"] == str(USER_ID)
    assert len(session["questions"]) == 4


def test_candidate_data_isolation(client, auth_headers, gateway):
    create_session(client, auth_headers)
    gateway.sessions.append({"id": "foreign", "user_id": "22222222-2222-4222-8222-222222222222", **CREATE_BODY, "status": "in_progress", "current_question_index": 0, "created_at": "2026-01-01T00:00:00Z", "completed_at": None})
    assert all(row["id"] != "foreign" for row in client.get("/api/interviews", headers=auth_headers).json())


def test_answer_persistence(client, auth_headers, gateway):
    session = create_session(client, auth_headers)
    response = client.post(f"/api/interviews/{session['id']}/answers", headers=auth_headers, json={"answer": "I delivered a measurable result after gathering feedback from my team."})
    assert response.status_code == 201
    assert gateway.answers[0]["answer_text"].startswith("I delivered")
    assert gateway.sessions[0]["current_question_index"] == 1


def test_interview_resumption(client, auth_headers):
    session = create_session(client, auth_headers)
    client.post(f"/api/interviews/{session['id']}/answers", headers=auth_headers, json={"answer": "A sufficiently detailed practice answer."})
    resumed = client.get(f"/api/interviews/{session['id']}", headers=auth_headers)
    assert resumed.status_code == 200
    assert resumed.json()["current_question_index"] == 1
    assert len(resumed.json()["answers"]) == 1


def test_report_retrieval(client, auth_headers):
    session = create_session(client, auth_headers)
    for index in range(4):
        response = client.post(f"/api/interviews/{session['id']}/answers", headers=auth_headers, json={"answer": f"I owned outcome {index}, delivered an improved result, and learned from feedback."})
        assert response.status_code == 201
    completed = client.post(f"/api/interviews/{session['id']}/complete", headers=auth_headers)
    report = client.get(f"/api/interviews/{session['id']}/report", headers=auth_headers)
    assert completed.status_code == report.status_code == 200
    assert report.json()["evaluation"]["evaluation_type"] == "heuristic"


