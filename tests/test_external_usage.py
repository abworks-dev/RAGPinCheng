import sqlite3

from src.external_usage import usage_summary
from api.db import init_db


def test_usage_summary_aggregates_providers(tmp_path):
    path = tmp_path / "app.sqlite"
    init_db(path, backup_dir=tmp_path / "backups")
    conn = sqlite3.connect(path)
    conn.execute(
        """INSERT INTO external_service_usage
        (provider, operation, success, prompt_tokens, completion_tokens, total_tokens,
         item_count, input_bytes, latency_ms, created_at)
        VALUES ('zhipu','answer',1,10,5,15,0,0,100,unixepoch())"""
    )
    conn.execute(
        """INSERT INTO external_service_usage
        (provider, operation, success, item_count, input_bytes, latency_ms, created_at)
        VALUES ('mineru','cloud_parse',0,2,4096,200,unixepoch())"""
    )
    conn.commit()
    summary = usage_summary(conn)
    assert summary["today"]["zhipu"]["total_tokens"] == 15
    assert summary["month"]["mineru"]["requests"] == 1
    assert summary["month"]["mineru"]["successes"] == 0
    assert summary["month"]["mineru"]["input_bytes"] == 4096
