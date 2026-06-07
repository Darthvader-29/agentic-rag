"""Phase 5: Celery app configuration + eager-mode wiring."""


def test_celery_app_name_and_config():
    from worker.celery_app import celery_app

    assert celery_app.main == "rag"
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_celery_broker_is_redis():
    from config import settings
    from worker.celery_app import celery_app

    # broker + backend both resolve to the Redis URL (default → REDIS_URL)
    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert settings.celery_broker_url.startswith("redis://")


def test_eager_mode_enabled_in_tests():
    from worker.celery_app import celery_app

    # the autouse conftest fixture forces eager mode for the suite
    assert celery_app.conf.task_always_eager is True
