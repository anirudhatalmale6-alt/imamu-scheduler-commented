"""
registration.py -- Student Course Section Registration Endpoints
=================================================================

This module defines the REST API endpoints that allow students to register
for (enroll in) and unregister from course sections. All endpoints require
authentication via a JWT Bearer token.

Endpoints:
    POST   /api/registration/register/{section_id}   -- Register for a section.
    DELETE /api/registration/unregister/{section_id}  -- Drop a section.
    GET    /api/registration/my-registrations         -- List the student's current enrollments.

Business rules enforced:
    1. A student cannot register for the same section twice.
    2. A student cannot register for two different sections of the same course
       (e.g., cannot be in both CS371-M1 and CS371-M2 simultaneously).
    3. The enrolled count on the Section is automatically updated when
       students register or unregister.

Architecture note:
    Registration uses SQLAlchemy's many-to-many relationship between User and
    Section (via the student_registrations association table). Adding or
    removing items from user.registered_sections automatically manages the
    junction table rows.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.models import User, Course, Section, student_registrations
from app.schemas import SectionResponse
from app.services.auth import get_current_user

# ---------------------------------------------------------------------------
# Router Configuration
# ---------------------------------------------------------------------------
# All registration endpoints are grouped under /api/registration/.
router = APIRouter(prefix="/api/registration", tags=["registration"])


# ---------------------------------------------------------------------------
# POST /api/registration/register/{section_id} -- Enroll in a Section
# ---------------------------------------------------------------------------
@router.post("/register/{section_id}")
def register_for_section(
    section_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Register the authenticated student for a specific course section.

    Validates that:
    - The section exists.
    - The student is not already registered for this specific section.
    - The student is not already registered for a different section of
      the same course (prevents double-enrollment in the same course).

    After successful registration, updates the section's enrolled count
    to reflect the new total number of registered students.

    Args:
        section_id:   The primary key of the section to register for.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (from JWT token).

    Returns:
        dict: Success message with the section identifier string.

    Raises:
        HTTPException 404: If the section does not exist.
        HTTPException 400: If the student is already registered for this
                           section or for another section of the same course.
    """
    # Look up the section by its primary key
    section = db.query(Section).filter(Section.id == section_id).first()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Check if the student is already registered for this exact section
    if section in current_user.registered_sections:
        raise HTTPException(status_code=400, detail="Already registered for this section")

    # Check if the student is already registered for another section of the same course
    # (e.g., registered for CS371-M1 and trying to register for CS371-M2)
    already_registered_course_ids = [s.course_id for s in current_user.registered_sections]
    if section.course_id in already_registered_course_ids:
        raise HTTPException(status_code=400, detail="Already registered for another section of this course")

    # Add the section to the student's registered sections (updates the M2M junction table)
    current_user.registered_sections.append(section)

    # Update the enrolled count on the section to reflect the new total
    # Uses the backref relationship to count all registered students
    section.enrolled = len(section.registered_students)

    # Persist the changes (junction table insert + enrolled count update)
    db.commit()

    return {"message": "Registered successfully", "section_id": section.section_id}


# ---------------------------------------------------------------------------
# DELETE /api/registration/unregister/{section_id} -- Drop a Section
# ---------------------------------------------------------------------------
@router.delete("/unregister/{section_id}")
def unregister_from_section(
    section_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Unregister (drop) the authenticated student from a course section.

    Validates that the student is actually registered for the section
    before attempting to remove the registration. Updates the section's
    enrolled count after removal.

    Args:
        section_id:   The primary key of the section to drop.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (from JWT token).

    Returns:
        dict: Success message confirming the unregistration.

    Raises:
        HTTPException 404: If the section does not exist.
        HTTPException 400: If the student is not registered for this section.
    """
    # Look up the section
    section = db.query(Section).filter(Section.id == section_id).first()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Verify the student is actually registered for this section
    if section not in current_user.registered_sections:
        raise HTTPException(status_code=400, detail="Not registered for this section")

    # Remove the section from the student's registrations (deletes junction table row)
    current_user.registered_sections.remove(section)

    # Update the enrolled count to reflect the removal
    section.enrolled = len(section.registered_students)

    # Persist the changes
    db.commit()

    return {"message": "Unregistered successfully"}


# ---------------------------------------------------------------------------
# GET /api/registration/my-registrations -- List Current Enrollments
# ---------------------------------------------------------------------------
@router.get("/my-registrations")
def get_my_registrations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all sections the authenticated student is currently registered for.

    Returns a list of section details including the parent course's code
    and name, so the frontend can display a complete registration summary
    without making additional API calls.

    Args:
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (from JWT token).

    Returns:
        list[dict]: A list of dicts, each containing section details and
                    the associated course code and name.
    """
    # Access the student's registered sections via the M2M relationship
    sections = current_user.registered_sections

    result = []
    for s in sections:
        # Look up the parent course to include its code and name
        course = db.query(Course).filter(Course.id == s.course_id).first()

        result.append({
            "id": s.id,
            "section_id": s.section_id,
            "gender": s.gender,
            "capacity": s.capacity,
            "enrolled": s.enrolled,
            "course_id": s.course_id,
            "course_code": course.code if course else "",
            "course_name": course.name if course else "",
        })

    return result
