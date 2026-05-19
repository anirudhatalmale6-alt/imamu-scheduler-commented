"""
models.py -- SQLAlchemy ORM Models (Database Schema Definition)
================================================================

This module defines the complete relational database schema for the IMAMU
Course Scheduler using SQLAlchemy's declarative ORM. Each Python class maps
to a database table, and each class attribute maps to a column or relationship.

Tables defined:
    users                    -- Student, instructor, and admin accounts.
    departments              -- Academic departments (CS, IT, IS, etc.).
    courses                  -- Course catalog with code, name, level, credits.
    instructors              -- Faculty members with workload constraints.
    rooms                    -- Classrooms and labs with capacity and gender.
    time_slots               -- Daily teaching periods (e.g., 08:00-08:50).
    days                     -- Working days of the week.
    sections                 -- Specific offerings of courses (gender-segregated).
    saved_schedules          -- User-saved generated schedules (JSON blob).

Association (many-to-many junction) tables:
    course_prerequisites     -- Which courses are prerequisites for which.
    student_completed_courses -- Courses a student has already completed.
    student_registrations    -- Which students are registered for which sections.

Entity-Relationship overview:
    Department  1---*  Course  1---*  Section
    Course     *---*  Course          (self-referential via course_prerequisites)
    User       *---*  Section         (via student_registrations)
    User       *---*  Course          (via student_completed_courses)
    User       1---*  SavedSchedule

Architecture note:
    All models inherit from 'Base' (the declarative base defined in database.py).
    When Base.metadata.create_all(engine) is called at startup, SQLAlchemy
    generates CREATE TABLE statements for every model registered with Base.
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, Float, ForeignKey, Table, Text,
    UniqueConstraint, Enum as SAEnum
)
from sqlalchemy.orm import relationship
import enum
from app.database import Base


# ===========================================================================
# Enumerations
# ===========================================================================

class UserRole(str, enum.Enum):
    """
    Enumeration of user roles in the system.

    Inherits from both str and enum.Enum so that the value can be used as
    a plain string (e.g., in JSON serialization) while still providing enum
    safety in Python code.

    Values:
        STUDENT    -- Can register for sections and generate/save schedules.
        INSTRUCTOR -- Can create/update/delete courses and sections.
        ADMIN      -- Has all permissions (same as instructor for now).
    """
    STUDENT = "student"
    INSTRUCTOR = "instructor"
    ADMIN = "admin"


# ===========================================================================
# Association (Junction) Tables for Many-to-Many Relationships
# ===========================================================================
# These tables do not have their own ORM class because they only store
# foreign key pairs. SQLAlchemy manages them through the 'secondary'
# parameter on relationship() definitions.

# --- Course Prerequisites (self-referential M2M on Course) ---
# Links a course to its prerequisite courses. For example, if CS371
# requires CS201, there will be a row: (course_id=CS371.id, prerequisite_id=CS201.id).
course_prerequisites = Table(
    "course_prerequisites",
    Base.metadata,
    Column("course_id", Integer, ForeignKey("courses.id"), primary_key=True),
    Column("prerequisite_id", Integer, ForeignKey("courses.id"), primary_key=True),
)

# --- Student Completed Courses (M2M: User <-> Course) ---
# Tracks which courses each student has already completed. Used for
# prerequisite checking during registration.
student_completed_courses = Table(
    "student_completed_courses",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("course_id", Integer, ForeignKey("courses.id"), primary_key=True),
)

# --- Student Registrations (M2M: User <-> Section) ---
# Tracks which sections each student is currently registered for.
# This is the core enrollment table -- used to count enrolled students
# and to feed the schedule generator with the student's course list.
student_registrations = Table(
    "student_registrations",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("section_id", Integer, ForeignKey("sections.id"), primary_key=True),
)


# ===========================================================================
# ORM Model Classes
# ===========================================================================

class User(Base):
    """
    Represents a user account (student, instructor, or admin).

    The User model is central to authentication, authorization, and
    enrollment tracking. Students can register for sections and save
    generated schedules. Instructors can manage courses and sections.

    Relationships:
        completed_courses   -- Many-to-many with Course (courses the student passed).
        registered_sections -- Many-to-many with Section (current enrollments).
        saved_schedules     -- One-to-many with SavedSchedule (bookmarked timetables).
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)   # Login identifier
    email = Column(String(255), unique=True, index=True, nullable=False)      # Contact email
    hashed_password = Column(String(255), nullable=False)                      # bcrypt hash (never plain text)
    full_name = Column(String(255), nullable=False)                            # Display name
    role = Column(SAEnum(UserRole), nullable=False, default=UserRole.STUDENT)  # Access control role
    gender = Column(String(10), default="Male")                                # Gender for segregated scheduling
    department = Column(String(100), default="")                               # Student's department name
    level = Column(Integer, default=1)                                         # Academic level (semester 1-8)
    is_active = Column(Boolean, default=True)                                  # Soft-delete / account deactivation flag

    # --- Relationships ---
    # Courses the student has completed (for prerequisite validation)
    completed_courses = relationship("Course", secondary=student_completed_courses, backref="completed_by")
    # Sections the student is currently enrolled in
    registered_sections = relationship("Section", secondary=student_registrations, backref="registered_students")
    # Schedules the user has saved for later reference
    saved_schedules = relationship("SavedSchedule", back_populates="user")


