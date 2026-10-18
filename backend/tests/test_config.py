from app.config import Settings


def test_comma_separated_frontend_origins_are_parsed():
    settings = Settings(
        _env_file=None,
        frontend_origins="http://localhost:5500,http://127.0.0.1:5500",
    )
    assert settings.cors_origins == [
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ]

