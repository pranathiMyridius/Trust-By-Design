import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

# This module reads DATABASE_URL at import time and is imported first
# (app/main.py imports it before any module that calls load_dotenv()),
# so it must load backend/.env itself or DATABASE_URL would silently be
# missed and this would fall back to SQLite.
load_dotenv()

# Defaults to the existing local SQLite file so nothing about the current
# setup changes unless DATABASE_URL is explicitly set (e.g. to point at
# the docker-compose Postgres+pgvector instance -- see
# docker-compose.yml and app/models/document_embedding.py). Semantic
# search over uploaded documents (pgvector) only works when this is a
# postgresql:// URL; the app runs fine on SQLite without it, just
# without that feature.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./risk.db")

IS_POSTGRES = DATABASE_URL.startswith("postgresql")

# P7 (R19): a remote database is only ever reached over TLS. Refused here,
# before any engine exists, unless deliberately overridden for a non-
# production setup (DATABASE_ALLOW_INSECURE_TRANSPORT=true; never honoured
# with APP_ENV=production -- see app/security_settings.py).
from app.security_settings import database_tls_problem, insecure_transport_allowed, is_production  # noqa: E402

_tls_problem = database_tls_problem(DATABASE_URL)
if _tls_problem and (is_production() or not insecure_transport_allowed()):
    raise RuntimeError(_tls_problem)

connect_args = {"check_same_thread": False} if not IS_POSTGRES else {}

# Hosted Postgres (e.g. Neon) drops idle SSL connections server-side, so a
# pooled connection can be dead by the time it is reused ("SSL connection has
# been closed unexpectedly"). pre_ping tests a connection on checkout and
# transparently replaces a dead one; recycle retires connections before the
# server's idle timeout. Both are no-ops for SQLite.
_pool_args = (
    {"pool_pre_ping": True, "pool_recycle": int(os.getenv("DATABASE_POOL_RECYCLE_SECONDS", "240"))}
    if IS_POSTGRES
    else {}
)

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    **_pool_args,
)

if not IS_POSTGRES:
    # Stage 19 (Availability -- protection against corruption): write-ahead
    # logging keeps the SQLite file consistent if the process dies mid-write
    # and lets readers work during background-job writes; busy_timeout makes
    # concurrent writers (request + job worker) wait instead of failing.
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

# Importing this module never runs anything against the database. The
# pgvector extension is created by the baseline migration
# (alembic/versions/0001_baseline.py), which runs only through the guarded
# path in app/migrations.py.

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