class Department(Base):
    """
    Represents an academic department (e.g., Computer Science, Information Technology).

    Each department contains multiple courses. The 'code' field provides a
    short abbreviation used in course codes (e.g., "CS" in "CS371").

    Relationships:
        courses -- One-to-many with Course (all courses offered by this department).
    """
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)   # Full department name
    code = Column(String(10), unique=True, nullable=False)    # Short code (e.g., "CS", "IT", "IS")

    # All courses that belong to this department
    courses = relationship("Course", back_populates="department")


class Course(Base):
    """
    Represents an academic course in the university catalog.

    A course has a unique code (e.g., "CS371"), belongs to one department,
    and may have prerequisites (other courses that must be completed first).
    Each course can have multiple sections (gender-segregated offerings).

    The is_archived flag enables soft-deletion -- archived courses are
    hidden from listings by default but remain in the database for
    historical records and saved schedules.

    Relationships:
        department    -- Many-to-one with Department.
        sections      -- One-to-many with Section (cascade delete: removing a
                         course also removes all its sections).
        prerequisites -- Self-referential many-to-many via course_prerequisites.
                         course.prerequisites = list of courses that must be
                         completed before enrolling in this course.
        required_by   -- Reverse of prerequisites (backref): courses that
                         depend on this course as a prerequisite.
    """
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)  # Course code (e.g., "CS371")
    name = Column(String(255), nullable=False)                           # Full course name
    level = Column(Integer, default=1)                                   # Academic level (1-8)
    credits = Column(Integer, default=3)                                 # Credit hours (typically 2-3)
    is_lab = Column(Boolean, default=False)                              # True for laboratory courses
    is_archived = Column(Boolean, default=False)                         # Soft-delete flag
    department_id = Column(Integer, ForeignKey("departments.id"))        # FK to owning department

    # --- Relationships ---
    department = relationship("Department", back_populates="courses")
    # Cascade "all, delete-orphan" ensures sections are deleted when their parent course is deleted
    sections = relationship("Section", back_populates="course", cascade="all, delete-orphan")
    # Self-referential M2M: this course's prerequisites
    prerequisites = relationship(
        "Course",
        secondary=course_prerequisites,
        primaryjoin=id == course_prerequisites.c.course_id,       # "I am the course"
        secondaryjoin=id == course_prerequisites.c.prerequisite_id, # "These are my prerequisites"
        backref="required_by",  # Reverse: courses that require this course
    )


class Instructor(Base):
    """
    Represents a faculty member / instructor.

    Instructors are loaded from the university.xml file and are assigned
    to class meetings by the scheduling algorithm. Workload constraints
    (min_hours, max_hours) are used by the optimizer to ensure fair
    distribution of teaching load.

    Attributes:
        instructor_id -- University-assigned ID (e.g., "P001").
        name          -- Full name of the instructor.
        gender        -- "Male" or "Female" (affects room assignment in
                         gender-segregated universities).
        department    -- Department name the instructor belongs to.
        rank          -- Academic rank (e.g., "Professor", "Associate Professor").
        min_hours     -- Minimum weekly teaching hours required by contract.
        max_hours     -- Maximum weekly teaching hours allowed.
    """
    __tablename__ = "instructors"

    id = Column(Integer, primary_key=True, index=True)
    instructor_id = Column(String(20), unique=True, nullable=False)  # University ID code
    name = Column(String(255), nullable=False)                        # Full name
    gender = Column(String(10), default="Male")                       # Gender designation
    department = Column(String(100), default="")                      # Department affiliation
    rank = Column(String(50), default="")                             # Academic rank
    min_hours = Column(Integer, default=12)                           # Min teaching hours/week
    max_hours = Column(Integer, default=18)                           # Max teaching hours/week


