"""
scheduler.py -- Bridge Between the Web API and the GA/PSO Scheduling Algorithms
=================================================================================

This module acts as an adapter (bridge) layer between the FastAPI web API and
the underlying scheduling optimization algorithms (Genetic Algorithm and
Particle Swarm Optimization). It is responsible for:

1. Loading university data from the XML file into the ImamuData structure
   that the GA/PSO algorithms expect (with caching to avoid re-parsing).
2. Configuring the algorithm parameters (population size, mutation rate, etc.)
   and invoking the correct optimizer based on the user's request.
3. Converting the raw Schedule objects returned by the GA/PSO engines into
   the API-friendly ScheduleResult and ScheduleSlot Pydantic models that
   the frontend can display directly.

Architecture overview:
    Web API (routes/schedule.py)
        --> scheduler.py (this file)
            --> GA_runner.py  (_run_ga)   -- Genetic Algorithm implementation
            --> pso_runner.py (_run_pso)  -- Particle Swarm Optimization
            --> schedule_maker.py        -- Schedule data structures & fitness

    The GA/PSO algorithms operate on their own internal data structures
    (Schedule, Class, Section, etc.) defined in schedule_maker.py. This
    module translates between those internal objects and the Pydantic
    schemas used by the web API.

File locations:
    This file:           backend/app/services/scheduler.py
    GA_runner.py:        backend/ (project root)
    schedule_maker.py:   backend/ (project root)
    university.xml:      backend/ (project root)
"""

import os
import sys
import time
import random
from typing import List
from sqlalchemy.orm import Session

from app.schemas import ScheduleResult, ScheduleSlot

# ---------------------------------------------------------------------------
# Path Setup -- Make the project root importable
# ---------------------------------------------------------------------------
# The GA and PSO runner modules (GA_runner.py, schedule_maker.py, pso_runner.py)
# live at the project root (backend/), not inside the app/ package. We need
# to add the project root to sys.path so Python can import them.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import the scheduling algorithm modules from the project root
import schedule_maker
import GA_runner as ga_module
from GA_runner import ImamuData, _run_ga, OBJECTIVE_UNIVERSITY, OBJECTIVE_INSTRUCTOR, OBJECTIVE_STUDENT


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Path to the university XML data file used by the scheduling algorithms
XML_FILE = os.path.join(_PROJECT_ROOT, "university.xml")

# Metadata for each optimization objective -- provides human-readable labels
# and descriptions that appear in the API response and frontend UI
OBJ_META = {
    OBJECTIVE_UNIVERSITY: {
        "label": "University-Optimized",
        "description": "Best overall schedule -- minimises all hard and soft conflicts",
    },
    OBJECTIVE_INSTRUCTOR: {
        "label": "Instructor-Optimized",
        "description": "Compact teaching days, minimal gaps, balanced workloads",
    },
    OBJECTIVE_STUDENT: {
        "label": "Student-Optimized",
        "description": "Consecutive lectures, fewer study days, no late slots",
    },
}

# ---------------------------------------------------------------------------
# Data Cache
# ---------------------------------------------------------------------------
# Cache the ImamuData object so we don't re-parse the XML file on every
# schedule generation request. The XML data is static (changes only on
# server restart), so caching is safe and significantly improves performance.
_data_cache = None


def _get_data(max_sections: int = 60) -> ImamuData:
    """
    Load and cache the university data from the XML file.

    On the first call, parses university.xml into an ImamuData object and
    caches it in the module-level _data_cache variable. Subsequent calls
    return the cached object immediately.

    Also sets schedule_maker.data to point to the loaded data, which is
    required by the internal scheduling classes.

    Args:
        max_sections: Maximum number of sections to load from the XML.
                      Limits problem size for faster optimization.

    Returns:
        ImamuData: The parsed university data structure containing all
                   courses, sections, rooms, instructors, days, and time slots.
    """
    global _data_cache
    if _data_cache is None:
        # Parse the XML and create the data structure on first call
        _data_cache = ImamuData(XML_FILE, max_sections=max_sections)
        # The schedule_maker module needs a reference to the data for
        # creating Schedule objects and computing fitness
        schedule_maker.data = _data_cache
    return _data_cache


