"""Fixtures for tests that need a real, live Postgres connection.

The `live_engine` fixture these tests use now lives in `tests/conftest.py`,
unchanged, so it is available to every suite rather than only this one --
`tests/api/test_documents.py` needs it to assert the audit-durability ruling
on the engine production actually runs on (sqlite takes a database-level write
lock, which no Postgres deployment does). See that fixture's docstring in
`tests/conftest.py` for why it targets a database SEPARATE from
`Settings.database_url` (Task 5 review, H2).
"""
