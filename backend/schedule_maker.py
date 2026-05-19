###############################################################################
#                        IMAMU COURSE SCHEDULING ENGINE                        #
#                                                                             #
#  This module is the core scheduling engine for the Imam Mohammad Ibn Saud   #
#  Islamic University (IMAMU) course scheduler. It provides all data          #
#  structures and algorithms used by both:                                    #
#    - GA_runner.py  (Genetic Algorithm approach)                             #
#    - pso_runner.py (Particle Swarm Optimization approach)                   #
#                                                                             #
#  The scheduling problem is a type of constraint satisfaction / optimization #
#  problem. The goal is to assign every course section a room, instructor,    #
#  day, and time slot such that hard constraints (absolute requirements) are  #
#  never violated and soft constraints (preferences) are minimized.           #
#                                                                             #
#  IMAMU-specific considerations:                                             #
#    - Gender-segregated campuses: male and female students attend in         #
#      separate buildings, so rooms are partitioned by gender.                #
#    - Prayer times: certain time slots overlap with daily prayers and must   #
#      be blocked out (no classes scheduled).                                 #
#    - Arabic academic week: Sunday through Thursday (not Mon-Fri).           #
#    - Credit-hour patterns dictate how many 50-minute slots per week a       #
#      course needs and whether they must be consecutive (back-to-back).      #
#                                                                             #
#  Metaheuristic Background:                                                  #
#    Both GA and PSO are population-based metaheuristic algorithms that       #
#    search for near-optimal solutions by maintaining and iteratively         #
#    improving a collection of candidate solutions (schedules).               #
#                                                                             #
#    - Genetic Algorithm (GA): Inspired by Darwinian evolution. A population  #
#      of schedules evolves over generations via selection, crossover         #
#      (recombination), and mutation. Fitter schedules survive and reproduce. #
#                                                                             #
#    - Particle Swarm Optimization (PSO): Inspired by the flocking behavior  #
#      of birds. Each "particle" is a schedule that moves through the        #
#      solution space, influenced by its own best-known position and the     #
#      swarm's global best position. This module provides helper methods     #
#      (extract_group_state / apply_group_state) that let PSO serialize and  #
#      manipulate schedule states as discrete position vectors.              #
###############################################################################

import prettytable    # Used by DisplayMgr to render schedules as ASCII tables
import random as rnd  # Core randomness source for initialization and genetic operators

# =============================================================================
# GENETIC ALGORITHM (GA) HYPERPARAMETERS
# =============================================================================
# These constants control how the GA explores the solution space. Tuning them
# affects convergence speed, solution quality, and diversity.

POPULATION_SIZE = 50
# Number of candidate schedules maintained in each generation.
# Larger populations increase diversity (reducing premature convergence)
# but require more memory and fitness evaluations per generation.

CROSSOVER_RATE = 0.8
# Probability that a SectionGroup gene is inherited from parent 1 during
# uniform crossover. With probability (1 - CROSSOVER_RATE) = 0.2, the gene
# comes from parent 2 instead. Higher values bias the child toward the
# tournament winner (parent 1).

NUMB_OF_ELITE_SCHEDULES = 2
# Elitism count: the top N schedules from each generation are carried forward
# unchanged into the next generation. This guarantees that the best solution
# found so far is never lost due to crossover or mutation. A value of 2 means
# the two fittest schedules are always preserved.

TOURNAMENT_SELECTION_SIZE = 8
# Number of randomly chosen individuals that compete in each tournament
# selection round. Larger tournaments increase selection pressure (fitter
# individuals are more likely to be chosen), which speeds convergence but
# can reduce diversity. Typical values range from 2 to 10.

MUTATION_RATE = 0.15
# Per-gene probability that a SectionGroup is replaced with a completely new
# random assignment during mutation. Mutation introduces diversity and helps
# escape local optima. Too high a rate turns the search into random walk;
# too low means the algorithm may stagnate.

# =============================================================================
# CONSTRAINT WEIGHTS
# =============================================================================
# The fitness function penalizes constraint violations. Hard constraints carry
# a heavy penalty (HARD_WEIGHT = 1.0) because they represent absolute rules
# that a valid schedule must never violate. Soft constraints carry a lighter
# penalty (SOFT_WEIGHT = 0.1) because they represent preferences that improve
# quality but are not strictly required.
#
# The final fitness value is: fitness = 1 / (1 + total_penalty)
# A perfect schedule with zero violations yields fitness = 1.0 (the maximum).

HARD_WEIGHT = 1.0   # Penalty added for each hard constraint violation
SOFT_WEIGHT = 0.1   # Penalty added for each soft constraint violation

# =============================================================================
# IMAMU-SPECIFIC CONSTANTS (populated at runtime by GA_runner.py / pso_runner.py)
# =============================================================================
# These are module-level lists/dicts that are filled in by the runner scripts
# after loading university data from the database or Excel. They remain empty
# here and serve as global configuration that Schedule.calculate_fitness() and
# Schedule.initialize() read during execution.

MALE_ROOMS = []
# List of room numbers (strings) assigned to the male campus.
# Used by HC6 (gender separation constraint) to detect violations when a
# female section is assigned to a male-only room.

FEMALE_ROOMS = []
# List of room numbers (strings) assigned to the female campus.
# Symmetric counterpart to MALE_ROOMS for HC6 enforcement.

SLOT_ORDER = {}   # e.g. {"08:25-09:15": 0, "09:20-10:10": 1, ...}
# Maps each time-slot label to its chronological index within the day.
# Index 0 = first slot of the day, higher indices = later slots.
# Used by:
#   - SC1 (last-slot penalty): identifies the final slot of the day
#   - SC2 (gap minimization): computes the distance between an instructor's
#     slots on the same day to detect idle gaps
#   - SC4 (consecutive sections): checks if same-course sections on the same
#     day are back-to-back or have gaps between them
#   - _pick_consecutive_pair(): validates that two slots are truly adjacent

PRAYER_SLOTS = set()
# Set of time-slot labels (strings) that overlap with daily prayer times.
# HC5 penalizes any class scheduled in one of these slots.
# Example: {"12:00-12:50"} for Dhuhr prayer time.

CONSECUTIVE_PAIRS = []   # [(slot_i_label, slot_i+1_label), ...]
# Pre-computed list of tuples where each tuple contains two time-slot labels
# that are physically back-to-back (e.g., ("08:25-09:15", "09:20-10:10")).
# Used by _pick_consecutive_pair() when building SectionGroups for courses
# that require consecutive (double-period) meetings.


# =============================================================================
# CLASS: Day
# =============================================================================
# Simple value object wrapping a day-of-week code string.
# IMAMU uses Arabic academic week codes: SUN, MON, TUE, WED, THU.
# Each Day instance is stored in Data._days and referenced by ClassSlot._day.

class Day:
    def __init__(self, day):
        """
        Parameters
        ----------
        day : str
            Short day code, e.g. "SUN", "MON", "TUE", "WED", "THU".
        """
        self._day = day

    def get_day(self):
        """Returns the day code string."""
        return self._day


# =============================================================================
# CLASS: Course -- Static Course Catalog Data
# =============================================================================
# Represents a course in the university catalog (e.g., "CS101 - Introduction
# to Programming"). This is static, read-only data loaded from the database.
# A Course can have multiple Sections (one per gender, or multiple sections
# of the same gender if enrollment is high).
#
# Key attributes:
#   - course_id:      Unique catalog identifier (e.g., "CS101")
#   - name:           Human-readable course title
#   - level:          Academic year/level (1-8); used by HC3 to detect student
#                     group conflicts (students in the same level likely share
#                     the same curriculum and cannot attend two classes at once)
#   - is_lab:         Boolean; lab sections can overlap in the same time slot
#                     for the same level (HC3 exception) because lab groups
#                     alternate weeks or use separate lab rooms
#   - credits:        Number of credit hours (2, 3, or 4), which directly
#                     determines how many 50-minute ClassSlots per week and
#                     whether they must be consecutive
#   - prerequisites:  List of course IDs that must not be scheduled in the
#                     same time slot (HC7), since the same student may need
#                     to take both courses