def _slots_to_api(schedule_obj) -> List[ScheduleSlot]:
    """
    Convert a GA/PSO Schedule object into a list of API-friendly ScheduleSlot models.

    The internal Schedule object stores class assignments using its own
    data types (Class, Section, Course, Instructor, Room, etc.). This
    function extracts the relevant fields from each assigned class and
    maps them to the ScheduleSlot Pydantic model that the API returns.

    Args:
        schedule_obj: A Schedule object from GA_runner/schedule_maker with
                      a _classes list of assigned Class objects.

    Returns:
        List[ScheduleSlot]: One ScheduleSlot per class meeting, ready for
                            JSON serialization in the API response.
    """
    slots = []
    # Iterate over each class assignment in the schedule
    for cls in schedule_obj._classes:
        sec = cls.get_section()         # The section assigned to this class slot
        course = sec.get_course()       # The course this section belongs to

        # Map internal data structure fields to API schema fields
        slots.append(ScheduleSlot(
            course_code    = course.get_id(),              # e.g., "CS371"
            course_name    = course.get_name(),            # e.g., "Software Engineering"
            section_id     = sec.get_id(),                 # e.g., "CS371-M1"
            department     = sec.get_dept_name(),          # e.g., "Computer Science"
            instructor_name= cls.get_instructor().get_name(),  # Assigned instructor
            room           = cls.get_room().get_number(),      # Assigned room ID
            day            = cls.get_day().get_day(),           # Day of week
            time           = cls.get_meetingTime().get_time(),  # Time slot label
            credits        = course.get_credits(),             # Credit hours
            level          = course.get_level(),               # Academic level
            gender         = sec.get_gender(),                 # Section gender
        ))
    return slots


