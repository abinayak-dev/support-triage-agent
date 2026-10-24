"""Startup validation for deployment-sensitive environment configuration."""
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured")
    if value.lower().startswith("replace-with-"):
        raise ValueError(f"{name} must be replaced with a real value")
    return value


def _validate_url(
    name: str,
    value: str,
    schemes: set[str],
    allow_query: bool = False,
    allow_credentials: bool = False,
) -> str:
    try:
        parsed = urlsplit(value)
        if (
            any(marker in value.lower() for marker in ("replace-with-", "<", ">"))
            or parsed.scheme not in schemes
            or not parsed.hostname
            or ((parsed.username is not None or parsed.password is not None) and not allow_credentials)
            or (parsed.query and not allow_query)
            or parsed.fragment
        ):
            raise ValueError
        parsed.port
    except ValueError:
        credential_rule = "" if allow_credentials else " without embedded credentials"
        raise ValueError(
            f"{name} must be a valid URL using {', '.join(sorted(schemes))}{credential_rule}"
        ) from None
    return parsed.hostname.lower()


def validate_configuration(env: Mapping[str, str]) -> None:
    """Fail at startup for malformed settings and unsafe production defaults."""
    app_env = env.get("APP_ENV", "development").strip().lower()
    if app_env not in {"development", "production"}:
        raise ValueError("APP_ENV must be 'development' or 'production'")

    api_key = _required(env, "API_KEY")
    if api_key == "change-this" or api_key.startswith("replace-with-"):
        raise ValueError("API_KEY must be replaced with a unique secret")

    try:
        rate_limit = int(env.get("RATE_LIMIT_PER_MIN", "60"))
    except ValueError:
        raise ValueError("RATE_LIMIT_PER_MIN must be a positive integer") from None
    if rate_limit < 1:
        raise ValueError("RATE_LIMIT_PER_MIN must be a positive integer")
    try:
        port = int(env.get("PORT", "8000"))
    except ValueError:
        raise ValueError("PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535")

    llm_provider = env.get("LLM_PROVIDER", "ollama").strip().lower()
    embedding_provider = env.get("EMBEDDING_PROVIDER", "ollama").strip().lower()
    if llm_provider not in {"ollama", "openai_compatible"}:
        raise ValueError("LLM_PROVIDER must be 'ollama' or 'openai_compatible'")
    if embedding_provider not in {"ollama", "openai_compatible"}:
        raise ValueError("EMBEDDING_PROVIDER must be 'ollama' or 'openai_compatible'")

    if llm_provider == "openai_compatible":
        _required(env, "LLM_API_KEY")
        _required(env, "LLM_MODEL")
        _validate_url("LLM_BASE_URL", _required(env, "LLM_BASE_URL"), {"http", "https"})
    if embedding_provider == "openai_compatible":
        _required(env, "EMBEDDING_API_KEY")
        _required(env, "EMBED_MODEL")
        _validate_url(
            "EMBEDDING_BASE_URL",
            _required(env, "EMBEDDING_BASE_URL"),
            {"http", "https"},
        )

    if app_env != "production":
        return

    if len(api_key) < 32:
        raise ValueError("Production API_KEY must be at least 32 characters")
    if llm_provider != "openai_compatible" or embedding_provider != "openai_compatible":
        raise ValueError("Production requires OpenAI-compatible chat and embedding providers")
    if "*" in (origin.strip() for origin in env.get("CORS_ORIGINS", "").split(",")):
        raise ValueError("Production CORS_ORIGINS must not contain '*'")
    for origin in (value.strip() for value in env.get("CORS_ORIGINS", "").split(",") if value.strip()):
        _validate_url("CORS_ORIGINS", origin, {"https"})

    postgres_url = _required(env, "POSTGRES_URL")
    postgres_host = _validate_url(
        "POSTGRES_URL",
        postgres_url,
        {"postgres", "postgresql"},
        allow_query=True,
        allow_credentials=True,
    )
    if postgres_host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Production POSTGRES_URL must use a reachable managed database")
    postgres_parts = urlsplit(postgres_url)
    if any(
        part and part.lower().startswith("replace-with-")
        for part in (postgres_parts.username, postgres_parts.password)
    ):
        raise ValueError("Production POSTGRES_URL credentials must be real values")
    sslmode = parse_qs(postgres_parts.query).get("sslmode", [""])[0].lower()
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("Production POSTGRES_URL must set sslmode=require or stronger")

    qdrant_url = _required(env, "QDRANT_HOST")
    qdrant_host = _validate_url("QDRANT_HOST", qdrant_url, {"https"})
    if qdrant_host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Production QDRANT_HOST must use a reachable managed service")
    _required(env, "QDRANT_API_KEY")

    for name in ("LLM_BASE_URL", "EMBEDDING_BASE_URL"):
        _validate_url(name, _required(env, name), {"https"})