class Course:
    def __init__(self, course_id, name, level, is_lab=False,
                 dept_name="", credits=3, prerequisites=None):
        """
        Parameters
        ----------
        course_id : str
            Unique course identifier from the university catalog.
        name : str
            Human-readable course title.
        level : int
            Academic level (1-8). Students within the same level share a
            curriculum, so their courses must not overlap in time.
        is_lab : bool, optional
            True if this is a laboratory section. Lab sections receive
            special handling in HC3 (student group conflict) because
            lab groups may run in parallel.
        dept_name : str, optional
            Name of the department offering this course.
        credits : int, optional
            Credit hours (2, 3, or 4). Determines the number and arrangement
            of weekly 50-minute time slots. Default is 3.
        prerequisites : list of str, optional
            Course IDs that are prerequisites. If a prerequisite course is
            scheduled in the same day+time slot, HC7 triggers a penalty
            (since a student taking both courses cannot attend both).
        """
        self._id            = course_id
        self._name          = name
        self._level         = level
        self._is_lab        = is_lab
        self._dept_name     = dept_name
        self._credits       = credits
        self._prerequisites = prerequisites or []

    # --- Accessor methods (getters) ---
    def get_id(self):            return self._id
    def get_name(self):          return self._name
    def get_level(self):         return self._level
    def get_is_lab(self):        return self._is_lab
    def get_dept_name(self):     return self._dept_name
    def get_credits(self):       return self._credits
    def get_prerequisites(self): return self._prerequisites

    def __str__(self):
        """String representation: 'CS101 - Introduction to Programming'."""
        return f"{self._id} - {self._name}"


# =============================================================================
# CLASS: Section -- A Specific Offering of a Course
# =============================================================================
# A Section is the fundamental scheduling unit. While a Course is a catalog
# entry, a Section is an actual class that students enroll in. For example,
# "CS101" might have Section "CS101-M1" (Male section 1) and "CS101-F1"
# (Female section 1).
#
# Each Section knows:
#   - Which Course it belongs to (and inherits level, credits, lab status)
#   - Its gender (Male/Female) for gender-segregation constraints (HC6)
#   - How many students are enrolled (for room capacity check, HC4)
#   - Which instructors are eligible to teach it (the scheduler picks one)
#
# In the GA, each SectionGroup wraps exactly one Section and represents
# that section's complete weekly schedule (all its ClassSlots).

class Section:
    def __init__(self, section_id, course, gender, enrolled, capacity,
                 eligible_instructors):
        """
        Parameters
        ----------
        section_id : str
            Unique section identifier (e.g., "CS101-M1").
        course : Course
            The parent Course object.
        gender : str
            "Male" or "Female" -- determines which campus/building rooms
            are eligible (HC6 gender separation).
        enrolled : int
            Number of students enrolled. Must not exceed the assigned room's
            seating capacity (HC4).
        capacity : int
            Maximum number of students the section can hold.
        eligible_instructors : list of Instructor
            Professors who are qualified and available to teach this section.
            The scheduler randomly picks one during initialization; the GA
            may reassign during mutation.
        """
        self._id                   = section_id
        self._course               = course
        self._gender               = gender
        self._enrolled             = enrolled
        self._capacity             = capacity
        self._eligible_instructors = eligible_instructors

    # --- Accessor methods ---
    def get_id(self):                   return self._id
    def get_course(self):               return self._course
    def get_gender(self):               return self._gender
    def get_enrolled(self):             return self._enrolled
    def get_capacity(self):             return self._capacity
    def get_eligible_instructors(self): return self._eligible_instructors

    # --- Delegate accessors: forward to the parent Course for convenience ---
    def get_level(self):                return self._course.get_level()
    def get_is_lab(self):               return self._course.get_is_lab()
    def get_dept_name(self):            return self._course.get_dept_name()
    def get_credits(self):              return self._course.get_credits()

    def __str__(self):
        return f"{self._id} | {self._course.get_name()} | {self._gender} | enrolled={self._enrolled}"


# =============================================================================
# CLASS: Instructor -- A Professor / Teaching Staff Member
# =============================================================================
# Represents a faculty member who can be assigned to teach sections.
# Each instructor has:
#   - A workload window (min_load to max_load sections)
#   - Teaching hour bounds (min_hours to max_hours per week)
#
# HC8 penalizes instructors assigned fewer sections than their min_load.
# SC3 penalizes extreme workload imbalances across the instructor pool.
#
# The _gender attribute is stored but currently defaults to 'Male'. In IMAMU's
# gender-segregated model, instructor gender could be used to further
# constrain which sections they may teach (e.g., female instructors for
# female sections), though the current implementation relies on the
# eligible_instructors list in Section to handle this.

class Instructor:
    def __init__(self, inst_id, name):
        """
        Parameters
        ----------
        inst_id : str
            Unique instructor identifier (e.g., "INST-001").
        name : str
            Full name of the instructor.
        """
        self._id         = inst_id
        self._name       = name
        self._gender     = 'Male'     # Default gender; can be overridden after construction
        self._department = ''         # Department affiliation (informational)
        self._max_load   = 5          # Maximum number of sections this instructor can teach
        self._min_load   = 0          # Minimum number of sections (HC8 threshold)
        self._min_hours  = 12         # Minimum required teaching hours per week
        self._max_hours  = 18         # Maximum allowed teaching hours per week

    # --- Accessor methods ---
    def get_id(self):       return self._id
    def get_name(self):     return self._name
    def get_max_load(self): return self._max_load
    def get_min_load(self): return self._min_load

    def __str__(self):
        """String representation: returns the instructor's name."""
        return self._name


# =============================================================================
# CLASS: Room -- A Physical Classroom or Lab
# =============================================================================
# Represents a physical room on one of IMAMU's campuses. Each room has:
#   - A room number (unique identifier like "M-201")
#   - A seating capacity (used by HC4 to ensure enrolled <= capacity)
#   - A gender assignment ("Male" or "Female") reflecting which campus
#     building the room is in. IMAMU operates gender-segregated facilities,
#     so a female section must only be assigned to a female-campus room and
#     vice versa (HC6).

class Room:
    def __init__(self, number, seating_capacity, gender=''):
        """
        Parameters
        ----------
        number : str
            Room identifier (e.g., "M-201", "F-Lab-3").
        seating_capacity : int
            Maximum number of students the room can seat.
        gender : str, optional
            "Male" or "Female" indicating which campus the room belongs to.
            Empty string means the room is unassigned or shared (fallback).
        """
        self._number           = number
        self._seating_capacity = seating_capacity
        self._gender           = gender

    # --- Accessor methods ---
    def get_number(self):          return self._number
    def get_seatingCapacity(self): return self._seating_capacity
    def get_gender(self):          return self._gender


# =============================================================================
# CLASS: MeetingTime -- A Named Time Slot
# =============================================================================
# Wraps a time-slot label string like "08:25-09:15". Each label represents a
# 50-minute lecture period. The university's daily schedule is a sequence of
# these slots, and the SLOT_ORDER dictionary maps each label to its
# chronological index (0 = first period, 1 = second, etc.).
#
# The scheduler does not store start/end times as datetime objects; it works
# entirely with label strings. The CONSECUTIVE_PAIRS list precomputes which
# pairs of labels are physically back-to-back, enabling the creation of
# double-period blocks for 3- and 4-credit courses.

class MeetingTime:
    def __init__(self, time):
        """
        Parameters
        ----------
        time : str
            Time-slot label, e.g. "08:25-09:15".
        """
        self._time = time

    def get_time(self):
        """Returns the time-slot label string."""
        return self._time


