import json

import structlog

from logging_config import configure_logging


def test_json_renders_json(capsys):
    configure_logging(json_logs=True)
    structlog.get_logger("t").info("hello", route="RAG", count=3)
    p = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (
        p["event"] == "hello" and p["route"] == "RAG" and p["level"] == "info" and "timestamp" in p
    )


def test_console_not_json(capsys):
    configure_logging(json_logs=False)
    structlog.get_logger("t").info("hello-console")
    assert "hello-console" in capsys.readouterr().out