class Room(Base):
    """
    Represents a physical classroom or laboratory.

    Rooms have a gender designation (for gender-segregated campuses),
    a type (lecture hall vs. lab), and a capacity that the scheduler
    uses to avoid over-booking.

    Attributes:
        room_id   -- Room identifier (e.g., "B2-101").
        capacity  -- Maximum number of students the room can hold.
        gender    -- Gender designation ("Male", "Female", or "" for shared).
        room_type -- "Lecture Hall", "Lab", or other room category.
        floor     -- Floor number within the building.
        building  -- Building name or code.
        equipment -- Comma-separated list of available equipment (projector, etc.).
    """
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(String(20), unique=True, nullable=False)    # Room identifier
    capacity = Column(Integer, default=40)                        # Max seating capacity
    gender = Column(String(10), default="")                       # Gender designation
    room_type = Column(String(50), default="Lecture Hall")        # Room category
    floor = Column(Integer, default=1)                            # Floor number
    building = Column(String(100), default="")                    # Building name
    equipment = Column(String(255), default="")                   # Available equipment


class TimeSlot(Base):
    """
    Represents a daily teaching period (e.g., 08:00 to 08:50).

    The university day is divided into fixed periods. Some periods are
    designated as "Break" (for prayer times or lunch) and are not used
    for scheduling classes.

    Attributes:
        label      -- Human-readable label (e.g., "08:00-08:50").
        start_time -- Period start time as a string (e.g., "08:00").
        end_time   -- Period end time as a string (e.g., "08:50").
        slot_type  -- "Class" for teaching periods, "Break" for non-teaching periods.
        slot_order -- Integer for ordering class slots chronologically.
                      Only "Class" type slots are numbered; breaks are skipped.
    """
    __tablename__ = "time_slots"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(20), unique=True, nullable=False)      # Display label
    start_time = Column(String(10), nullable=False)               # Start time string
    end_time = Column(String(10), nullable=False)                 # End time string
    slot_type = Column(String(10), default="Class")               # "Class" or "Break"
    slot_order = Column(Integer, default=0)                       # Chronological ordering index


class Day(Base):
    """
    Represents a working day of the week.

    Saudi universities typically use Sunday through Thursday as working
    days (Friday and Saturday are the weekend).

    Attributes:
        code -- Short day code (e.g., "SUN", "MON", "TUE").
        name -- Full day name (e.g., "Sunday", "Monday").
    """
    __tablename__ = "days"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(5), unique=True, nullable=False)   # Short code
    name = Column(String(20), default="")                    # Full name


class Section(Base):
    """
    Represents a specific offering (section) of a course.

    In Saudi universities, courses are often offered in separate male and
    female sections. Each section has its own capacity and enrollment count.
    The scheduler assigns each section to a room, instructor, day, and
    time slot.

    Attributes:
        section_id  -- Unique section identifier (e.g., "CS371-M1" for male section 1).
        gender      -- "Male" or "Female" -- determines eligible rooms and instructors.
        capacity    -- Maximum number of students.
        enrolled    -- Current count of registered students (updated on registration).
        course_id   -- Foreign key to the parent Course.
        is_archived -- Soft-delete flag; archived sections are hidden from listings.

    Relationships:
        course              -- Many-to-one with Course.
        registered_students -- Many-to-many with User (via student_registrations backref).
    """
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(String(20), unique=True, nullable=False)  # Section identifier
    gender = Column(String(10), default="Male")                    # Gender designation
    capacity = Column(Integer, default=40)                         # Max enrollment
    enrolled = Column(Integer, default=0)                          # Current enrollment count
    course_id = Column(Integer, ForeignKey("courses.id"))          # FK to parent course
    is_archived = Column(Boolean, default=False)                   # Soft-delete flag

    # Parent course this section belongs to
    course = relationship("Course", back_populates="sections")


class SavedSchedule(Base):
    """
    Stores a user's saved (bookmarked) generated schedule.

    When a user generates schedules and finds one they like, they can save
    it. The full schedule is stored as a JSON string in the schedule_data
    column, along with metadata about the algorithm, objective, fitness
    score, and conflict count at the time of generation.

    Attributes:
        user_id       -- Foreign key to the User who saved this schedule.
        name          -- User-chosen name (e.g., "My Fall Schedule").
        algorithm     -- Algorithm used ("GA" or "PSO").
        objective     -- Objective that was optimized ("student", "instructor", etc.).
        fitness       -- Fitness score from the optimizer (higher = better).
        conflicts     -- Number of scheduling conflicts.
        schedule_data -- JSON string containing the full list of schedule slots.

    Relationships:
        user -- Many-to-one with User.
    """
    __tablename__ = "saved_schedules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))              # FK to owning user
    name = Column(String(255), default="My Schedule")              # User-chosen label
    algorithm = Column(String(10), default="GA")                   # "GA" or "PSO"
    objective = Column(String(20), default="student")              # Optimization objective
    fitness = Column(Float, default=0.0)                           # Fitness score
    conflicts = Column(Float, default=0.0)                         # Conflict count
    schedule_data = Column(Text, nullable=False)                   # JSON-encoded schedule slots

    # The user who saved this schedule
    user = relationship("User", back_populates="saved_schedules")