# =============================================================================
# CLASS: ClassSlot -- A Single 50-Minute Assignment
# =============================================================================
# A ClassSlot represents one atomic scheduling decision: a specific Section
# meeting in a specific Room with a specific Instructor on a specific Day at
# a specific MeetingTime. This is the lowest-level building block.
#
# A 3-credit course, for example, generates 3 ClassSlots (grouped into a
# SectionGroup): 2 consecutive slots on day 1 + 1 slot on day 2.
#
# During initialization, all four assignment fields (instructor, room, day,
# meetingTime) are set. The GA's crossover and mutation operators work at the
# SectionGroup level, swapping or replacing entire groups of ClassSlots rather
# than individual ones. This preserves the structural integrity of multi-slot
# patterns (e.g., keeping a double-period truly consecutive).

class ClassSlot:
    def __init__(self, slot_id, dept, section):
        """
        Parameters
        ----------
        slot_id : int
            Unique numeric identifier for this slot within the schedule.
        dept : Department
            The academic department this slot belongs to.
        section : Section
            The section being scheduled in this slot.
        """
        self._id          = slot_id
        self._dept        = dept
        self._section     = section
        self._instructor  = None    # Assigned Instructor object
        self._meetingTime = None    # Assigned MeetingTime object
        self._day         = None    # Assigned Day object
        self._room        = None    # Assigned Room object

    # --- Getter methods ---
    def get_id(self):          return self._id
    def get_dept(self):        return self._dept
    def get_section(self):     return self._section
    def get_course(self):      return self._section   # Alias; returns Section (not Course)
    def get_instructor(self):  return self._instructor
    def get_meetingTime(self): return self._meetingTime
    def get_room(self):        return self._room
    def get_day(self):         return self._day

    # --- Setter methods: used during initialization and by PSO's apply_group_state ---
    def set_instructor(self, i):   self._instructor  = i
    def set_meetingTime(self, mt): self._meetingTime = mt
    def set_day(self, d):          self._day         = d
    def set_room(self, r):         self._room        = r

    def __str__(self):
        """CSV-like representation for debugging: dept, section, room, instructor, time, day."""
        return (f"{self._dept.get_name()},"
                f"{self._section.get_id()},"
                f"{self._room.get_number() if self._room else 'None'},"
                f"{self._instructor.get_id() if self._instructor else 'None'},"
                f"{self._meetingTime.get_time() if self._meetingTime else 'None'},"
                f"{self._day.get_day() if self._day else 'None'}")

# Backward-compatibility alias: some older code may reference "Class" instead
# of "ClassSlot". This alias ensures both names resolve to the same type.
Class = ClassSlot


# =============================================================================
# CLASS: SectionGroup -- The GA's Genetic Unit (Gene)
# =============================================================================
# In genetic algorithm terminology:
#   - A "chromosome" is a complete Schedule (a full timetable for the university)
#   - A "gene" is a SectionGroup (one section's complete weekly slot assignment)
#
# A SectionGroup bundles all ClassSlots that belong to a single Section's
# weekly schedule. The number of slots depends on the course's credit hours:
#
#   credits=2: 2 ClassSlots on 2 different days (1 slot per day)
#              Example: SUN slot 3, TUE slot 5
#
#   credits=3: 3 ClassSlots across 2 days:
#              - Day 1: 2 consecutive (back-to-back) slots (a "double period")
#              - Day 2: 1 single slot
#              Example: SUN slots 2+3, WED slot 1
#
#   credits=4: 4 ClassSlots across 2 days:
#              - Day 1: 2 consecutive slots
#              - Day 2: 2 consecutive slots
#              Example: MON slots 1+2, THU slots 1+2
#
# By treating SectionGroup as the atomic unit of genetic operations (crossover
# and mutation), the algorithm preserves the structural relationships between
# a section's slots. For example, a 3-credit course's double-period on day 1
# always stays consecutive, because the entire group is swapped as one piece.
# This is far more effective than mutating individual ClassSlots independently,
# which would constantly break consecutive-slot constraints.
#
# For PSO, the SectionGroup is also the unit of "position" serialization.
# extract_group_state() captures each group's room/instructor/time/day as a
# dictionary, and apply_group_state() writes it back. This lets PSO treat
# the schedule as a vector of discrete group-states that particles can
# interpolate between.

class SectionGroup:
    """
    Represents one complete section with all its weekly slots.
    credits=2 -> 2 slots on the same day back-to-back
    credits=3 -> 2 consecutive slots (day1) + 1 slot (day2)
    The GA treats each SectionGroup as a single genetic unit.
    """
    def __init__(self, group_id, dept, section):
        """
        Parameters
        ----------
        group_id : int
            Unique identifier for this group within the schedule.
        dept : Department
            The academic department.
        section : Section
            The section whose weekly meetings this group represents.
        """
        self._id      = group_id
        self._dept    = dept
        self._section = section
        self._slots   = []   # List of ClassSlot objects comprising this section's weekly schedule

    # --- Accessor methods ---
    def get_id(self):      return self._id
    def get_dept(self):    return self._dept
    def get_section(self): return self._section
    def get_slots(self):   return self._slots

    def add_slot(self, slot):
        """Appends a ClassSlot to this group's weekly slot list."""
        self._slots.append(slot)

    def __str__(self):
        return f"Group({self._section.get_id()}) slots={len(self._slots)}"


# =============================================================================
# CLASS: Data -- University Data Container
# =============================================================================
# Aggregates all input data needed by the scheduling engine:
#   - _rooms:        All physical rooms (both campuses)
#   - _meetingTimes: All available time slots in a day
#   - _instructors:  All teaching staff
#   - _days:         All teaching days (SUN-THU)
#   - _courses:      All courses in the catalog
#   - _sections:     All sections to be scheduled
#   - _depts:        All departments (each grouping its sections)
#
# This object is instantiated once by the runner script (GA_runner.py or
# pso_runner.py), populated from the database, and then stored in the
# module-level variable `data` (see bottom of this file). All Schedule
# objects reference this shared Data instance to access rooms, instructors,
# time slots, and days during initialization and fitness evaluation.

class Data:
    def __init__(self):
        self._rooms        = []   # List of Room objects
        self._meetingTimes = []   # List of MeetingTime objects
        self._instructors  = []   # List of Instructor objects
        self._days         = []   # List of Day objects
        self._courses      = []   # List of Course objects
        self._sections     = []   # List of Section objects
        self._depts        = []   # List of Department objects

    # --- Accessor methods ---
    def get_rooms(self):           return self._rooms
    def get_instructors(self):     return self._instructors
    def get_courses(self):         return self._courses
    def get_sections(self):        return self._sections
    def get_depts(self):           return self._depts
    def get_meetingTimes(self):    return self._meetingTimes
    def get_days(self):            return self._days
    def get_numberOfClasses(self): return len(self._sections)

    def get_male_rooms(self):
        """Returns only rooms assigned to the male campus."""
        return [r for r in self._rooms if r.get_gender() == 'Male']

    def get_female_rooms(self):
        """Returns only rooms assigned to the female campus."""
        return [r for r in self._rooms if r.get_gender() == 'Female']


# =============================================================================
# CLASS: Department -- Groups Sections by Academic Department
# =============================================================================
# Represents an academic department (e.g., "Computer Science", "Mathematics").
# Each department contains a list of Sections that need to be scheduled.
# The scheduler iterates over departments and their sections during
# initialization to create SectionGroups.
#
# Note: get_courses() is an alias for get_sections() for backward
# compatibility. Despite the name, it returns Section objects, not Course
# objects.

class Department:
    def __init__(self, name, sections):
        """
        Parameters
        ----------
        name : str
            Department name (e.g., "Computer Science").
        sections : list of Section
            All sections belonging to this department that need scheduling.
        """
        self._name     = name
        self._sections = sections

    def get_name(self):     return self._name
    def get_sections(self): return self._sections
    def get_courses(self):  return self._sections   # Alias for backward compatibility