def run_schedule_generation(
    db: Session,
    selected_course_ids: List[int],
    algorithm: str = "GA",
    objective: str = "student",
    preferred_gender: str = "Male",
    max_sections: int = 60,
    pop_size: int = 50,
    mutation_rate: float = 0.15,
    top_n: int = 3,
) -> List[ScheduleResult]:
    """
    Main entry point: generate optimized schedules using the specified algorithm.

    This function is called by the /api/schedule/generate endpoint. It:
    1. Loads the university data (from cache or XML).
    2. Configures the algorithm parameters on the schedule_maker module.
    3. Determines which objective(s) to optimize.
    4. Runs the GA or PSO algorithm for each objective.
    5. Collects and ranks the results by fitness score.
    6. Returns the top N schedules as ScheduleResult objects.

    Args:
        db:                 Database session (currently unused but available for
                            future enhancements like filtering by registration).
        selected_course_ids: List of Course.id values to schedule.
        algorithm:          "GA" for Genetic Algorithm or "PSO" for Particle Swarm.
        objective:          "student", "instructor", "university", or "all".
        preferred_gender:   Gender filter for section/room matching.
        max_sections:       Max sections to load from XML data.
        pop_size:           Population size for the optimization algorithm.
        mutation_rate:      Probability of mutation per individual/particle.
        top_n:              Number of top schedules to return.

    Returns:
        List[ScheduleResult]: Up to top_n schedules ranked by fitness (best first).
    """
    # Load the university data (cached after first call)
    data = _get_data(max_sections)

    # Configure the schedule_maker module's global parameters.
    # These globals are read by the GA/PSO algorithms during population
    # initialization, crossover, mutation, and fitness evaluation.
    schedule_maker.data = data
    schedule_maker.POPULATION_SIZE         = pop_size
    schedule_maker.MUTATION_RATE           = mutation_rate
    # Elite schedules are preserved unchanged between generations (elitism).
    # Using at least 3 elites, or ~3% of population size.
    schedule_maker.NUMB_OF_ELITE_SCHEDULES = max(3, pop_size // 33)

    # --- Determine which objective function(s) to optimize ---
    # "all" runs all three objectives and combines the results.
    # Otherwise, run only the single requested objective.
    if objective == "all":
        objectives = [OBJECTIVE_UNIVERSITY, OBJECTIVE_INSTRUCTOR, OBJECTIVE_STUDENT]
    else:
        # Map string names to the internal objective constants
        obj_map = {
            "university": OBJECTIVE_UNIVERSITY,
            "instructor": OBJECTIVE_INSTRUCTOR,
            "student":    OBJECTIVE_STUDENT,
        }
        objectives = [obj_map.get(objective, OBJECTIVE_STUDENT)]

    # Collect results from all objective runs
    results: List[ScheduleResult] = []
    rank = 1  # Temporary rank counter (will be re-sorted later)

    # --- Run the optimizer for each objective ---
    for obj in objectives:
        meta = OBJ_META[obj]  # Get the label and description for this objective

        if algorithm.upper() == "PSO":
            # --- Particle Swarm Optimization ---
            # Lazy import: pso_runner is only loaded if PSO is requested
            import pso_runner as pso_module

            # Sync shared state between schedule_maker and the PSO module.
            # The PSO module has its own reference to schedule_maker, so we
            # need to ensure it uses the same data and precomputed lookups.
            pso_module.schedule_maker.data = data
            pso_module.schedule_maker.PRAYER_SLOTS     = schedule_maker.PRAYER_SLOTS
            pso_module.schedule_maker.MALE_ROOMS       = schedule_maker.MALE_ROOMS
            pso_module.schedule_maker.FEMALE_ROOMS     = schedule_maker.FEMALE_ROOMS
            pso_module.schedule_maker.SLOT_ORDER        = schedule_maker.SLOT_ORDER
            pso_module.schedule_maker.CONSECUTIVE_PAIRS = schedule_maker.CONSECUTIVE_PAIRS

            # Set the current optimization objective for the PSO fitness function
            pso_module.CURRENT_OBJECTIVE = obj

            # Run the PSO algorithm and get the top N schedules
            schedules = pso_module._run_pso(
                label=meta["label"],
                objective=obj,
                data=data,
                swarm_size=pop_size,
                mutation_rate=mutation_rate,
                top_n=top_n,
            )
        else:
            # --- Genetic Algorithm (default) ---
            # Set the current optimization objective for the GA fitness function
            ga_module.CURRENT_OBJECTIVE = obj

            # Run the GA and get the top N schedules
            schedules = _run_ga(
                label=meta["label"],
                objective=obj,
                data=data,
                pop_size=pop_size,
                mutation_rate=mutation_rate,
                top_n=top_n,
            )

        # Convert each raw Schedule object into an API ScheduleResult
        for sched in schedules:
            results.append(ScheduleResult(
                rank        = rank,
                label       = meta["label"],
                description = meta["description"],
                fitness     = round(sched.get_fitness(), 4),         # Round for clean display
                conflicts   = round(sched.get_numbOfConflicts(), 2), # Conflict count
                objective   = obj,
                slots       = _slots_to_api(sched),                  # Convert class assignments
            ))
            rank += 1

    # --- Sort all results by fitness (highest = best) and reassign ranks ---
    results.sort(key=lambda r: r.fitness, reverse=True)
    for i, r in enumerate(results, 1):
        r.rank = i  # 1-based ranking after sorting

    # Return only the top N results
    return results[:top_n]


def _slots_from_dict(schedule_data: list) -> List[ScheduleSlot]:
    """
    Convert a list of raw dict objects into ScheduleSlot Pydantic models.

    This is a utility function used when schedule data comes from PSO's
    raw dictionary output format (rather than the GA's Schedule objects).
    It maps dictionary keys to ScheduleSlot attributes, handling minor
    naming differences between the two algorithm outputs.

    Args:
        schedule_data: List of dicts, each representing one class assignment.
                       Expected keys: course_code, course_name, section_id,
                       department, professor_name/instructor_name, room_id,
                       day, start/time, credits, level, gender.

    Returns:
        List[ScheduleSlot]: Corresponding Pydantic model objects.
    """
    slots = []
    for item in schedule_data:
        slots.append(ScheduleSlot(
            course_code     = item.get("course_code", ""),
            course_name     = item.get("course_name", ""),
            section_id      = item.get("section_id", ""),
            department      = item.get("department", ""),
            # Handle naming difference: PSO uses "professor_name", GA uses "instructor_name"
            instructor_name = item.get("professor_name", item.get("instructor_name", "")),
            room            = item.get("room_id", ""),
            # Handle naming difference: PSO uses "start", GA uses "time"
            day             = item.get("day", ""),
            time            = item.get("start", item.get("time", "")),
            credits         = item.get("credits", 3),
            level           = item.get("level", 1),
            gender          = item.get("gender", "Male"),
        ))
    return slots
