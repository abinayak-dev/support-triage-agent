"""Persist a classified ticket and determine its routing and alert decision."""
import logging
import os

import psycopg2
import psycopg2.extras

from .errors import TriageError

logger = logging.getLogger("triage.ticket_router")

CATEGORY_TEAM_MAP = {
    "account_access": "identity-support",
    "billing": "billing-support",
    "technical_issue": "technical-support",
    "bug_report": "engineering",
    "feature_request": "product",
    "general_inquiry": "general-support",
}

ALERT_PRIORITIES = {"high", "urgent"}


def _get_connection():
    postgres_url = os.getenv("POSTGRES_URL")
    if not postgres_url:
        raise TriageError("Ticket storage is not configured", 503)
    return psycopg2.connect(postgres_url)


def route_ticket(
    subject: str,
    body: str,
    category: str = "general_inquiry",
    priority: str = "normal",
) -> dict:
    """Persist a ticket and return its team and alert decision."""
    team = CATEGORY_TEAM_MAP.get(category, "general-support")
    should_alert = priority in ALERT_PRIORITIES

    try:
        conn = _get_connection()
    except TriageError:
        raise
    except psycopg2.Error:
        logger.exception("PostgreSQL connection failed")
        raise TriageError("Ticket storage service unavailable", 503)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                INSERT INTO tickets (subject, body, category, priority, team)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (subject, body, category, priority, team),
            )
            ticket_id = cur.fetchone()[0]
        conn.commit()
    except psycopg2.Error:
        conn.rollback()
        logger.exception("PostgreSQL ticket insert failed")
        raise TriageError("Ticket could not be stored", 503)
    finally:
        conn.close()

    return {
        "ticket_id": ticket_id,
        "team": team,
        "priority": priority,
        "should_alert": should_alert,
    }


if __name__ == "__main__":
    import json

    result = route_ticket(
        subject="Cannot reset password",
        body="I tried the reset link 3 times, it expires immediately.",
        category="account_access",
        priority="high",
    )
    print(json.dumps(result, indent=2))
