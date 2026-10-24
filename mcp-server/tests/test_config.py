import unittest

from tools.config import validate_configuration


class ConfigurationTests(unittest.TestCase):
    def test_rejects_placeholder_api_key(self):
        with self.assertRaisesRegex(ValueError, "API_KEY"):
            validate_configuration({"API_KEY": "replace-with-a-long-random-value"})

    def test_rejects_invalid_rate_limit(self):
        with self.assertRaisesRegex(ValueError, "RATE_LIMIT_PER_MIN"):
            validate_configuration({"API_KEY": "local-test-key", "RATE_LIMIT_PER_MIN": "0"})

    def test_rejects_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "PORT"):
            validate_configuration({"API_KEY": "local-test-key", "PORT": "70000"})

    def test_requires_hosted_providers_for_production(self):
        with self.assertRaisesRegex(ValueError, "Production requires"):
            validate_configuration({"APP_ENV": "production", "API_KEY": "x" * 40})

    def test_rejects_production_database_without_ssl(self):
        env = {
            "APP_ENV": "production",
            "API_KEY": "x" * 40,
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "chat-model",
            "EMBEDDING_PROVIDER": "openai_compatible",
            "EMBEDDING_BASE_URL": "https://embeddings.example/v1",
            "EMBEDDING_API_KEY": "test-key",
            "EMBED_MODEL": "embedding-model",
            "POSTGRES_URL": "postgresql://user:password@db.example/tickets",
            "QDRANT_HOST": "https://qdrant.example",
            "QDRANT_API_KEY": "test-key",
        }
        with self.assertRaisesRegex(ValueError, "sslmode"):
            validate_configuration(env)

    def test_rejects_provider_urls_with_embedded_credentials(self):
        env = {
            "API_KEY": "local-test-key",
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://user:password@llm.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "chat-model",
        }
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            validate_configuration(env)

    def test_accepts_production_configuration_with_tls(self):
        env = {
            "APP_ENV": "production",
            "API_KEY": "x" * 40,
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "chat-model",
            "EMBEDDING_PROVIDER": "openai_compatible",
            "EMBEDDING_BASE_URL": "https://embeddings.example/v1",
            "EMBEDDING_API_KEY": "test-key",
            "EMBED_MODEL": "embedding-model",
            "POSTGRES_URL": "postgresql://user:password@db.example/tickets?sslmode=verify-full",
            "QDRANT_HOST": "https://qdrant.example",
            "QDRANT_API_KEY": "test-key",
        }
        validate_configuration(env)

    def test_rejects_production_provider_urls_without_tls(self):
        env = {
            "APP_ENV": "production",
            "API_KEY": "x" * 40,
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "chat-model",
            "EMBEDDING_PROVIDER": "openai_compatible",
            "EMBEDDING_BASE_URL": "https://embeddings.example/v1",
            "EMBEDDING_API_KEY": "test-key",
            "EMBED_MODEL": "embedding-model",
            "POSTGRES_URL": "postgresql://user:password@db.example/tickets?sslmode=require",
            "QDRANT_HOST": "https://qdrant.example",
            "QDRANT_API_KEY": "test-key",
        }
        for name in ("LLM_BASE_URL", "EMBEDDING_BASE_URL"):
            with self.subTest(name=name):
                insecure_env = {**env, name: env[name].replace("https://", "http://")}
                with self.assertRaisesRegex(ValueError, f"{name}.*https"):
                    validate_configuration(insecure_env)

    def test_rejects_placeholder_production_service_urls(self):
        env = {
            "APP_ENV": "production",
            "API_KEY": "x" * 40,
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "chat-model",
            "EMBEDDING_PROVIDER": "openai_compatible",
            "EMBEDDING_BASE_URL": "https://embeddings.example/v1",
            "EMBEDDING_API_KEY": "test-key",
            "EMBED_MODEL": "embedding-model",
            "POSTGRES_URL": "postgresql://user:password@db.example/tickets?sslmode=require",
            "QDRANT_HOST": "https://qdrant.example",
            "QDRANT_API_KEY": "test-key",
        }
        placeholders = {
            "POSTGRES_URL": "postgresql://<username>:<password>@<postgres-host>:5432/<database>?sslmode=require",
            "QDRANT_HOST": "https://<cluster-id>.<region>.<provider-domain>",
            "LLM_BASE_URL": "https://<chat-provider-host>/v1",
            "EMBEDDING_BASE_URL": "https://<embedding-provider-host>/v1",
        }
        for name, value in placeholders.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, name):
                    validate_configuration({**env, name: value})


if __name__ == "__main__":
    unittest.main()