# =============================================================================
# CLASS: Schedule -- A Complete Timetable (GA Chromosome / PSO Particle)
# =============================================================================
# A Schedule represents one complete candidate timetable for the entire
# university. In GA terminology, this is a "chromosome" -- a full solution
# to the scheduling problem. In PSO terminology, this is a "particle" -- a
# point in the solution space.
#
# Internally, a Schedule is a list of SectionGroup objects (the "genes").
# Each SectionGroup contains all the ClassSlots for one section's weekly
# meetings. The total number of SectionGroups equals the total number of
# sections across all departments.
#
# Key operations:
#   - initialize():        Creates a random but structurally valid schedule
#                           (correct number of slots per credit hour, correct
#                           consecutive pairing, gender-appropriate rooms).
#   - calculate_fitness():  Evaluates the schedule against all hard and soft
#                           constraints, returning a fitness score in (0, 1].
#   - extract_group_state() / apply_group_state(): PSO-specific methods that
#                           serialize and deserialize the schedule's state
#                           for particle position manipulation.
#
# The fitness is cached (_isFitnessChanged flag) and only recalculated when
# the schedule's structure has been modified (via get_classes(), get_groups(),
# or flag_state_changed()).

class Schedule:
    def __init__(self):
        """
        Creates an empty schedule. The `data` variable must be set at module
        level before instantiation (see bottom of file).
        """
        self._data             = data    # Reference to the shared Data container
        self._groups           = []      # List of SectionGroup objects (the "genes")
        self._numbOfConflicts  = 0       # Accumulated constraint violation penalty
        self._fitness          = -1      # Cached fitness value; -1 = not yet calculated
        self._isFitnessChanged = True    # Dirty flag: True means fitness must be recalculated
        self._slot_counter     = 0       # Auto-incrementing ID for ClassSlot/SectionGroup creation

    @property
    def _classes(self):
        """
        Backward compatibility property: flattens all SectionGroups into a
        single list of ClassSlot objects. Older code that works with a flat
        list of "classes" (slots) can use this transparently.

        Returns
        -------
        list of ClassSlot
            Every individual time-slot assignment across all section groups.
        """
        return [slot for g in self._groups for slot in g.get_slots()]

    def get_classes(self):
        """
        Returns all ClassSlots (flattened from groups) and marks fitness as
        stale. The fitness is marked stale because callers may modify the
        returned slots.
        """
        self._isFitnessChanged = True
        return self._classes

    def get_groups(self):
        """
        Returns the list of SectionGroup objects and marks fitness as stale.
        """
        self._isFitnessChanged = True
        return self._groups

    def get_numbOfConflicts(self):
        """Returns the total weighted constraint violation count from the last fitness evaluation."""
        return self._numbOfConflicts

    def get_fitness(self):
        """
        Returns the schedule's fitness score, recalculating if the schedule
        has been modified since the last evaluation.

        The fitness is lazily computed: it is only recalculated when the
        _isFitnessChanged flag is True. This avoids redundant evaluations
        when fitness is queried multiple times without intervening changes.

        Returns
        -------
        float
            Fitness in the range (0, 1]. 1.0 = perfect (zero violations).
        """
        if self._isFitnessChanged:
            self._fitness          = self.calculate_fitness()
            self._isFitnessChanged = False
        return self._fitness

    # -------------------------------------------------------------------------
    # PSO HELPER METHODS
    # -------------------------------------------------------------------------
    # Particle Swarm Optimization operates by moving particles (schedules)
    # through a solution space. Unlike GA which uses crossover/mutation on
    # chromosomes, PSO needs to:
    #   1. Extract a particle's current "position" (schedule state)
    #   2. Compute "velocity" (difference between states)
    #   3. Apply a new "position" (updated state)
    #
    # Since our solution space is discrete (rooms, instructors, time slots,
    # days are categorical, not continuous), PSO uses group-level state
    # dictionaries as the position representation. The pso_runner.py file
    # implements the velocity/position update logic using these helpers.

    def flag_state_changed(self):
        """
        Marks the schedule's fitness as stale, forcing recalculation on the
        next get_fitness() call. This MUST be called after any external
        modification to the schedule's slots (e.g., after PSO applies a
        new position vector via apply_group_state).
        """
        self._isFitnessChanged = True

    def extract_group_state(self):
        """
        Serializes the schedule's current state into a nested list structure
        suitable for PSO position representation.

        Returns
        -------
        list of list of dict
            Outer list: one entry per SectionGroup (indexed by group position).
            Inner list: one entry per ClassSlot within that group.
            Each dict has keys: 'room', 'instructor', 'time', 'day', holding
            the actual Room, Instructor, MeetingTime, Day objects respectively.

        Example structure for a 3-credit section:
            [
                [  # Group 0 (some section)
                    {'room': <Room>, 'instructor': <Instructor>, 'time': <MeetingTime>, 'day': <Day>},
                    {'room': <Room>, 'instructor': <Instructor>, 'time': <MeetingTime>, 'day': <Day>},
                    {'room': <Room>, 'instructor': <Instructor>, 'time': <MeetingTime>, 'day': <Day>}
                ],
                [  # Group 1 (another section)
                    ...
                ],
                ...
            ]
        """
        state = []
        for group in self._groups:
            group_state = []
            for slot in group.get_slots():
                group_state.append({
                    'room': slot.get_room(),
                    'instructor': slot.get_instructor(),
                    'time': slot.get_meetingTime(),
                    'day': slot.get_day()
                })
            state.append(group_state)
        return state

    def apply_group_state(self, state):
        """
        Overwrites the schedule's slot assignments with a previously extracted
        state. This is the PSO "position update" operation: after the PSO
        algorithm computes a new position for a particle, it calls this
        method to apply that position to the schedule.

        Parameters
        ----------
        state : list of list of dict
            The state structure as returned by extract_group_state().
            Must have the same shape (number of groups, slots per group)
            as the current schedule.

        Side Effects
        ------------
        - Modifies all ClassSlot objects in-place
        - Flags fitness as stale (triggers recalculation on next get_fitness)
        """
        for g_idx, group in enumerate(self._groups):
            for s_idx, slot in enumerate(group.get_slots()):
                slot.set_room(state[g_idx][s_idx]['room'])
                slot.set_instructor(state[g_idx][s_idx]['instructor'])
                slot.set_meetingTime(state[g_idx][s_idx]['time'])
                slot.set_day(state[g_idx][s_idx]['day'])
        self.flag_state_changed()

    # -------------------------------------------------------------------------
    # SCHEDULE INITIALIZATION HELPERS
    # -------------------------------------------------------------------------

    def _pick_consecutive_pair(self, allowed_times):
        """
        Selects a random valid pair of back-to-back (consecutive) time slots
        from the given list of allowed meeting times.

        This is used when building SectionGroups for 3-credit and 4-credit
        courses that require "double periods" (two 50-minute slots in a row
        with only a short break between them).

        The method filters the global CONSECUTIVE_PAIRS list to find pairs
        where both slots are in the allowed_times set (i.e., neither slot
        falls during prayer time).

        Parameters
        ----------
        allowed_times : list of MeetingTime
            The set of non-prayer time slots available for scheduling.

        Returns
        -------
        tuple of (MeetingTime, MeetingTime)
            Two MeetingTime objects that are chronologically adjacent.
            If no valid consecutive pair exists (e.g., all pairs include a
            prayer slot), falls back to returning two independently random
            slots -- this is a degraded fallback that will likely incur a
            soft constraint penalty.
        """
        # Build a set of available time labels for fast membership testing
        valid_pairs = [(a, b) for (a, b) in CONSECUTIVE_PAIRS
                       if a in {t.get_time() for t in allowed_times}
                       and b in {t.get_time() for t in allowed_times}]
        if not valid_pairs:
            # Fallback: no valid consecutive pair available. Pick two random
            # slots independently. This will likely violate the consecutive-slot
            # pattern but avoids crashing. The fitness function will penalize
            # the resulting gap.
            t1 = allowed_times[rnd.randrange(len(allowed_times))]
            t2 = allowed_times[rnd.randrange(len(allowed_times))]
            return t1, t2

        # Pick a random valid consecutive pair and map labels back to MeetingTime objects
        pair = valid_pairs[rnd.randrange(len(valid_pairs))]
        time_map = {t.get_time(): t for t in allowed_times}
        return time_map[pair[0]], time_map[pair[1]]

    def _make_group(self, dept, section, instructor, room, day):
        """
        Builds a SectionGroup for the given section based on its credit hours.
        This method encodes IMAMU's credit-to-slot mapping rules:

            2 credits -> 2 single slots on 2 DIFFERENT days
                         (one 50-min lecture per day, twice per week)

            3 credits -> 2 consecutive slots on day1 + 1 single slot on day2
                         (one double-period + one single-period per week)

            4 credits -> 2 consecutive slots on day1 + 2 consecutive on day2
                         (two double-periods per week)

        For each ClassSlot created, the same instructor and room are assigned
        (a section meets in the same room with the same instructor all week).
        Days and times are randomly chosen, respecting consecutiveness rules.

        Parameters
        ----------
        dept : Department
            The department for labeling purposes.
        section : Section
            The section to schedule.
        instructor : Instructor
            The randomly chosen eligible instructor.
        room : Room
            The randomly chosen gender-appropriate room.
        day : Day
            A seed day (used only for the initial random selection framework;
            actual days are randomly re-picked inside this method).

        Returns
        -------
        SectionGroup
            A fully populated group with the correct number of ClassSlots.
        """
        credits       = section.get_credits()
        # Filter out prayer slots -- no class can be scheduled during prayer
        allowed_times = [t for t in data.get_meetingTimes()
                         if t.get_time() not in PRAYER_SLOTS]
        group = SectionGroup(self._slot_counter, dept, section)
        self._slot_counter += 1

        if credits == 2:
            # ------------------------------------------------------------------
            # 2-CREDIT PATTERN: Two separate days, one slot each
            # ------------------------------------------------------------------
            # Pick two distinct days randomly (e.g., SUN and WED)
            day1 = data.get_days()[rnd.randrange(len(data.get_days()))]
            day2 = day1
            while day2 == day1:
                # Keep re-picking until we get a different day
                day2 = data.get_days()[rnd.randrange(len(data.get_days()))]
            # Pick one random time slot for each day
            t1   = allowed_times[rnd.randrange(len(allowed_times))]
            t2   = allowed_times[rnd.randrange(len(allowed_times))]
            # Create the two ClassSlots
            for d, t in [(day1, t1), (day2, t2)]:
                slot = ClassSlot(self._slot_counter, dept, section)
                self._slot_counter += 1
                slot.set_instructor(instructor)
                slot.set_room(room)
                slot.set_day(d)
                slot.set_meetingTime(t)
                group.add_slot(slot)

        elif credits == 3:
            # ------------------------------------------------------------------
            # 3-CREDIT PATTERN: Day1 = double period, Day2 = single period
            # ------------------------------------------------------------------
            # Pick two days (may be the same day -- the algorithm does not
            # enforce distinctness for 3-credit courses)
            day1   = data.get_days()[rnd.randrange(len(data.get_days()))]
            day2   = data.get_days()[rnd.randrange(len(data.get_days()))]
            # Pick a consecutive pair for day1's double-period block
            t_a, t_b = self._pick_consecutive_pair(allowed_times)
            # Pick a single slot for day2
            t_c      = allowed_times[rnd.randrange(len(allowed_times))]
            # Create three ClassSlots: two consecutive on day1, one on day2
            for d, t in [(day1, t_a), (day1, t_b), (day2, t_c)]:
                slot = ClassSlot(self._slot_counter, dept, section)
                self._slot_counter += 1
                slot.set_instructor(instructor)
                slot.set_room(room)
                slot.set_day(d)
                slot.set_meetingTime(t)
                group.add_slot(slot)

        elif credits == 4:
            # ------------------------------------------------------------------
            # 4-CREDIT PATTERN: Day1 = double period, Day2 = double period
            # ------------------------------------------------------------------
            day1     = data.get_days()[rnd.randrange(len(data.get_days()))]
            day2     = data.get_days()[rnd.randrange(len(data.get_days()))]
            # Pick two independent consecutive pairs (one per day)
            t_a, t_b = self._pick_consecutive_pair(allowed_times)
            t_c, t_d = self._pick_consecutive_pair(allowed_times)
            # Create four ClassSlots: two consecutive on each day
            for d, t in [(day1, t_a), (day1, t_b), (day2, t_c), (day2, t_d)]:
                slot = ClassSlot(self._slot_counter, dept, section)
                self._slot_counter += 1
                slot.set_instructor(instructor)
                slot.set_room(room)
                slot.set_day(d)
                slot.set_meetingTime(t)
                group.add_slot(slot)

        return group

    # -------------------------------------------------------------------------
    # SCHEDULE INITIALIZATION
    # -------------------------------------------------------------------------

    def initialize(self):
        """
        Creates a random but structurally valid initial schedule.

        Iterates over every department and every section within each
        department, and for each section:
          1. Selects a gender-appropriate room pool (male or female campus)
          2. Randomly picks a room from that pool
          3. Randomly picks an instructor from the section's eligible list
          4. Randomly picks a day
          5. Calls _make_group() to build the SectionGroup with the correct
             number of slots and consecutive-pair structure

        The result is a complete timetable that satisfies structural
        requirements (correct number of slots per credit hour, consecutive
        pairs where needed) but likely has many constraint violations
        (room conflicts, instructor double-bookings, etc.). The GA/PSO
        optimization process then improves this initial solution over
        many iterations.

        Returns
        -------
        Schedule
            Returns self (fluent interface) so callers can write:
            schedule = Schedule().initialize()
        """
        male_rooms   = data.get_male_rooms()
        female_rooms = data.get_female_rooms()
        allowed_times = [t for t in data.get_meetingTimes()
                         if t.get_time() not in PRAYER_SLOTS]

        for dept in data.get_depts():
            for section in dept.get_sections():
                # Select the room pool matching the section's gender
                gender_rooms = (male_rooms if section.get_gender() == 'Male'
                                else female_rooms)
                if not gender_rooms:
                    # Fallback: if no rooms are tagged for this gender,
                    # use the full room list (shouldn't happen in production
                    # data, but prevents a crash)
                    gender_rooms = data.get_rooms()

                # Randomly assign a room, instructor, and day
                room       = gender_rooms[rnd.randrange(len(gender_rooms))]
                instructors = section.get_eligible_instructors()
                instructor  = instructors[rnd.randrange(len(instructors))]
                day         = data.get_days()[rnd.randrange(len(data.get_days()))]

                # Build the SectionGroup with credit-appropriate slot structure
                group = self._make_group(dept, section, instructor, room, day)
                self._groups.append(group)
        return self

    # -------------------------------------------------------------------------
    # FITNESS EVALUATION (Constraint Checking)
    # -------------------------------------------------------------------------

    def calculate_fitness(self):
        """
        Evaluates this schedule against all hard and soft constraints,
        computing a fitness score.

        The fitness function is the heart of the optimization. It defines
        what makes a "good" schedule by penalizing constraint violations.

        HARD CONSTRAINTS (weight = 1.0 each):
        These are absolute rules that a valid schedule MUST satisfy.
        A schedule with any hard constraint violation is considered invalid.

          HC1 - Instructor Double-Booking:
                An instructor cannot teach two sections at the same day+time.
                Checked pairwise: if classes i and j share the same time slot
                AND the same instructor, add HARD_WEIGHT.

          HC2 - Room Double-Booking:
                A room cannot host two sections at the same day+time.
                Checked pairwise: if classes i and j share the same time slot
                AND the same room, add HARD_WEIGHT.

          HC3 - Student Group Conflict:
                Students in the same level and gender cannot have two required
                courses scheduled at the same time (they need to attend both).
                Exception: lab sections may overlap because lab groups often
                alternate or run independently.
                Checked pairwise: if two classes share the same slot, same
                level, same gender, different courses, and neither is a lab,
                add HARD_WEIGHT.

          HC4 - Room Capacity:
                The assigned room must have enough seats for all enrolled
                students. If room.capacity < section.enrolled, add HARD_WEIGHT.

          HC5 - Prayer Time:
                No class may be scheduled during prayer times. If a slot's
                time label is in PRAYER_SLOTS, add HARD_WEIGHT.

          HC6 - Gender Separation:
                A female section must not be in a male-campus room, and vice
                versa. This enforces IMAMU's gender-segregated building policy.

          HC7 - Prerequisite Conflict:
                If course A is a prerequisite of course B, they must not be
                scheduled at the same day+time. A student taking course B
                may also need to attend course A (or the prerequisite's
                assessment), so they cannot overlap.

          HC8 - Instructor Minimum Load:
                Each instructor must be assigned at least their minimum number
                of sections (min_load). If an instructor's actual load is
                below this threshold, add HARD_WEIGHT.

        SOFT CONSTRAINTS (weight = 0.1 each):
        These are quality preferences. Violating them degrades schedule quality
        but does not make it invalid.

          SC1 - Avoid Last Slot:
                Instructors and students prefer not to have classes in the
                last time slot of the day. Each class in the final slot
                incurs a small penalty.

          SC2 - Minimize Instructor Gaps:
                If an instructor has two classes on the same day with a gap
                of more than 1 slot between them, add SOFT_WEIGHT. Idle
                gaps waste the instructor's time on campus.

          SC3 - Workload Balance:
                If any instructor's load exceeds twice the average load
                across all instructors, add SOFT_WEIGHT. This encourages
                a more equitable distribution of teaching assignments.

          SC4 - Consecutive Same-Course Sections:
                If multiple sections of the same course are scheduled on the
                same day, they should be back-to-back (consecutive slot
                indices). Gaps between them add SOFT_WEIGHT proportional to
                the gap size. This is convenient for instructors who teach
                multiple sections of the same course and for shared TA
                resources.

        FITNESS FORMULA:
            fitness = 1 / (1 + total_penalty)

        Where total_penalty is the sum of all weighted violations.
        - Perfect schedule (0 violations): fitness = 1 / (1 + 0) = 1.0
        - 1 hard violation:                fitness = 1 / (1 + 1.0) = 0.5
        - 1 soft violation:                fitness = 1 / (1 + 0.1) ~ 0.909
        - 10 hard violations:              fitness = 1 / (1 + 10.0) ~ 0.091

        The higher the fitness, the better the schedule. The GA/PSO algorithms
        maximize this value.

        Returns
        -------
        float
            Fitness score in the range (0, 1]. Higher is better.
        """
        self._numbOfConflicts = 0.0
        classes = self._classes  # Flatten all groups into a single slot list

        # --- Pre-computation: Instructor workload map ---
        # Count how many slots each instructor is assigned across the schedule.
        # Used by HC8 (min load check) and SC3 (workload balance).
        instructor_workload = {}
        for cls in classes:
            iid = cls.get_instructor().get_id()
            instructor_workload[iid] = instructor_workload.get(iid, 0) + 1

        # --- Pre-computation: Course schedule map ---
        # For each course ID, collect all (day, time) tuples where it's scheduled.
        # Used by HC7 (prerequisite conflict): if a prerequisite course shares
        # the same (day, time) as the current course, it's a conflict.
        course_schedule_map = {}
        for cls in classes:
            cid = cls.get_section().get_course().get_id()
            key = (cls.get_day().get_day(), cls.get_meetingTime().get_time())
            course_schedule_map.setdefault(cid, []).append(key)

        # Determine the index of the last time slot of the day (for SC1)
        last_slot_idx = max(SLOT_ORDER.values()) if SLOT_ORDER else 99

        # ===================================================================
        # MAIN PAIRWISE CONSTRAINT LOOP
        # ===================================================================
        # For each class slot, check per-slot constraints (HC4, HC5, HC6, HC7,
        # SC1), then check pairwise constraints against all subsequent slots
        # (HC1, HC2, HC3, SC2). The inner loop starts at i+1 to avoid
        # double-counting pairs.

        for i in range(len(classes)):
            ci    = classes[i]
            sec_i = ci.get_section()

            # --- HC4: Room Capacity Check ---
            # The room must have enough seats for all enrolled students.
            if ci.get_room().get_seatingCapacity() < sec_i.get_enrolled():
                self._numbOfConflicts += HARD_WEIGHT

            # --- HC5: Prayer Time Check ---
            # No class should be scheduled during prayer time slots.
            if ci.get_meetingTime().get_time() in PRAYER_SLOTS:
                self._numbOfConflicts += HARD_WEIGHT

            # --- HC6: Gender Separation Check ---
            # Female sections must not be in male-campus rooms and vice versa.
            room_num = ci.get_room().get_number()
            if sec_i.get_gender() == 'Female' and room_num in MALE_ROOMS:
                self._numbOfConflicts += HARD_WEIGHT
            if sec_i.get_gender() == 'Male' and room_num in FEMALE_ROOMS:
                self._numbOfConflicts += HARD_WEIGHT

            # --- HC7: Prerequisite Conflict Check ---
            # If this course has prerequisites, ensure none of those
            # prerequisite courses are scheduled at the exact same (day, time).
            prereqs = sec_i.get_course().get_prerequisites()
            if prereqs:
                my_key = (ci.get_day().get_day(), ci.get_meetingTime().get_time())
                for prereq_id in prereqs:
                    if prereq_id in course_schedule_map:
                        if my_key in course_schedule_map[prereq_id]:
                            self._numbOfConflicts += HARD_WEIGHT

            # --- SC1: Avoid Last Time Slot ---
            # Classes scheduled in the day's final slot get a soft penalty.
            if SLOT_ORDER.get(ci.get_meetingTime().get_time(), 0) == last_slot_idx:
                self._numbOfConflicts += SOFT_WEIGHT

            # --- Pairwise checks against all subsequent class slots ---
            for j in range(i + 1, len(classes)):
                cj    = classes[j]
                sec_j = cj.get_section()

                # Determine if classes i and j occupy the exact same time slot
                # (same day AND same meeting time)
                same_slot = (ci.get_meetingTime() == cj.get_meetingTime() and
                             ci.get_day()         == cj.get_day())

                if same_slot:
                    # --- HC1: Instructor Double-Booking ---
                    # Same instructor cannot teach two classes simultaneously.
                    if ci.get_instructor() == cj.get_instructor():
                        self._numbOfConflicts += HARD_WEIGHT

                    # --- HC2: Room Double-Booking ---
                    # Same room cannot host two classes simultaneously.
                    if ci.get_room() == cj.get_room():
                        self._numbOfConflicts += HARD_WEIGHT

                    # --- HC3: Student Group Conflict ---
                    # Two non-lab courses at the same level and gender cannot
                    # overlap, because the same students need to attend both.
                    # Lab sections are exempt because lab groups typically
                    # alternate or can run concurrently.
                    same_level  = sec_i.get_level()  == sec_j.get_level()
                    same_gender = sec_i.get_gender() == sec_j.get_gender()
                    same_course = sec_i.get_course().get_id() == sec_j.get_course().get_id()
                    both_labs   = sec_i.get_is_lab() and sec_j.get_is_lab()
                    if same_level and same_gender and not same_course and not both_labs:
                        self._numbOfConflicts += HARD_WEIGHT

                # --- SC2: Minimize Instructor Gaps ---
                # If two classes on the same day share the same instructor,
                # check if there's a gap > 1 slot between them. Large gaps
                # mean the instructor has idle time on campus.
                if (ci.get_day() == cj.get_day() and
                        ci.get_instructor() == cj.get_instructor()):
                    si = SLOT_ORDER.get(ci.get_meetingTime().get_time(), -1)
                    sj = SLOT_ORDER.get(cj.get_meetingTime().get_time(), -1)
                    if si != -1 and sj != -1 and abs(si - sj) > 1:
                        self._numbOfConflicts += SOFT_WEIGHT

        # --- HC8: Instructor Minimum Load ---
        # Each instructor must teach at least their minimum number of sections.
        # This prevents the situation where an instructor is paid but assigned
        # no (or too few) classes.
        for inst in data.get_instructors():
            load = instructor_workload.get(inst.get_id(), 0)
            if load < inst.get_min_load():
                self._numbOfConflicts += HARD_WEIGHT

        # --- SC3: Workload Balance ---
        # Penalize instructors whose load is more than double the average.
        # This soft constraint encourages fair distribution of teaching across
        # the faculty.
        if len(instructor_workload) > 1:
            avg = sum(instructor_workload.values()) / len(instructor_workload)
            for load in instructor_workload.values():
                if load > avg * 2:
                    self._numbOfConflicts += SOFT_WEIGHT

        # --- SC4: Consecutive Same-Course Sections ---
        # If multiple sections of the same course land on the same day, they
        # should ideally be back-to-back (adjacent slot indices). This is
        # convenient for instructors teaching sequential sections and for
        # shared resources (TAs, projectors, etc.).
        #
        # Algorithm: for each course, group its slots by day. On each day,
        # sort the slot indices and measure gaps between consecutive entries.
        # Gaps > 1 are penalized proportionally to their size.
        course_slots = {}
        for cls in classes:
            cid = cls.get_section().get_course().get_id()
            day = cls.get_day().get_day()
            idx = SLOT_ORDER.get(cls.get_meetingTime().get_time(), -1)
            course_slots.setdefault(cid, []).append((day, idx))

        for cid, day_slots in course_slots.items():
            # Group slot indices by day
            by_day = {}
            for (day, idx) in day_slots:
                by_day.setdefault(day, []).append(idx)
            for day, idxs in by_day.items():
                idxs.sort()
                # Check consecutive pairs of slot indices on this day
                for k in range(len(idxs) - 1):
                    gap = idxs[k+1] - idxs[k]
                    if gap > 1:
                        # Penalty proportional to gap size: a 3-slot gap is
                        # worse than a 2-slot gap
                        self._numbOfConflicts += SOFT_WEIGHT * gap

        # ===================================================================
        # COMPUTE FINAL FITNESS
        # ===================================================================
        # fitness = 1 / (1 + total_penalty)
        # This maps the penalty range [0, +inf) to the fitness range (0, 1].
        # Zero penalty -> fitness 1.0 (perfect schedule).
        return 1.0 / (1.0 + self._numbOfConflicts)

    def __str__(self):
        """String representation: comma-separated list of SectionGroup summaries."""
        return ", ".join([str(g) for g in self._groups])


