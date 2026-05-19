"""
schemas.py -- Pydantic Models for API Request Validation and Response Serialization
====================================================================================

This module defines all Pydantic models (also called "schemas") used by the
IMAMU Course Scheduler API. Pydantic models serve two purposes:

1. **Request Validation** -- When a client sends JSON in a POST/PUT body,
   FastAPI automatically deserializes it into the corresponding Pydantic
   model, validating types and required fields. If validation fails, FastAPI
   returns a 422 Unprocessable Entity response with detailed error messages.

2. **Response Serialization** -- When a route declares `response_model=X`,
   FastAPI serializes the return value through model X, ensuring only the
   declared fields are included and types are correct.

Naming convention used here:
    - *Create   -- Fields required to create a new resource (request body).
    - *Update   -- Fields that can be partially updated (all Optional).
    - *Response -- Fields returned to the client (includes computed fields).
    - *Request  -- Fields for action requests (e.g., schedule generation).

Architecture note:
    These schemas are deliberately separate from the SQLAlchemy ORM models
    (defined in models/models.py). This separation follows the "repository
    pattern" -- the database layer and the API layer each have their own
    data contracts, which makes it easy to change one without affecting
    the other.
"""

from pydantic import BaseModel
from typing import Optional, List


# ===========================================================================
# Authentication Schemas
# ===========================================================================

class UserCreate(BaseModel):
    """
    Schema for user registration requests (POST /api/auth/register).

    All fields are required except those with defaults. The 'role' field
    defaults to "student" so that most sign-ups do not need to specify it.

    Attributes:
        username:   Unique login name (must not already exist in the database).
        email:      Unique email address for the user account.
        password:   Plain-text password (will be hashed before storage).
        full_name:  User's display name (e.g., "Ahmed Al-Rashid").
        role:       One of "student", "instructor", or "admin". Defaults to "student".
        gender:     "Male" or "Female" -- used for gender-segregated scheduling
                    (IMAMU follows Saudi university gender policies).
        department: Optional department name the user belongs to.
        level:      Academic level (1-8 for an 8-semester program). Defaults to 1.
    """
    username: str
    email: str
    password: str
    full_name: str
    role: str = "student"
    gender: str = "Male"
    department: str = ""
    level: int = 1


class UserLogin(BaseModel):
    """
    Schema for login requests (POST /api/auth/login).

    Attributes:
        username: The user's registered username.
        password: The user's plain-text password (verified against bcrypt hash).
    """
    username: str
    password: str


class UserResponse(BaseModel):
    """
    Schema for user data returned in API responses.

    This model omits sensitive fields (hashed_password, is_active) and
    exposes only the information the frontend needs to display user
    profiles and manage role-based UI elements.

    Attributes:
        id:         Database primary key.
        username:   Unique login name.
        email:      User's email address.
        full_name:  Display name.
        role:       User role ("student", "instructor", or "admin").
        gender:     "Male" or "Female".
        department: Department name (may be empty string if unset).
        level:      Academic level (1-8).
    """
    id: int
    username: str
    email: str
    full_name: str
    role: str
    gender: str
    department: str
    level: int

    class Config:
        # Allows Pydantic to read data from SQLAlchemy model attributes
        # (ORM mode), not just plain dicts.
        from_attributes = True


class TokenResponse(BaseModel):
    """
    Schema for authentication responses (returned after register or login).

    Contains the JWT access token and the authenticated user's profile.
    The frontend stores the token and sends it in the Authorization header
    for all subsequent API requests.

    Attributes:
        access_token: The signed JWT string.
        token_type:   Always "bearer" (OAuth2 convention).
        user:         Nested UserResponse with the authenticated user's profile.
    """
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ===========================================================================
# Course Schemas
# ===========================================================================

class CourseCreate(BaseModel):
    """
    Schema for creating a new course (POST /api/courses).

    Only instructors and admins may create courses. Prerequisite courses
    are specified by their course codes (e.g., ["CS101", "CS102"]).

    Attributes:
        code:               Unique course code (e.g., "CS371").
        name:               Full course name (e.g., "Software Engineering").
        level:              Academic level the course belongs to (1-8).
        credits:            Credit hours (typically 2 or 3).
        is_lab:             True if this is a laboratory section.
        department_id:      Foreign key to the Department table.
        prerequisite_codes: List of course codes that must be completed first.
    """
    code: str
    name: str
    level: int = 1
    credits: int = 3
    is_lab: bool = False
    department_id: int
    prerequisite_codes: List[str] = []


