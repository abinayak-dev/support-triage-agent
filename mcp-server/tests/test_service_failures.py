import os
import unittest
from unittest.mock import patch

import psycopg2

from tools.errors import TriageError
from tools.rag_query import query_knowledge_base
from tools.ticket_router import route_ticket


class ServiceFailureTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_route_requires_database_configuration(self):
        with self.assertRaises(TriageError) as error:
            route_ticket("Subject", "Body", "account_access", "high")

        self.assertEqual(error.exception.status_code, 503)

    @patch.dict(os.environ, {"POSTGRES_URL": "postgresql://test"}, clear=True)
    @patch("tools.ticket_router.logger.exception")
    @patch("tools.ticket_router.psycopg2.connect", side_effect=psycopg2.OperationalError("unavailable"))
    def test_route_does_not_return_success_when_database_is_unavailable(self, connect, _log):
        with self.assertRaises(TriageError) as error:
            route_ticket("Subject", "Body", "account_access", "high")

        self.assertEqual(error.exception.status_code, 503)
        connect.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    @patch("tools.rag_query._embed", return_value=[0.1, 0.2])
    @patch("tools.rag_query._get_client", side_effect=RuntimeError("unavailable"))
    @patch("tools.rag_query.logger.exception")
    def test_query_does_not_return_empty_success_when_qdrant_is_unavailable(self, _log, _client, _embed):
        with self.assertRaises(TriageError) as error:
            query_knowledge_base("Query")

        self.assertEqual(error.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
