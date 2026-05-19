"""
schedule.py -- Schedule Generation, Saving, and Retrieval Endpoints
====================================================================

This module defines the REST API endpoints for the core scheduling
functionality of the IMAMU Course Scheduler. All endpoints require
authentication via a JWT Bearer token.

Endpoints:
    POST   /api/schedule/generate          -- Generate optimized schedules using GA or PSO.
    POST   /api/schedule/save              -- Save a generated schedule for later retrieval.
    GET    /api/schedule/saved             -- List all schedules saved by the current user.
    DELETE /api/schedule/saved/{id}        -- Delete a previously saved schedule.

Workflow:
    1. The student registers for course sections (via /api/registration/).
    2. The student requests schedule generation, optionally specifying
       which courses to include, the algorithm (GA/PSO), and the
       optimization objective (student/instructor/university/all).
    3. The server runs the optimization algorithm and returns the top N
       candidate schedules, ranked by fitness score.
    4. The student reviews the results in the frontend timetable view
       and saves their preferred schedule.
    5. Saved schedules can be retrieved or deleted later.

Architecture note:
    The generate endpoint delegates to the scheduler service
    (app/services/scheduler.py), which bridges the web API with the
    GA_runner and pso_runner algorithm modules. The saved schedules
    are stored as JSON strings in the database, making them independent
    of the algorithm state.
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.models import User, SavedSchedule, Course
from app.schemas import ScheduleGenerateRequest, ScheduleResult, SaveScheduleRequest
from app.services.auth import get_current_user
from app.services.scheduler import run_schedule_generation

# ---------------------------------------------------------------------------
# Router Configuration
# ---------------------------------------------------------------------------
# All schedule endpoints are grouped under /api/schedule/.
router = APIRouter(prefix="/api/schedule", tags=["schedule"])


# ---------------------------------------------------------------------------
# POST /api/schedule/generate -- Generate Optimized Schedules
# ---------------------------------------------------------------------------
@router.post("/generate", response_model=List[ScheduleResult])
def generate_schedule(
    req: ScheduleGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate optimized class schedules using the specified algorithm.

    If no course IDs are provided in the request, the endpoint automatically
    uses the courses from the student's current section registrations. This
    allows a simple "generate my schedule" flow where the student only needs
    to register for sections first.

    The optimizer returns multiple candidate schedules ranked by fitness.
    The frontend displays these as tabs or a list, allowing the student
    to compare and choose their preferred timetable.

    Args:
        req:          ScheduleGenerateRequest with course IDs, algorithm, and objective.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (from JWT token).

    Returns:
        List[ScheduleResult]: Top N generated schedules, each containing a ranked
                              list of time slot assignments (ScheduleSlot objects).

    Raises:
        HTTPException 400: If no courses are selected and the student has no
                           section registrations.
    """
    # If the client didn't specify course IDs, fall back to the student's
    # currently registered sections to determine which courses to schedule
    if not req.selected_course_ids:
        registered = current_user.registered_sections
        # Extract unique course IDs from the student's registered sections
        req.selected_course_ids = list(set(s.course_id for s in registered))

    # Validate that we have at least one course to schedule
    if not req.selected_course_ids:
        raise HTTPException(
            status_code=400,
            detail="No courses selected. Register for courses first or provide course IDs."
        )

    # Delegate to the scheduler service, which runs the GA or PSO algorithm
    # and converts the results into API-friendly ScheduleResult objects
    results = run_schedule_generation(
        db=db,
        selected_course_ids=req.selected_course_ids,
        algorithm=req.algorithm,
        objective=req.objective,
        # Use the student's gender if no preferred gender was specified
        preferred_gender=req.preferred_gender or current_user.gender,
    )

    return results


# ---------------------------------------------------------------------------
# POST /api/schedule/save -- Save a Generated Schedule
# ---------------------------------------------------------------------------
@router.post("/save")
def save_schedule(
    req: SaveScheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save a generated schedule to the database for later retrieval.

    The schedule data is stored as a JSON string in the schedule_data column,
    along with metadata about the algorithm, objective, fitness score, and
    conflict count. This creates a persistent bookmark of the schedule that
    survives server restarts and algorithm re-runs.

    Args:
        req:          SaveScheduleRequest with name, algorithm, objective,
                      fitness, conflicts, and JSON schedule_data.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user who is saving the schedule.

    Returns:
        dict: Success message with the database ID of the saved schedule.
    """
    # Create a SavedSchedule ORM object linked to the current user
    saved = SavedSchedule(
        user_id=current_user.id,
        name=req.name,                  # User-chosen label (e.g., "My Fall Schedule")
        algorithm=req.algorithm,        # "GA" or "PSO"
        objective=req.objective,        # "student", "instructor", etc.
        fitness=req.fitness,            # Fitness score at time of generation
        conflicts=req.conflicts,        # Conflict count at time of generation
        schedule_data=req.schedule_data, # Full schedule as JSON string
    )

    # Persist the saved schedule
    db.add(saved)
    db.commit()
    db.refresh(saved)  # Reload to get the auto-generated ID

    return {"message": "Schedule saved", "id": saved.id}


# ---------------------------------------------------------------------------
# GET /api/schedule/saved -- List Saved Schedules
# ---------------------------------------------------------------------------
@router.get("/saved")
def get_saved_schedules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve all schedules saved by the current user.

    The schedule_data is deserialized from JSON string back into a Python
    list/dict structure so the frontend receives ready-to-use data.

    Args:
        db:           Database session injected by FastAPI.
        current_user: The authenticated user whose saved schedules to retrieve.

    Returns:
        list[dict]: A list of saved schedule records, each containing metadata
                    (name, algorithm, objective, fitness, conflicts) and the
                    deserialized schedule_data.
    """
    # Query all saved schedules belonging to the current user
    schedules = db.query(SavedSchedule).filter(
        SavedSchedule.user_id == current_user.id
    ).all()

    # Serialize each saved schedule into a response dict
    return [
        {
            "id": s.id,
            "name": s.name,
            "algorithm": s.algorithm,
            "objective": s.objective,
            "fitness": s.fitness,
            "conflicts": s.conflicts,
            # Parse the JSON string back into a Python object for the response
            "schedule_data": json.loads(s.schedule_data),
        }
        for s in schedules
    ]


# ---------------------------------------------------------------------------
# DELETE /api/schedule/saved/{schedule_id} -- Delete a Saved Schedule
# ---------------------------------------------------------------------------
@router.delete("/saved/{schedule_id}")
def delete_saved_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a previously saved schedule.

    Only the owner of the saved schedule can delete it -- the query filters
    by both schedule_id and user_id to prevent unauthorized deletion.

    Args:
        schedule_id:  The primary key of the saved schedule to delete.
        db:           Database session injected by FastAPI.
        current_user: The authenticated user (must be the schedule's owner).

    Returns:
        dict: Success message confirming deletion.

    Raises:
        HTTPException 404: If no saved schedule with the given ID exists
                           for the current user.
    """
    # Look up the saved schedule, ensuring it belongs to the current user
    saved = db.query(SavedSchedule).filter(
        SavedSchedule.id == schedule_id,
        SavedSchedule.user_id == current_user.id,  # Authorization: must own the schedule
    ).first()

    if not saved:
        raise HTTPException(status_code=404, detail="Schedule not found")

    # Permanently delete the saved schedule from the database
    db.delete(saved)
    db.commit()

    return {"message": "Schedule deleted"}