class CourseUpdate(BaseModel):
    """
    Schema for partially updating an existing course (PUT /api/courses/{id}).

    All fields are Optional -- only the fields provided in the request body
    will be updated; all others remain unchanged (partial update / PATCH
    semantics implemented via PUT).

    Attributes:
        name:               Updated course name (or None to keep current).
        level:              Updated academic level.
        credits:            Updated credit hours.
        is_lab:             Updated lab flag.
        is_archived:        Set to True to soft-delete (archive) the course.
        prerequisite_codes: Updated list of prerequisite course codes.
    """
    name: Optional[str] = None
    level: Optional[int] = None
    credits: Optional[int] = None
    is_lab: Optional[bool] = None
    is_archived: Optional[bool] = None
    prerequisite_codes: Optional[List[str]] = None


class CourseResponse(BaseModel):
    """
    Schema for course data returned in API responses.

    Includes computed/joined fields like department_name, prerequisites
    (as a list of course code strings), and nested section summaries.

    Attributes:
        id:              Database primary key.
        code:            Unique course code.
        name:            Course name.
        level:           Academic level (1-8).
        credits:         Credit hours.
        is_lab:          Whether this is a lab course.
        is_archived:     Whether the course has been soft-deleted.
        department_id:   Foreign key to Department.
        department_name: Joined department name for display.
        prerequisites:   List of prerequisite course codes (strings).
        sections:        List of section summary dicts (id, section_id, gender, etc.).
    """
    id: int
    code: str
    name: str
    level: int
    credits: int
    is_lab: bool
    is_archived: bool
    department_id: int
    department_name: str = ""
    prerequisites: List[str] = []
    sections: List[dict] = []

    class Config:
        from_attributes = True


# ===========================================================================
# Section Schemas
# ===========================================================================

class SectionCreate(BaseModel):
    """
    Schema for creating a new section (POST /api/sections).

    A section is a specific offering of a course (e.g., "CS371-M1" for the
    first male section of CS371).

    Attributes:
        section_id: Unique section identifier string (e.g., "CS371-M1").
        gender:     "Male" or "Female" -- determines which students/rooms.
        capacity:   Maximum number of students that can enroll.
        course_id:  Foreign key linking this section to its parent course.
    """
    section_id: str
    gender: str = "Male"
    capacity: int = 40
    course_id: int


class SectionResponse(BaseModel):
    """
    Schema for section data returned in API responses.

    Includes joined course information (code and name) so the frontend
    can display section listings without a second API call.

    Attributes:
        id:           Database primary key.
        section_id:   Unique section identifier string.
        gender:       "Male" or "Female".
        capacity:     Maximum enrollment capacity.
        enrolled:     Current number of enrolled students.
        course_id:    Foreign key to the parent course.
        course_code:  Joined course code for display.
        course_name:  Joined course name for display.
        is_archived:  Whether the section has been soft-deleted.
    """
    id: int
    section_id: str
    gender: str
    capacity: int
    enrolled: int
    course_id: int
    course_code: str = ""
    course_name: str = ""
    is_archived: bool = False

    class Config:
        from_attributes = True


# ===========================================================================
# Schedule Generation Schemas
# ===========================================================================

class ScheduleGenerateRequest(BaseModel):
    """
    Schema for schedule generation requests (POST /api/schedule/generate).

    The client specifies which courses to schedule, which optimization
    algorithm to use, and which objective function to optimize.

    Attributes:
        selected_course_ids: List of Course.id values to include in the schedule.
                             If empty, the system uses the student's registered courses.
        algorithm:           "GA" for Genetic Algorithm or "PSO" for Particle Swarm
                             Optimization. Defaults to "GA".
        objective:           Optimization objective:
                             - "student"    -- Minimize gaps, prefer consecutive slots.
                             - "instructor" -- Compact teaching days, balanced workloads.
                             - "university" -- Minimize all hard and soft constraint violations.
                             - "all"        -- Run all three objectives and return combined results.
        preferred_gender:    Optional gender filter for room/section matching.
    """
    selected_course_ids: List[int] = []
    algorithm: str = "GA"
    objective: str = "student"
    preferred_gender: Optional[str] = None


class ScheduleSlot(BaseModel):
    """
    Represents a single class meeting in a generated schedule.

    Each slot maps one section of a course to a specific instructor, room,
    day, and time period. The frontend renders these as cells in a weekly
    timetable grid.

    Attributes:
        course_code:     Course identifier (e.g., "CS371").
        course_name:     Full course name.
        section_id:      Section identifier (e.g., "CS371-M1").
        department:      Department name.
        instructor_name: Assigned instructor's full name.
        room:            Room identifier (e.g., "B2-101").
        day:             Day of the week (e.g., "Sun", "Mon").
        time:            Time slot label (e.g., "08:00-08:50").
        credits:         Course credit hours.
        level:           Academic level of the course.
        gender:          Section gender ("Male" or "Female").
    """
    course_code: str
    course_name: str
    section_id: str
    department: str
    instructor_name: str
    room: str
    day: str
    time: str
    credits: int
    level: int
    gender: str