# =============================================================================
# CLASS: Population -- Collection of Candidate Schedules
# =============================================================================
# In GA terminology, a Population is the set of chromosomes (schedules) that
# exists at any given generation. The GA evolves this population by:
#   1. Selecting fit individuals (tournament selection)
#   2. Recombining them (crossover)
#   3. Introducing random changes (mutation)
#   4. Preserving the best (elitism)
#
# The Population constructor creates `size` randomly initialized schedules.
# Passing size=0 creates an empty population (used internally by the GA
# when building crossover/tournament populations incrementally).

class Population:
    def __init__(self, size):
        """
        Parameters
        ----------
        size : int
            Number of random schedules to generate. Pass 0 for an empty
            population that will be filled manually (e.g., during crossover).
        """
        self._size      = size
        self._schedules = []
        for _ in range(size):
            # Each schedule is randomly initialized (random room, instructor,
            # day, time assignments for every section)
            self._schedules.append(Schedule().initialize())

    def get_schedules(self):
        """Returns the list of Schedule objects in this population."""
        return self._schedules


# =============================================================================
# CLASS: GeneticAlgorithm -- Evolutionary Optimization Engine
# =============================================================================
# Implements a steady-state Genetic Algorithm that operates on SectionGroups
# (genes) within Schedule objects (chromosomes).
#
# GA WORKFLOW (one generation):
# ============================================================================
#
#   [Current Population]
#         |
#         v
#   1. ELITISM: Copy the top NUMB_OF_ELITE_SCHEDULES schedules directly
#      to the next generation (they bypass crossover and mutation).
#
#   2. SELECTION + CROSSOVER: Fill remaining slots by:
#      a) Running two independent tournament selections to pick two parents
#      b) Performing uniform crossover to create one child
#      c) Repeat until the new population reaches POPULATION_SIZE
#
#   3. MUTATION: For every non-elite schedule in the new population,
#      iterate over each SectionGroup and, with probability MUTATION_RATE,
#      replace it with a completely new random SectionGroup.
#
#         |
#         v
#   [Next Generation Population]
#
# ============================================================================
#
# KEY CONCEPTS:
#
#   Tournament Selection:
#     Instead of selecting parents proportionally to fitness (roulette wheel),
#     this GA uses tournament selection. A random subset of TOURNAMENT_SELECTION_SIZE
#     individuals is drawn from the population, and the fittest individual in
#     that subset is chosen as a parent. This provides good selection pressure
#     while being simple and efficient. Two independent tournaments produce
#     the two parents for each crossover operation.
#
#   Uniform Crossover (at SectionGroup level):
#     For each gene position (SectionGroup index), the child inherits from
#     parent 1 with probability CROSSOVER_RATE (0.8) or from parent 2 with
#     probability (1 - CROSSOVER_RATE) (0.2). This means approximately 80%
#     of the child's genes come from the tournament winner, combining good
#     building blocks from both parents.
#
#   Mutation (SectionGroup replacement):
#     Each SectionGroup in a non-elite schedule has a MUTATION_RATE (0.15)
#     chance of being replaced with an entirely new random SectionGroup.
#     A fresh Schedule().initialize() is created and its corresponding
#     SectionGroup is copied over. This introduces new genetic material
#     and helps the population escape local optima where all individuals
#     converge to similar (but suboptimal) solutions.
#
#   Elitism:
#     The top NUMB_OF_ELITE_SCHEDULES (2) schedules pass unchanged to the
#     next generation. This guarantees monotonic improvement: the best
#     fitness never decreases from one generation to the next.

