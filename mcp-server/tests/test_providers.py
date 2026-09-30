import os
import unittest
from unittest.mock import Mock, patch

from tools.providers import chat_completion, embed_texts


class ProviderTests(unittest.TestCase):
    @patch.dict(os.environ, {"LLM_PROVIDER": "ollama", "CLASSIFY_MODEL": "tinyllama"}, clear=True)
    @patch("tools.providers.httpx.post")
    def test_local_ollama_chat_keeps_existing_api_contract(self, post):
        response = Mock()
        response.json.return_value = {"message": {"content": '{"category":"general_inquiry"}'}}
        post.return_value = response

        result = chat_completion([{"role": "user", "content": "Test"}])

        self.assertEqual(result, '{"category":"general_inquiry"}')
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://localhost:11434/api/chat")
        self.assertEqual(kwargs["json"]["model"], "tinyllama")
        self.assertEqual(kwargs["json"]["format"], "json")

    @patch.dict(
        os.environ,
        {
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "demo-model",
        },
        clear=True,
    )
    @patch("tools.providers.httpx.post")
    def test_openai_compatible_chat_uses_configured_server_credentials(self, post):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content": '{"category":"billing"}'}}]}
        post.return_value = response

        result = chat_completion([{"role": "user", "content": "Test"}])

        self.assertEqual(result, '{"category":"billing"}')
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://llm.example/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(kwargs["json"]["model"], "demo-model")

    @patch.dict(
        os.environ,
        {
            "EMBEDDING_PROVIDER": "openai_compatible",
            "EMBEDDING_BASE_URL": "https://embeddings.example/v1",
            "EMBEDDING_API_KEY": "test-key",
            "EMBED_MODEL": "demo-embedding",
        },
        clear=True,
    )
    @patch("tools.providers.httpx.post")
    def test_openai_compatible_embeddings_preserve_input_order(self, post):
        response = Mock()
        response.json.return_value = {
            "data": [
                {"index": 1, "embedding": [3, 4]},
                {"index": 0, "embedding": [1, 2]},
            ]
        }
        post.return_value = response

        vectors = embed_texts(["first", "second"])

        self.assertEqual(vectors, [[1.0, 2.0], [3.0, 4.0]])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://embeddings.example/v1/embeddings")
        self.assertEqual(kwargs["json"], {"model": "demo-embedding", "input": ["first", "second"]})

    @patch.dict(os.environ, {"EMBEDDING_PROVIDER": "ollama"}, clear=True)
    @patch("tools.providers.httpx.post")
    def test_local_ollama_embeddings_keep_existing_model_default(self, post):
        response = Mock()
        response.json.return_value = {"embeddings": [[0.1, 0.2]]}
        post.return_value = response

        self.assertEqual(embed_texts(["text"]), [[0.1, 0.2]])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://localhost:11434/api/embed")
        self.assertEqual(kwargs["json"]["model"], "nomic-embed-text")

    @patch.dict(os.environ, {"LLM_PROVIDER": "openai_compatible"}, clear=True)
    def test_hosted_chat_requires_explicit_endpoint_configuration(self):
        with self.assertRaisesRegex(ValueError, "LLM_BASE_URL"):
            chat_completion([{"role": "user", "content": "Test"}])


if __name__ == "__main__":
    unittest.main()
