from datetime import datetime
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.db import engine
from app.amsat import parse_status


def test_migration_roundtrip():
    config=Config('alembic.ini')
    assert 'notifications' in inspect(engine).get_table_names()
    command.downgrade(config,'base')
    assert 'notifications' not in inspect(engine).get_table_names()
    command.upgrade(config,'head')
    command.check(config)


def test_amsat_read_only_mapping_and_staleness():
    html="""<script>tips.a123 = new Array(5,5,120,'Heard<br>2026-11-21 01:00 UTC');
</script><table><tr><td><a>ISS_[FM]</a></td><td bgcolor="#648fff"><a onmouseover="docTips.show('a123')">1</a></td></tr></table>"""
    assert parse_status(html,'ISS_[FM]',datetime(2026,11,21,2)).startswith('Sat/Mode Active')
    assert '未知' in parse_status(html,'ISS_[FM]',datetime(2026,11,23))
    assert '未知' in parse_status(html,'FO-29',datetime(2026,11,21))