class GeneticAlgorithm:
    def evolve(self, population):
        """
        Performs one complete generation of evolution:
        crossover (with elitism) followed by mutation.

        Parameters
        ----------
        population : Population
            The current generation's population. Must be sorted by fitness
            in descending order (best first) for elitism to work correctly.

        Returns
        -------
        Population
            The new generation's population after crossover and mutation.
        """
        return self._mutate_population(self._crossover_population(population))

    def _crossover_population(self, pop):
        """
        Creates the next generation via elitism and crossover.

        Steps:
        1. Carry over the top NUMB_OF_ELITE_SCHEDULES individuals unchanged.
        2. Fill remaining slots by selecting two parents via tournament
           selection and producing one child via uniform crossover.

        Parameters
        ----------
        pop : Population
            Current population, assumed sorted by fitness (best first).

        Returns
        -------
        Population
            New population of size POPULATION_SIZE.
        """
        crossover_pop = Population(0)  # Start with empty population

        # Step 1: Elitism - preserve top schedules
        for i in range(NUMB_OF_ELITE_SCHEDULES):
            crossover_pop.get_schedules().append(pop.get_schedules()[i])

        # Step 2: Fill remaining slots via selection + crossover
        while len(crossover_pop.get_schedules()) < POPULATION_SIZE:
            # Select two parents via independent tournaments
            s1 = self._select_tournament(pop).get_schedules()[0]  # Tournament winner 1
            s2 = self._select_tournament(pop).get_schedules()[0]  # Tournament winner 2
            # Create child via uniform crossover
            crossover_pop.get_schedules().append(self._crossover(s1, s2))
        return crossover_pop

    def _mutate_population(self, population):
        """
        Applies mutation to all non-elite schedules in the population.

        Elite schedules (indices 0 to NUMB_OF_ELITE_SCHEDULES-1) are skipped
        to preserve the best solutions found so far. All other schedules
        undergo per-gene mutation with probability MUTATION_RATE.

        Parameters
        ----------
        population : Population
            The population after crossover (elites at the front).

        Returns
        -------
        Population
            The same population object, with non-elite members mutated.
        """
        for i in range(NUMB_OF_ELITE_SCHEDULES, POPULATION_SIZE):
            self._mutate(population.get_schedules()[i])
        return population

    def _crossover(self, s1, s2):
        """
        Uniform crossover at the SectionGroup (gene) level.

        Creates a new child schedule (randomly initialized as a base), then
        for each gene position, replaces the child's gene with:
          - Parent 1's gene with probability CROSSOVER_RATE (0.8)
          - Parent 2's gene with probability (1 - CROSSOVER_RATE) (0.2)

        This is "uniform" crossover because each gene position is independently
        decided (as opposed to single-point or two-point crossover which
        swaps contiguous segments).

        Note: The child is first fully initialized with random genes, then
        overwritten. This means if the parents have fewer groups than the
        child (shouldn't happen in practice), the child retains its random
        genes for those positions.

        Parameters
        ----------
        s1 : Schedule
            Parent 1 (typically the first tournament winner).
        s2 : Schedule
            Parent 2 (typically the second tournament winner).

        Returns
        -------
        Schedule
            The child schedule combining genes from both parents.
        """
        child = Schedule().initialize()
        for i in range(len(child._groups)):
            if i < len(s1._groups) and i < len(s2._groups):
                if rnd.random() < CROSSOVER_RATE:
                    # Inherit this gene (SectionGroup) from parent 1
                    child._groups[i] = s1._groups[i]
                else:
                    # Inherit this gene (SectionGroup) from parent 2
                    child._groups[i] = s2._groups[i]

        return child

    def _mutate(self, schedule):
        """
        Mutation operator: randomly replaces SectionGroups with new ones.

        For each SectionGroup (gene) in the schedule, a random number is
        drawn. If it falls below MUTATION_RATE (0.15), that gene is replaced
        with the corresponding gene from a freshly generated random schedule.
        This effectively gives that section a completely new random room,
        instructor, day, and time assignment.

        Mutation is essential for maintaining diversity in the population.
        Without it, the GA can converge prematurely to a local optimum
        where all individuals look alike and further improvement stalls.

        Parameters
        ----------
        schedule : Schedule
            The schedule to mutate (modified in place).

        Returns
        -------
        Schedule
            The same schedule object (for chaining convenience).
        """
        # Create a fresh random schedule to serve as the mutation source
        random_schedule = Schedule().initialize()
        for i in range(len(schedule._groups)):
            if MUTATION_RATE > rnd.random() and i < len(random_schedule._groups):
                # Replace this gene with a fresh random assignment
                schedule._groups[i] = random_schedule._groups[i]
        return schedule

    def _select_tournament(self, pop):
        """
        Tournament selection: picks the best individual from a random subset.

        Algorithm:
        1. Randomly sample TOURNAMENT_SELECTION_SIZE (8) individuals from
           the population (with replacement).
        2. Sort them by fitness in descending order.
        3. Return the tournament population (the caller takes index [0],
           the fittest).

        Tournament selection has several advantages:
        - Adjustable selection pressure (larger tournament = stronger pressure)
        - Does not require fitness scaling or ranking of the full population
        - Simple to implement and computationally efficient
        - Works well even when fitness values are close together

        Parameters
        ----------
        pop : Population
            The full population to sample from.

        Returns
        -------
        Population
            A small population of TOURNAMENT_SELECTION_SIZE individuals,
            sorted by fitness (best first). The caller typically takes [0].
        """
        tournament = Population(0)  # Empty tournament bracket
        for _ in range(TOURNAMENT_SELECTION_SIZE):
            # Randomly pick one individual from the full population
            tournament.get_schedules().append(
                pop.get_schedules()[rnd.randrange(0, POPULATION_SIZE)])
        # Sort by fitness descending so the winner is at index 0
        tournament.get_schedules().sort(key=lambda x: x.get_fitness(), reverse=True)
        return tournament


