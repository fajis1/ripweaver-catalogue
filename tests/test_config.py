from ripweaver_catalogue.config import Settings


def test_allowed_hosts_accepts_documented_comma_separated_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CATALOGUE_ALLOWED_HOSTS", "api.ripweaver.com,localhost,127.0.0.1"
    )
    settings = Settings(_env_file=None)
    assert settings.allowed_hosts == [
        "api.ripweaver.com",
        "localhost",
        "127.0.0.1",
    ]
