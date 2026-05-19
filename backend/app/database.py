"""
database.py -- SQLAlchemy Database Engine, Session Factory, and Base Class
==========================================================================

This module sets up the core SQLAlchemy infrastructure used throughout the
IMAMU Course Scheduler backend:

1. **Engine** -- The low-level connection pool that manages connections to
   the SQLite database (or any other RDBMS configured in settings).

2. **SessionLocal** -- A session factory (created by sessionmaker) that
   produces new database session objects. Each session represents a
   "unit of work" -- a conversation with the database that can be
   committed or rolled back.

3. **Base** -- The declarative base class that all ORM models inherit from.
   SQLAlchemy inspects subclasses of Base to discover table definitions
   and generate DDL (CREATE TABLE) statements.

4. **get_db()** -- A FastAPI dependency generator that yields a session
   and ensures it is closed after the request finishes, even if an
   exception occurs.

Architecture note:
    SQLAlchemy's "session-per-request" pattern is implemented via get_db().
    FastAPI's dependency injection system calls get_db() for each incoming
    request, giving the route handler a fresh session. The 'finally' block
    guarantees cleanup regardless of success or failure.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# ---------------------------------------------------------------------------
# SQLite-Specific Connection Arguments
# ---------------------------------------------------------------------------
# SQLite enforces single-threaded access by default. When running under
# FastAPI (which uses multiple threads for synchronous endpoints), we must
# pass check_same_thread=False to allow the connection to be shared across
# threads. This is safe because SQLAlchemy's session management ensures
# that each request gets its own session object.
# For other databases (PostgreSQL, MySQL), no extra connect_args are needed.
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# ---------------------------------------------------------------------------
# Engine Creation
# ---------------------------------------------------------------------------
# The engine is the starting point for all SQLAlchemy operations. It manages
# a pool of database connections and translates Python calls into SQL.
# connect_args are passed directly to the underlying DBAPI driver.
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)

# ---------------------------------------------------------------------------
# Session Factory
# ---------------------------------------------------------------------------
# sessionmaker returns a class (SessionLocal) that, when instantiated,
# produces a new Session bound to the engine above.
#   autocommit=False  -- Transactions must be explicitly committed.
#   autoflush=False   -- Pending changes are not automatically flushed
#                        before every query; this gives us more control.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------
# All ORM model classes (User, Course, Section, etc.) inherit from Base.
# Base.metadata holds the collection of Table objects, which is used by
# create_all() in main.py to generate the database schema.
Base = declarative_base()


def get_db():
    """
    FastAPI dependency that provides a database session for each request.

    This is a Python generator function. FastAPI's Depends() mechanism:
    1. Calls next() to advance to the yield, creating a new session.
    2. Injects the session into the route handler's 'db' parameter.
    3. After the route handler returns (or raises), execution resumes
       in the 'finally' block, which closes the session.

    Yields:
        Session: A SQLAlchemy database session.

    Example usage in a route:
        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()   # Create a new session from the factory
    try:
        yield db          # Hand the session to the route handler
    finally:
        db.close()        # Always close the session to release the connection
