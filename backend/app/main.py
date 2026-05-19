"""
main.py -- Application Entry Point for the IMAMU Course Scheduler Backend
=========================================================================

This module is the central bootstrap file for the entire FastAPI web application.
It is responsible for:

1. Creating and configuring the FastAPI application instance.
2. Attaching CORS (Cross-Origin Resource Sharing) middleware so that the
   React/Vite frontend (served on a different port during development) can
   communicate with the API without browser security errors.
3. Registering all API route modules (auth, courses, registration, schedule)
   so their endpoints become available under /api/*.
4. Running a startup event that:
   a. Creates all SQLAlchemy ORM tables in the SQLite database if they do
      not yet exist (equivalent to running migrations).
   b. Seeds the database from the university.xml file on first launch
      (when the Day table is empty), populating days, time slots, rooms,
      instructors, departments, courses, sections, and prerequisites.
5. Providing a lightweight /api/health endpoint for monitoring.
6. Serving the compiled React SPA (Single-Page Application) as static files,
   with a catch-all route that returns index.html for client-side routing.

Architecture note:
    FastAPI uses an ASGI server (typically uvicorn). This file is usually
    invoked as:  uvicorn app.main:app --reload
"""

import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Internal imports -- database engine, session factory, and declarative base
from app.database import engine, SessionLocal, Base

# Wildcard import pulls in all SQLAlchemy models so that Base.metadata
# knows about every table when create_all() is called at startup.
from app.models.models import *

# API route modules -- each defines an APIRouter with its own prefix
from app.routes import auth, courses, registration, schedule

# XML data importer -- used to seed the database on first launch
from app.services.xml_import import import_university_xml

# Centralized configuration (database URL, secret key, XML path, etc.)
from app.config import settings

# ---------------------------------------------------------------------------
# Application Instance
# ---------------------------------------------------------------------------
# Create the FastAPI application with a descriptive title and version number.
# These values appear in the auto-generated OpenAPI (Swagger) documentation
# available at /docs when the server is running.
app = FastAPI(title="IMAMU Course Scheduler", version="1.0.0")

# ---------------------------------------------------------------------------
# CORS Middleware Configuration
# ---------------------------------------------------------------------------
# CORS middleware allows the frontend (e.g., http://localhost:3000 during
# development) to make cross-origin HTTP requests to this backend API.
# In production the allow_origins list should be restricted to the actual
# domain(s) hosting the frontend. The wildcard "*" is used here for
# convenience during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Allows requests from any origin
    allow_credentials=True,        # Allows cookies / Authorization headers
    allow_methods=["*"],           # Allows all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],           # Allows all custom headers (e.g., Authorization)
)

# ---------------------------------------------------------------------------
# Route Registration
# ---------------------------------------------------------------------------
# Each router is defined in its own module under app/routes/.
# include_router() merges the router's endpoints into the main app.
# The prefix is set inside each router (e.g., /api/auth, /api/schedule).
app.include_router(auth.router)            # /api/auth/*       -- register, login, me
app.include_router(courses.router)         # /api/courses/*    -- CRUD for courses & sections
app.include_router(registration.router)    # /api/registration/* -- student section registration
app.include_router(schedule.router)        # /api/schedule/*   -- generate, save, list, delete

# ---------------------------------------------------------------------------
# Frontend Static Files Directory
# ---------------------------------------------------------------------------
# Resolve the path to the compiled React frontend build output.
# The structure is:  backend/static/  (contains index.html, static/js, etc.)
# os.path.dirname(__file__) is  backend/app/
# Going up one level yields backend/, then we look for the "static" folder.
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


# ---------------------------------------------------------------------------
# Startup Event -- Database Initialization and XML Import
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup():
    """
    Called once when the FastAPI application starts (before the first request).

    Steps:
    1. Create all database tables defined in the SQLAlchemy models. If the
       tables already exist, create_all() is a no-op (safe to run repeatedly).
    2. Check whether the database has been seeded by querying the Day table.
       If it is empty (first launch), locate the university.xml file and
       import all university data (days, time slots, rooms, instructors,
       departments, courses, sections, and prerequisite relationships).
    """
    # Step 1: Create tables from ORM model metadata
    Base.metadata.create_all(bind=engine)

    # Step 2: Seed the database from XML on first launch
    db = SessionLocal()
    try:
        # Import DayModel locally to avoid any circular-import issues
        from app.models.models import Day as DayModel

        # Only import XML data if the Day table is empty (first-time setup)
        if db.query(DayModel).count() == 0:
            # Try to find university.xml relative to the backend/ directory first
            xml_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), settings.UNIVERSITY_XML)

            # Fallback: look for it in the current working directory
            if not os.path.exists(xml_path):
                xml_path = os.path.join(os.getcwd(), settings.UNIVERSITY_XML)

            if os.path.exists(xml_path):
                print(f"Importing university data from {xml_path}...")
                import_university_xml(db, xml_path)   # Parse XML and insert records
                db.commit()                            # Persist all inserted data
            else:
                print(f"university.xml not found at {xml_path}")
    finally:
        # Always close the session to release the database connection
        db.close()


# ---------------------------------------------------------------------------
# Health Check Endpoint
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """
    Simple health check endpoint for monitoring and load balancers.

    Returns a JSON object with the application status. External services
    (e.g., Docker health checks, uptime monitors) can poll this endpoint
    to verify that the backend is running and responsive.

    Returns:
        dict: {"status": "ok", "app": "IMAMU Course Scheduler"}
    """
    return {"status": "ok", "app": "IMAMU Course Scheduler"}


# ---------------------------------------------------------------------------
# SPA (Single-Page Application) Static File Serving
# ---------------------------------------------------------------------------
# Only mount static files and the catch-all route if the frontend build
# directory exists. This allows the backend to run standalone during
# development even without a frontend build.
if os.path.exists(frontend_dir):
    # Mount the React build's /static/ sub-directory (JS, CSS, images)
    # at the /static URL path so that asset references in index.html resolve.
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_dir, "static")), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """
        Catch-all route that serves the React SPA.

        How it works:
        - If the requested path corresponds to an actual file on disk
          (e.g., favicon.ico, manifest.json), serve that file directly.
        - Otherwise, serve index.html and let the React Router handle
          client-side routing (e.g., /dashboard, /courses, /schedule).

        This pattern is standard for serving SPAs from the same server
        as the API -- all /api/* routes are matched first (registered
        above), and everything else falls through to this handler.

        Args:
            request: The incoming HTTP request (unused but required by FastAPI).
            full_path: The URL path captured by the {full_path:path} parameter.

        Returns:
            FileResponse: Either the requested static file or index.html.
        """
        # Build the absolute filesystem path for the requested URL
        file_path = os.path.join(frontend_dir, full_path)

        # If the path points to a real file, serve it directly
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)

        # Otherwise, serve index.html for client-side routing
        return FileResponse(os.path.join(frontend_dir, "index.html"))