class ScheduleResult(BaseModel):
    """
    Represents one complete candidate schedule returned by the optimizer.

    The generation endpoint typically returns the top N schedules (ranked
    by fitness), each containing a list of ScheduleSlot objects.

    Attributes:
        rank:        Position in the ranking (1 = best).
        label:       Human-readable label (e.g., "Student-Optimized").
        description: Brief explanation of what the objective optimizes.
        fitness:     Fitness score from the optimization algorithm (higher = better).
        conflicts:   Number of constraint violations in this schedule.
        objective:   Which objective function produced this result.
        slots:       List of all class meeting slots in the schedule.
    """
    rank: int
    label: str
    description: str
    fitness: float
    conflicts: float
    objective: str
    slots: List[ScheduleSlot]


class SaveScheduleRequest(BaseModel):
    """
    Schema for saving a generated schedule to the database (POST /api/schedule/save).

    The schedule_data field contains a JSON-encoded string of the full
    schedule (serialized list of slot objects). This allows the user to
    revisit saved schedules later without regenerating them.

    Attributes:
        name:          User-chosen name for the saved schedule.
        algorithm:     Algorithm that generated it ("GA" or "PSO").
        objective:     Objective that was optimized.
        fitness:       Fitness score at generation time.
        conflicts:     Conflict count at generation time.
        schedule_data: JSON string containing the full list of schedule slots.
    """
    name: str = "My Schedule"
    algorithm: str = "GA"
    objective: str = "student"
    fitness: float = 0.0
    conflicts: float = 0.0
    schedule_data: str


# ===========================================================================
# Reference Data Response Schemas
# ===========================================================================
# These schemas serialize read-only reference/lookup data that the frontend
# uses to populate dropdowns, filters, and informational displays.

class DepartmentResponse(BaseModel):
    """
    Schema for department data (GET /api/departments).

    Attributes:
        id:   Database primary key.
        name: Full department name (e.g., "Computer Science").
        code: Short department code (e.g., "CS").
    """
    id: int
    name: str
    code: str

    class Config:
        from_attributes = True


class InstructorResponse(BaseModel):
    """
    Schema for instructor data (GET /api/instructors).

    Attributes:
        id:            Database primary key.
        instructor_id: University-assigned instructor code.
        name:          Instructor's full name.
        gender:        "Male" or "Female".
        department:    Department the instructor belongs to.
        rank:          Academic rank (e.g., "Professor", "Lecturer").
        min_hours:     Minimum required weekly teaching hours.
        max_hours:     Maximum allowed weekly teaching hours.
    """
    id: int
    instructor_id: str
    name: str
    gender: str
    department: str
    rank: str
    min_hours: int
    max_hours: int

    class Config:
        from_attributes = True


class RoomResponse(BaseModel):
    """
    Schema for room/lab data (GET /api/rooms).

    Attributes:
        id:        Database primary key.
        room_id:   Room identifier (e.g., "B2-101").
        capacity:  Maximum seating capacity.
        gender:    Gender designation ("Male", "Female", or "" for shared).
        room_type: Type of room (e.g., "Lecture Hall", "Lab").
        floor:     Floor number in the building.
        building:  Building name or code.
    """
    id: int
    room_id: str
    capacity: int
    gender: str
    room_type: str
    floor: int
    building: str

    class Config:
        from_attributes = True


class DayResponse(BaseModel):
    """
    Schema for day-of-week data (GET /api/days).

    Attributes:
        id:   Database primary key (also determines display order).
        code: Short code (e.g., "SUN", "MON").
        name: Full day name (e.g., "Sunday", "Monday").
    """
    id: int
    code: str
    name: str

    class Config:
        from_attributes = True


class TimeSlotResponse(BaseModel):
    """
    Schema for daily time period data (GET /api/timeslots).

    Attributes:
        id:         Database primary key.
        label:      Display label combining start and end (e.g., "08:00-08:50").
        start_time: Period start time string (e.g., "08:00").
        end_time:   Period end time string (e.g., "08:50").
        slot_type:  "Class" for teaching periods or "Break" for prayer/lunch breaks.
        slot_order: Integer defining the chronological ordering of class slots.
    """
    id: int
    label: str
    start_time: str
    end_time: str
    slot_type: str
    slot_order: int

    class Config:
        from_attributes = True