# =============================================================================
# CLASS: DisplayMgr -- Schedule Pretty-Printer
# =============================================================================
# Provides methods to render a Schedule as a formatted ASCII table using the
# prettytable library. This is primarily used for debugging and console output
# during development or when running the scheduler from the command line.
#
# Two display modes are available:
#   - print_schedule_as_table():  Flat view -- one row per ClassSlot
#   - print_groups_as_table():    Grouped view -- slots grouped by SectionGroup,
#                                 with section info shown only on the first row
#                                 of each group for readability

class DisplayMgr:
    # Column headers for the flat schedule table
    _HEADERS = [
        'Section ID', 'Course', 'Dept', 'Level', 'Gender',
        'Enrolled', 'Credits', 'Is Lab',
        'Room (Capacity)', 'Instructor', 'Time', 'Day'
    ]

    def _build_row(self, cls):
        """
        Builds a single table row from a ClassSlot object.

        Parameters
        ----------
        cls : ClassSlot
            The time-slot assignment to display.

        Returns
        -------
        list
            A list of display values matching the _HEADERS columns.
        """
        sec = cls.get_section()
        return [
            sec.get_id(),
            sec.get_course().get_name(),
            sec.get_dept_name(),
            f"Level {sec.get_level()}",
            sec.get_gender(),
            sec.get_enrolled(),
            sec.get_credits(),
            sec.get_is_lab(),
            f"{cls.get_room().get_number()} ({cls.get_room().get_seatingCapacity()})",
            f"{cls.get_instructor().get_name()} ({cls.get_instructor().get_id()})",
            cls.get_meetingTime().get_time(),
            cls.get_day().get_day()
        ]

    def print_schedule_as_table(self, schedule):
        """
        Prints the schedule as a flat table with one row per ClassSlot.
        All slots are listed regardless of which SectionGroup they belong to.

        Parameters
        ----------
        schedule : Schedule
            The schedule to display.
        """
        table = prettytable.PrettyTable(self._HEADERS)
        for cls in schedule.get_classes():
            table.add_row(self._build_row(cls))
        print(table)

    def print_groups_as_table(self, schedule):
        """
        Prints the schedule grouped by SectionGroup. Within each group,
        section-level information (ID, course name, credits, gender,
        instructor) is shown only on the first slot's row. Subsequent
        slots show only per-slot data (slot number, room, time, day).
        This makes it easy to see which slots belong together for
        multi-slot courses.

        Parameters
        ----------
        schedule : Schedule
            The schedule to display.
        """
        headers = ['Section ID', 'Course', 'Credits', 'Gender',
                   'Instructor', 'Slot', 'Room', 'Time', 'Day']
        table = prettytable.PrettyTable(headers)
        for group in schedule._groups:
            sec = group.get_section()
            for i, slot in enumerate(group.get_slots()):
                table.add_row([
                    # Show section info only on the first slot row (i==0)
                    sec.get_id() if i == 0 else '',
                    sec.get_course().get_name() if i == 0 else '',
                    sec.get_credits() if i == 0 else '',
                    sec.get_gender() if i == 0 else '',
                    slot.get_instructor().get_name() if i == 0 else '',
                    f"Slot {i+1}",  # Slot number within the group (1-based)
                    f"{slot.get_room().get_number()} ({slot.get_room().get_seatingCapacity()})",
                    slot.get_meetingTime().get_time(),
                    slot.get_day().get_day()
                ])
        print(table)
