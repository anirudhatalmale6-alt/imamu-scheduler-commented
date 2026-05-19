"""
courses.py -- Course and Section CRUD API Endpoints + Reference Data Lookups
=============================================================================

This module defines the REST API endpoints for managing courses and sections,
as well as read-only endpoints for reference data (departments, instructors,
rooms, days, and time slots). All endpoints are prefixed with /api/.

Endpoints -- Reference Data (public, no authentication required):
    GET /api/departments  -- List all academic departments.
    GET /api/instructors  -- List all faculty members.
    GET /api/rooms        -- List all classrooms and labs.
    GET /api/days         -- List all working days (ordered by ID).
    GET /api/timeslots    -- List all daily time periods (ordered chronologically).

Endpoints -- Course CRUD (instructor/admin authentication required for writes):
    GET    /api/courses           -- List courses with optional filters.
    GET    /api/courses/{id}      -- Get a single course with full details.
    POST   /api/courses           -- Create a new course (instructor/admin only).
    PUT    /api/courses/{id}      -- Update an existing course (instructor/admin only).
    DELETE /api/courses/{id}      -- Delete a course (instructor/admin only).

Endpoints -- Section Management:
    POST /api/sections            -- Create a new section (instructor/admin only).
    GET  /api/sections            -- List sections with optional filters.

Architecture note:
    The _course_to_response() helper function handles the transformation from
    SQLAlchemy ORM objects to the CourseResponse Pydantic schema, including
    joining related data (department name, prerequisite codes, section details).
    This keeps the route handlers clean and DRY.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List
from app.database import get_db
from app.models.models import User, UserRole, Course, Section, Department
from app.schemas import (
    CourseCreate, CourseUpdate, CourseResponse, SectionCreate, SectionResponse,
    DepartmentResponse, InstructorResponse, RoomResponse, DayResponse, TimeSlotResponse,
)
from app.services.auth import get_current_user
from app.models.models import Instructor, Room, Day, TimeSlot

# ---------------------------------------------------------------------------
# Router Configuration
# ---------------------------------------------------------------------------
# The /api prefix is shared by all endpoints in this module.
# The "courses" tag groups these endpoints in the Swagger UI.
router = APIRouter(prefix="/api", tags=["courses"])


# ---------------------------------------------------------------------------
# Helper: ORM-to-Response Conversion
# ---------------------------------------------------------------------------
def _course_to_response(course: Course) -> CourseResponse:
    """
    Convert a Course SQLAlchemy ORM object into a CourseResponse Pydantic model.

    This helper performs the "serialization" step that transforms database
    relationships (department, prerequisites, sections) into plain data
    structures suitable for JSON serialization.

    Args:
        course: A Course ORM object with its relationships loaded
                (department, prerequisites, sections).

    Returns:
        CourseResponse: A Pydantic model with all fields populated,
                        including joined department_name, prerequisite
                        course codes, and section summary dicts.
    """
    return CourseResponse(
        id=course.id,
        code=course.code,
        name=course.name,
        level=course.level,
        credits=course.credits,
        is_lab=course.is_lab,
        is_archived=course.is_archived,
        department_id=course.department_id,
        # Resolve the department name from the relationship (avoids N+1 queries
        # when joinedload is used in the calling query)
        department_name=course.department.name if course.department else "",
        # Extract just the course codes from the prerequisites relationship
        prerequisites=[p.code for p in course.prerequisites],
        # Build a list of section summary dicts for embedding in the response
        sections=[
            {
                "id": s.id,
                "section_id": s.section_id,
                "gender": s.gender,
                "capacity": s.capacity,
                "enrolled": s.enrolled,
                "is_archived": s.is_archived,
            }
            for s in course.sections
        ],
    )


# ===========================================================================
# Reference Data Endpoints (Read-Only, No Authentication Required)
# ===========================================================================
# These endpoints serve lookup data used by the frontend for dropdowns,
# filter controls, and informational displays.

@router.get("/departments", response_model=List[DepartmentResponse])
def list_departments(db: Session = Depends(get_db)):
    """
    List all academic departments.

    Returns:
        List[DepartmentResponse]: All departments with id, name, and code.
    """
    return db.query(Department).all()


@router.get("/instructors", response_model=List[InstructorResponse])
def list_instructors(db: Session = Depends(get_db)):
    """
    List all instructors/faculty members.

    Returns:
        List[InstructorResponse]: All instructors with their details
                                  (name, gender, department, rank, hours).
    """
    return db.query(Instructor).all()


@router.get("/rooms", response_model=List[RoomResponse])
def list_rooms(db: Session = Depends(get_db)):
    """
    List all classrooms and laboratories.

    Returns:
        List[RoomResponse]: All rooms with their details
                            (capacity, gender, type, building, floor).
    """
    return db.query(Room).all()


@router.get("/days", response_model=List[DayResponse])
def list_days(db: Session = Depends(get_db)):
    """
    List all working days, ordered by ID (chronological order).

    Returns:
        List[DayResponse]: All days with code and name (e.g., SUN/Sunday).
    """
    return db.query(Day).order_by(Day.id).all()


@router.get("/timeslots", response_model=List[TimeSlotResponse])
def list_timeslots(db: Session = Depends(get_db)):
    """
    List all daily time slots, ordered chronologically by slot_order.

    Returns:
        List[TimeSlotResponse]: All time slots with label, start/end times,
                                type (Class/Break), and ordering.
    """
    return db.query(TimeSlot).order_by(TimeSlot.slot_order).all()


# ===========================================================================
# Course CRUD Endpoints
# ===========================================================================

@router.get("/courses", response_model=List[CourseResponse])
def list_courses(
    department_id: int = None,
    level: int = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    """
    List courses with optional filtering.

    Uses SQLAlchemy's joinedload strategy to eagerly load the department,
    sections, and prerequisites relationships in a single query, avoiding
    the N+1 query problem when serializing multiple courses.

    Query parameters:
        department_id:    Filter by department (optional).
        level:            Filter by academic level (optional).
        include_archived: If True, include soft-deleted courses (default False).

    Args:
        db: Database session injected by FastAPI.

    Returns:
        List[CourseResponse]: Matching courses with full details.
    """
    # Build the base query with eager loading of related objects
    q = db.query(Course).options(
        joinedload(Course.department),     # Eager load department for .name access
        joinedload(Course.sections),       # Eager load sections for the sections list
        joinedload(Course.prerequisites),  # Eager load prerequisites for code extraction
    )

    # Apply optional filters
    if not include_archived:
        q = q.filter(Course.is_archived == False)  # Exclude archived courses by default
    if department_id:
        q = q.filter(Course.department_id == department_id)
    if level:
        q = q.filter(Course.level == level)

    # Execute the query and convert each ORM object to the response schema
    courses = q.all()
    return [_course_to_response(c) for c in courses]


@router.get("/courses/{course_id}", response_model=CourseResponse)
def get_course(course_id: int, db: Session = Depends(get_db)):
    """
    Get a single course by its database ID, with full details.

    Args:
        course_id: The primary key of the course to retrieve.
        db:        Database session injected by FastAPI.

    Returns:
        CourseResponse: The course with department, prerequisites, and sections.

    Raises:
        HTTPException 404: If no course exists with the given ID.
    """
    # Query with eager loading for a single course
    course = db.query(Course).options(
        joinedload(Course.department),
        joinedload(Course.sections),
        joinedload(Course.prerequisites),
    ).filter(Course.id == course_id).first()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    return _course_to_response(course)


@router.post("/courses", response_model=CourseResponse)
def create_course(
    data: CourseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new course (instructor/admin only).

    Validates the user's role, checks for duplicate course codes, creates
    the course, and optionally links prerequisite courses.

    Args:
        data:         Validated CourseCreate schema from the request body.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (must be instructor or admin).

    Returns:
        CourseResponse: The newly created course with full details.

    Raises:
        HTTPException 403: If the user is not an instructor or admin.
        HTTPException 400: If the course code already exists.
    """
    # Authorization check: only instructors and admins can create courses
    if current_user.role not in (UserRole.INSTRUCTOR, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only instructors can create courses")

    # Check for duplicate course code
    if db.query(Course).filter(Course.code == data.code).first():
        raise HTTPException(status_code=400, detail="Course code already exists")

    # Create the new Course ORM object
    course = Course(
        code=data.code,
        name=data.name,
        level=data.level,
        credits=data.credits,
        is_lab=data.is_lab,
        department_id=data.department_id,
    )

    # Link prerequisite courses if any were specified
    if data.prerequisite_codes:
        # Look up all prerequisite courses by their codes in a single query
        prereqs = db.query(Course).filter(Course.code.in_(data.prerequisite_codes)).all()
        course.prerequisites = prereqs

    # Persist the new course
    db.add(course)
    db.commit()
    db.refresh(course)  # Reload to get auto-generated ID and loaded relationships

    return _course_to_response(course)


@router.put("/courses/{course_id}", response_model=CourseResponse)
def update_course(
    course_id: int,
    data: CourseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update an existing course (instructor/admin only).

    Supports partial updates -- only fields present in the request body
    are modified. All other fields retain their current values.

    Args:
        course_id:    The primary key of the course to update.
        data:         Validated CourseUpdate schema with optional fields.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (must be instructor or admin).

    Returns:
        CourseResponse: The updated course with full details.

    Raises:
        HTTPException 403: If the user is not an instructor or admin.
        HTTPException 404: If no course exists with the given ID.
    """
    # Authorization check
    if current_user.role not in (UserRole.INSTRUCTOR, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only instructors can update courses")

    # Look up the course to update
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Apply partial updates -- only set fields that were explicitly provided
    if data.name is not None:
        course.name = data.name
    if data.level is not None:
        course.level = data.level
    if data.credits is not None:
        course.credits = data.credits
    if data.is_lab is not None:
        course.is_lab = data.is_lab
    if data.is_archived is not None:
        course.is_archived = data.is_archived
    if data.prerequisite_codes is not None:
        # Replace the entire prerequisite list with the new one
        prereqs = db.query(Course).filter(Course.code.in_(data.prerequisite_codes)).all()
        course.prerequisites = prereqs

    # Persist changes
    db.commit()
    db.refresh(course)

    return _course_to_response(course)


@router.delete("/courses/{course_id}")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a course and all its sections (instructor/admin only).

    This is a hard delete -- the course and its sections are permanently
    removed from the database. For soft deletion, use the update endpoint
    to set is_archived=True instead.

    Args:
        course_id:    The primary key of the course to delete.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (must be instructor or admin).

    Returns:
        dict: {"message": "Course deleted"} on success.

    Raises:
        HTTPException 403: If the user is not an instructor or admin.
        HTTPException 404: If no course exists with the given ID.
    """
    # Authorization check
    if current_user.role not in (UserRole.INSTRUCTOR, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only instructors can delete courses")

    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Delete the course (cascade deletes sections due to "all, delete-orphan")
    db.delete(course)
    db.commit()

    return {"message": "Course deleted"}


# ===========================================================================
# Section Endpoints
# ===========================================================================

@router.post("/sections", response_model=SectionResponse)
def create_section(
    data: SectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new section for a course (instructor/admin only).

    Sections represent specific offerings of a course (e.g., "CS371-M1"
    for male section 1 of CS371). Each section has its own gender
    designation and capacity.

    Args:
        data:         Validated SectionCreate schema from the request body.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (must be instructor or admin).

    Returns:
        SectionResponse: The newly created section with course details.

    Raises:
        HTTPException 403: If the user is not an instructor or admin.
    """
    # Authorization check
    if current_user.role not in (UserRole.INSTRUCTOR, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only instructors can create sections")

    # Create the Section ORM object
    section = Section(
        section_id=data.section_id,
        gender=data.gender,
        capacity=data.capacity,
        course_id=data.course_id,
    )

    # Persist the new section
    db.add(section)
    db.commit()
    db.refresh(section)

    # Look up the parent course to include its code and name in the response
    course = db.query(Course).filter(Course.id == section.course_id).first()

    return SectionResponse(
        id=section.id,
        section_id=section.section_id,
        gender=section.gender,
        capacity=section.capacity,
        enrolled=section.enrolled,
        course_id=section.course_id,
        course_code=course.code if course else "",
        course_name=course.name if course else "",
    )


@router.get("/sections", response_model=List[SectionResponse])
def list_sections(course_id: int = None, gender: str = None, db: Session = Depends(get_db)):
    """
    List sections with optional filtering by course and/or gender.

    Only non-archived sections are returned. Each section includes
    the parent course's code and name for display purposes.

    Query parameters:
        course_id: Filter by parent course ID (optional).
        gender:    Filter by gender designation (optional).

    Args:
        db: Database session injected by FastAPI.

    Returns:
        List[SectionResponse]: Matching sections with course details.
    """
    # Start with non-archived sections only
    q = db.query(Section).filter(Section.is_archived == False)

    # Apply optional filters
    if course_id:
        q = q.filter(Section.course_id == course_id)
    if gender:
        q = q.filter(Section.gender == gender)

    sections = q.all()

    # Build the response list, looking up the parent course for each section
    result = []
    for s in sections:
        course = db.query(Course).filter(Course.id == s.course_id).first()
        result.append(SectionResponse(
            id=s.id,
            section_id=s.section_id,
            gender=s.gender,
            capacity=s.capacity,
            enrolled=s.enrolled,
            course_id=s.course_id,
            course_code=course.code if course else "",
            course_name=course.name if course else "",
        ))

    return result
