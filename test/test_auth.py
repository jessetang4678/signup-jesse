"""Accounts: registering, logging in and out, and how passwords are stored."""
from conftest import PASSWORD, SITE_NAME, login, register, text


def user_row(db, username):
    return db.execute("SELECT * FROM users WHERE username = %s", (username,)).fetchone()


def test_home_page_shows_site_name(client):
    response = client.get("/")
    assert response.status_code == 200
    assert f"<title>Home - {SITE_NAME}</title>" in text(response)
    assert f"<h1>{SITE_NAME}</h1>" in text(response)
    assert "not logged in" in text(response)


def test_register_creates_account(client, db):
    response = register(client, "alice")
    assert "Account created" in text(response)
    assert user_row(db, "alice") is not None


def test_register_strips_spaces_from_username(client, db):
    register(client, "  alice  ")
    assert user_row(db, "alice") is not None


def test_register_rejects_taken_username(client, db):
    register(client, "alice")
    response = register(client, "alice")
    assert "already taken" in text(response)
    assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1


def test_register_requires_username(client, db):
    response = register(client, "")
    assert "Username is required" in text(response)
    assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0


def test_register_requires_password(client, db):
    response = register(client, "alice", password="")
    assert "Password is required" in text(response)
    assert user_row(db, "alice") is None


def test_password_shorter_than_8_characters_is_rejected(client, db):
    response = register(client, "alice", password="1234567")  # 7 characters
    assert "Account created" not in text(response)
    assert user_row(db, "alice") is None


def test_password_of_exactly_8_characters_is_accepted(client, db):
    response = register(client, "alice", password="12345678")
    assert "Account created" in text(response)
    assert user_row(db, "alice") is not None


def test_password_is_stored_hashed(client, db):
    register(client, "alice")
    stored = user_row(db, "alice")["password_hash"]
    assert stored != PASSWORD
    assert stored.startswith(("scrypt:", "pbkdf2:"))


def test_login_with_correct_password(client):
    register(client, "alice")
    response = login(client, "alice")
    assert "Welcome, alice" in text(response)
    assert "Logged in as <strong>alice</strong>" in text(response)


def test_login_with_wrong_password(client):
    register(client, "alice")
    response = login(client, "alice", password="wrong-password")
    assert "Incorrect username or password" in text(response)


def test_login_with_unknown_user(client):
    response = login(client, "nobody")
    assert "Incorrect username or password" in text(response)


def test_logout(client):
    register(client, "alice")
    login(client, "alice")
    response = client.post("/logout", follow_redirects=True)
    assert "logged out" in text(response)
    assert "not logged in" in text(response)


def test_logout_requires_login(client):
    response = client.post("/logout", follow_redirects=True)
    assert "Please log in first" in text(response)


def test_deleted_user_is_logged_out(client, db):
    register(client, "alice")
    login(client, "alice")
    db.execute("DELETE FROM users WHERE username = 'alice'")
    response = client.get("/")
    assert "not logged in" in text(response)
