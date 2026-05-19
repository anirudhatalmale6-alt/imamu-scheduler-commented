"""
GA_runner.py — Genetic Algorithm Runner for IMAMU Academic Course Scheduling
=============================================================================

Purpose:
    This module implements a multi-objective Genetic Algorithm (GA) system for
    generating optimal university course schedules at Imam Mohammad Ibn Saud
    Islamic University (IMAMU). It replaces the default fitness function from
    `schedule_maker` with a custom multi-objective fitness evaluator that
    respects IMAMU-specific constraints (prayer times, gender separation, etc.).

Architecture Overview:
    1. FITNESS FUNCTION (calculate_fitness_multi):
       - Evaluates each candidate schedule (chromosome) produced by the GA.
       - Uses a weighted penalty approach: hard constraint violations carry
         a weight of 1.0 (must be zero for a valid schedule), while soft
         constraint violations carry 0.1 (preferences that improve quality).
       - The fitness value is computed as 1/(1+conflicts), so a perfect
         schedule with zero conflicts yields fitness = 1.0.
       - Three optimization perspectives (objectives) are supported:
         * University: focuses on room utilization, capacity fit, workload balance.
         * Instructor: focuses on minimizing teaching days, gaps between classes.
         * Student: focuses on minimizing study days, gaps, and late-hour classes.

    2. DATA LOADER (ImamuData class):
       - Parses "university.xml" which contains the complete university model:
         campus buildings, rooms, faculty, students, courses, and time slots.
       - Builds all data structures needed by the GA: rooms (gender-tagged),
         instructors (with teaching-hour constraints), courses (with prerequisites),
         sections (gender-balanced via round-robin interleaving), and departments.

    3. GA RUNNER (_run_ga function):
       - Manages the evolutionary loop: creates initial population, evolves via
         crossover/mutation, and tracks convergence with stagnation detection.
       - Supports multiple independent runs to produce top-N schedules per objective.

    4. ORCHESTRATOR (run_all_schedules):
       - Runs the GA three times (once per objective), collects results, and
         prints formatted timetables using DisplayMgr.

Hard Constraints (HC1-HC7) — must be satisfied for a feasible schedule:
    HC1: No instructor double-booking (same instructor, same timeslot)
    HC2: No room double-booking (same room, same timeslot)
    HC3: No student group conflict (same level+gender, different courses, same slot)
    HC4: Room capacity must accommodate enrolled students
    HC5: No classes during prayer times (Islamic prayer breaks)
    HC6: Gender separation (female sections in female buildings, male in male)
    HC7: Prerequisite courses must not overlap in time (students need both)

Soft Constraints (SC1-SC5) — quality preferences, vary by objective:
    SC1: Room utilization (university) — avoid underused rooms
    SC2: Room-student fit (university) — avoid oversized rooms for small sections
    SC3: Workload balance (university/instructor) — distribute hours fairly
    SC4: Consecutive scheduling (university) — same-course sections back-to-back
    SC5: Instructor hours balance (always active) — enforce minimum load

Dependencies:
    - schedule_maker: Core GA engine providing Population, GeneticAlgorithm,
      Schedule, and all domain model classes (Course, Section, Room, etc.)
    - university.xml: IMAMU-specific data file with full university configuration.

Usage:
    quick_test()   — Fast test with 30 sections, small population (debugging)
    medium_run()   — Moderate run with 60 sections (development/demo)
    full_run()     — Full-scale run with all sections (production)
"""

import xml.etree.ElementTree as ET
import os, time
import schedule_maker

from schedule_maker import (Data, Course, Section, Room, Instructor, MeetingTime,
                             Department, Population, GeneticAlgorithm, DisplayMgr, Day)

# ============================================================================
# OBJECTIVE CONSTANTS
# ============================================================================
# These constants define the three optimization perspectives. Each perspective
# activates a different set of soft constraints in the fitness function, allowing
# the GA to produce schedules tailored to different stakeholder priorities.
# The university can then compare all three and pick the best compromise.
OBJECTIVE_UNIVERSITY = "university"   # Optimize for institutional efficiency
OBJECTIVE_INSTRUCTOR = "instructor"   # Optimize for faculty convenience
OBJECTIVE_STUDENT    = "student"      # Optimize for student experience

# Global variable that controls which soft constraints are active during
# the current GA run. Changed by _run_ga() before each optimization pass.
CURRENT_OBJECTIVE    = OBJECTIVE_UNIVERSITY


# ============================================================================
# MULTI-OBJECTIVE FITNESS FUNCTION
# ============================================================================
def calculate_fitness_multi(schedule_self):
    """
    Custom fitness function that replaces schedule_maker's default.

    This function evaluates a candidate schedule (a chromosome in GA terms)
    by counting weighted constraint violations. The GA tries to MAXIMIZE
    fitness, so fewer violations = higher fitness.

    Weighting strategy:
        - HARD = 1.0: Hard constraints are non-negotiable. Any schedule with
          hard violations > 0 is infeasible. The GA must eliminate all of these.
        - SOFT = 0.1: Soft constraints are 10x less important than hard ones.
          This ensures the GA prioritizes feasibility first, then optimizes
          quality. The 10:1 ratio prevents soft constraints from masking
          hard constraint progress during evolution.

    Returns:
        float: Fitness value in range (0, 1]. A value of 1.0 means zero
        conflicts (perfect schedule). Computed as 1/(1+conflicts).

    Why this formula?
        The reciprocal formula 1/(1+c) maps any non-negative conflict count
        to the (0,1] range. This is important because:
        - It provides diminishing returns: reducing from 10 to 5 conflicts
          gives a bigger fitness jump than reducing from 100 to 95.
        - It ensures the GA always has selection pressure toward improvement.
        - fitness=1.0 is a clean termination condition (zero conflicts).
    """
    HARD = 1.0    # Weight for hard (mandatory) constraint violations
    SOFT = 0.1    # Weight for soft (preference) constraint violations
    conflicts = 0.0    # Accumulator for total weighted violations
    classes   = schedule_self._classes    # List of all class assignments in this schedule

    # ------------------------------------------------------------------
    # PHASE 1: Collect aggregate statistics from all class assignments.
    # These dictionaries are built in a single pass over classes and are
    # reused by multiple constraints below, avoiding redundant iteration.
    # ------------------------------------------------------------------
    instructor_days     = {}    # instructor_id -> set of days they teach on
    instructor_slots    = {}    # instructor_id -> list of (day, slot_index) tuples
    instructor_workload = {}    # instructor_id -> number of class assignments (sections)
    room_usage          = {}    # room_id -> number of times the room is used across all days/slots
    student_days        = {}    # (level, gender) -> set of days that student group has classes

    instructor_hours = {}   # instructor_id -> total teaching hours (sum of credit hours)
    for cls in classes:
        # Extract all relevant attributes from each class assignment.
        # A "class" in GA terms is a fully assigned tuple: (section, room, instructor, day, slot).
        iid      = cls.get_instructor().get_id()
        rid      = cls.get_room().get_number()
        level    = cls.get_section().get_level()         # Academic level (1-8 typically)
        gender   = cls.get_section().get_gender()        # 'Male' or 'Female'
        day      = cls.get_day().get_day()               # Day code like 'SUN', 'MON', etc.
        slot_idx = schedule_maker.SLOT_ORDER.get(cls.get_meetingTime().get_time(), 0)  # Numeric index for ordering
        credits  = cls.get_section().get_credits()       # Credit hours for this course

        # Accumulate statistics into the tracking dictionaries
        instructor_workload[iid] = instructor_workload.get(iid, 0) + 1
        instructor_hours[iid]    = instructor_hours.get(iid, 0) + credits
        room_usage[rid]          = room_usage.get(rid, 0) + 1
        instructor_days.setdefault(iid, set()).add(day)
        instructor_slots.setdefault(iid, []).append((day, slot_idx))
        student_days.setdefault((level, gender), set()).add(day)

    # Identify the last time slot index — used by the student objective to
    # penalize late-afternoon classes (students prefer earlier schedules).
    last_slot = max(schedule_maker.SLOT_ORDER.values()) if schedule_maker.SLOT_ORDER else 99

    # ==================================================================
    # PHASE 2: HARD CONSTRAINTS (HC1-HC7)
    # ==================================================================
    # Hard constraints represent physical impossibilities or institutional
    # rules that cannot be violated. A feasible schedule must have zero
    # hard constraint violations.
    # ------------------------------------------------------------------

    # Pre-build a lookup: course_id -> list of (day, time) slots it occupies.
    # This is needed by HC7 (prerequisite overlap check) to quickly find
    # when prerequisite courses are scheduled.
    course_schedule_map = {}
    for cls in classes:
        cid = cls.get_section().get_course().get_id()
        key = (cls.get_day().get_day(), cls.get_meetingTime().get_time())
        course_schedule_map.setdefault(cid, []).append(key)

    # Outer loop: check each class individually (HC4-HC7) and pairwise (HC1-HC3).
    # The inner loop starts at i+1 to avoid double-counting pairs.
    for i in range(len(classes)):
        ci    = classes[i]
        sec_i = ci.get_section()

        # HC4: Room capacity — The assigned room must have enough seats for
        # all enrolled students. Violating this means students physically
        # cannot fit in the room.
        if ci.get_room().get_seatingCapacity() < sec_i.get_enrolled():
            conflicts += HARD

        # HC5: Prayer time — At IMAMU, certain time periods are reserved for
        # Islamic prayers (Dhuhr, Asr). No classes may be scheduled during
        # these slots. This is a religious and institutional requirement.
        if ci.get_meetingTime().get_time() in schedule_maker.PRAYER_SLOTS:
            conflicts += HARD

        # HC6: Gender separation — IMAMU enforces gender-segregated education.
        # Female sections must be in female-designated buildings and vice versa.
        # This constraint ensures no cross-gender room assignments occur.
        room_num = ci.get_room().get_number()
        if sec_i.get_gender() == 'Female' and room_num in schedule_maker.MALE_ROOMS:
            conflicts += HARD
        if sec_i.get_gender() == 'Male' and room_num in schedule_maker.FEMALE_ROOMS:
            conflicts += HARD

        # HC7: Prerequisite overlap — If course B requires course A as a
        # prerequisite, some students may be taking both courses this semester
        # (course A for the first time, course B after completing A previously,
        # OR course A is offered for retakers). To be safe, the two courses
        # must not be scheduled at the same day+time, so that no student is
        # forced to choose between a course and its prerequisite.
        prereqs = sec_i.get_course().get_prerequisites()
        if prereqs:
            my_key = (ci.get_day().get_day(), ci.get_meetingTime().get_time())
            for prereq_id in prereqs:
                if prereq_id in course_schedule_map:
                    if my_key in course_schedule_map[prereq_id]:
                        conflicts += HARD

        # Pairwise constraints: compare class i with every class j > i
        for j in range(i + 1, len(classes)):
            cj    = classes[j]
            sec_j = cj.get_section()
            # Check if both classes are scheduled at the exact same day+timeslot
            same_slot = (ci.get_meetingTime() == cj.get_meetingTime() and
                         ci.get_day()         == cj.get_day())
            if same_slot:
                # HC1: Instructor double-booking — One instructor cannot teach
                # two classes simultaneously. This is a physical impossibility.
                if ci.get_instructor() == cj.get_instructor():
                    conflicts += HARD
                # HC2: Room double-booking — One room cannot host two classes
                # at the same time. This is a physical impossibility.
                if ci.get_room() == cj.get_room():
                    conflicts += HARD
                # HC3: Student group conflict — Students of the same level and
                # gender are assumed to share the same curriculum. If two
                # different (non-lab) courses for the same student group are
                # scheduled at the same time, students must choose one and
                # miss the other. Labs are excluded because lab sections run
                # in parallel by design (students are split into lab groups).
                same_level  = sec_i.get_level()  == sec_j.get_level()
                same_gender = sec_i.get_gender() == sec_j.get_gender()
                same_course = sec_i.get_course().get_id() == sec_j.get_course().get_id()
                both_labs   = sec_i.get_is_lab() and sec_j.get_is_lab()
                if same_level and same_gender and not same_course and not both_labs:
                    conflicts += HARD

    # ==================================================================
    # SC5: Instructor teaching hours balance (ALWAYS ACTIVE)
    # ==================================================================
    # This soft constraint runs regardless of the active objective because
    # fair workload distribution is a universal concern. It penalizes
    # instructors who are assigned significantly fewer hours than average
    # (below 50% of the mean). This encourages the GA to spread teaching
    # load evenly rather than overloading some instructors while others idle.
    # The penalty is proportional to the deficit, creating smooth gradient
    # pressure toward balance.
    active_insts = [i for i in schedule_maker.data.get_instructors()
                    if i.get_id() in instructor_hours]
    if active_insts:
        avg_hrs = sum(instructor_hours.values()) / len(active_insts)
        for inst in active_insts:
            actual = instructor_hours.get(inst.get_id(), 0)
            if actual < avg_hrs * 0.5:
                conflicts += SOFT * (avg_hrs * 0.5 - actual)

    # ==================================================================
    # PHASE 3: SOFT CONSTRAINTS BY OBJECTIVE
    # ==================================================================
    # Soft constraints are quality preferences that differ based on whose
    # perspective we are optimizing for. Only one set is active per GA run.
    # This multi-objective approach lets the university generate three
    # different "best" schedules and choose or blend them.
    # ------------------------------------------------------------------
    obj = CURRENT_OBJECTIVE

    if obj == OBJECTIVE_UNIVERSITY:
        # --- University Objective: Institutional efficiency ---
        # The university cares about maximizing resource usage, minimizing
        # waste, balancing workloads, and clean scheduling patterns.

        # SC1: Room utilization — Penalize rooms that are used less than 10%
        # of available timeslots. Underused rooms represent wasted facility
        # costs. The 10% threshold is intentionally low to avoid punishing
        # rooms in small schedules (e.g., 30-section test runs).
        num_slots = len(schedule_maker.data.get_days()) * len(schedule_maker.data.get_meetingTimes())
        for rid, used in room_usage.items():
            util = used / num_slots
            if util < 0.1:
                conflicts += SOFT * 0.5

        # SC2: Room-student fit — Penalize assigning a large room to a small
        # section. If a 200-seat hall is used for 20 students, that is wasteful.
        # The penalty scales with how oversized the room is (capacity/enrolled
        # ratio), capped at 2.0 to avoid extreme penalties for tiny sections.
        for cls in classes:
            capacity = cls.get_room().get_seatingCapacity()
            enrolled = cls.get_section().get_enrolled()
            if enrolled > 0 and capacity > enrolled * 2:
                ratio = capacity / enrolled
                conflicts += SOFT * 0.5 * min(ratio / 2, 2.0)

        # SC3: Workload balance — Penalize instructors whose teaching hours
        # exceed twice the average. This prevents the GA from dumping all
        # sections onto a few instructors, which would be unfair and may
        # violate faculty agreements on maximum teaching load.
        if len(instructor_hours) > 1:
            avg_h = sum(instructor_hours.values()) / len(instructor_hours)
            for hrs in instructor_hours.values():
                if hrs > avg_h * 2:
                    conflicts += SOFT

        # SC4: Consecutive scheduling — When the same course has multiple
        # sections on the same day, they should be scheduled back-to-back
        # (adjacent time slots). This reduces room turnover, allows shared
        # setup, and makes it easier for students and instructors to plan.
        # The penalty is proportional to the gap size between non-adjacent slots.
        course_day_slots = {}
        for cls in classes:
            cid = cls.get_section().get_course().get_id()
            day = cls.get_day().get_day()
            idx = schedule_maker.SLOT_ORDER.get(cls.get_meetingTime().get_time(), 0)
            course_day_slots.setdefault((cid, day), []).append(idx)
        for slots in course_day_slots.values():
            if len(slots) > 1:
                slots.sort()
                for k in range(len(slots) - 1):
                    gap = slots[k+1] - slots[k]
                    if gap > 1:
                        conflicts += SOFT * 0.3 * gap

    elif obj == OBJECTIVE_INSTRUCTOR:
        # --- Instructor Objective: Faculty convenience ---
        # Faculty prefer fewer teaching days, no gaps between their classes,
        # and fair workload distribution.

        # Penalize instructors who teach on more than 3 days per week.
        # Most faculty prefer a compact 3-day schedule (e.g., Sun-Tue-Thu)
        # to keep 2 days free for research, office hours, or personal time.
        for iid, days in instructor_days.items():
            if len(days) > 3:
                conflicts += SOFT * (len(days) - 3)

        # Penalize gaps between an instructor's classes on the same day.
        # For example, if an instructor has classes at slot 1 and slot 4,
        # the gap of 3 slots means they wait idle on campus for 3 hours.
        # The penalty is proportional to the gap size.
        for iid, slot_list in instructor_slots.items():
            by_day = {}
            for (day, idx) in slot_list:
                by_day.setdefault(day, []).append(idx)
            for slots in by_day.values():
                slots.sort()
                for k in range(len(slots) - 1):
                    gap = slots[k+1] - slots[k]
                    if gap > 1:
                        conflicts += SOFT * gap

        # Same workload balance check as SC3 in the university objective.
        # Fair distribution matters from the instructor's perspective too.
        if len(instructor_hours) > 1:
            avg_h = sum(instructor_hours.values()) / len(instructor_hours)
            for hrs in instructor_hours.values():
                if hrs > avg_h * 2:
                    conflicts += SOFT

    elif obj == OBJECTIVE_STUDENT:
        # --- Student Objective: Student experience ---
        # Students prefer fewer days on campus, no gaps between classes,
        # and avoiding late-afternoon slots.

        # Penalize student groups that have classes on more than 3 days.
        # A student group is identified by (level, gender). Fewer campus
        # days means less commuting and more self-study time.
        for (level, gender), days in student_days.items():
            if len(days) > 3:
                conflicts += SOFT * (len(days) - 3)

        # Penalize gaps between a student group's classes on the same day.
        # A gap means students sit idle between classes, which is especially
        # problematic for commuter students who travel long distances.
        level_slots = {}
        for cls in classes:
            sec  = cls.get_section()
            key  = (sec.get_level(), sec.get_gender(), cls.get_day().get_day())
            idx  = schedule_maker.SLOT_ORDER.get(cls.get_meetingTime().get_time(), 0)
            level_slots.setdefault(key, []).append(idx)
        for slots in level_slots.values():
            slots.sort()
            for k in range(len(slots) - 1):
                gap = slots[k+1] - slots[k]
                if gap > 1:
                    conflicts += SOFT * gap

        # Penalize late time slots — Students prefer earlier schedules.
        # The last slot of the day gets a full SOFT penalty; the second-to-last
        # gets half. This models the general student preference to finish
        # earlier, especially given prayer times and hot afternoon weather
        # in Riyadh (where IMAMU is located).
        for cls in classes:
            idx = schedule_maker.SLOT_ORDER.get(cls.get_meetingTime().get_time(), 0)
            if idx == last_slot:
                conflicts += SOFT
            elif idx == last_slot - 1:
                conflicts += SOFT * 0.5

    # Store the conflict count on the schedule object so it can be retrieved
    # later for reporting (without recalculating).
    schedule_self._numbOfConflicts = conflicts
    # Return fitness: higher is better. 1.0 = perfect (no conflicts).
    return 1.0 / (1.0 + conflicts)


# MONKEY-PATCH: Replace the default fitness function in schedule_maker's Schedule
# class with our custom multi-objective version. This is the key integration point --
# the GA engine in schedule_maker calls schedule.calculate_fitness() during evolution,
# and by replacing the method here, we inject IMAMU-specific constraint logic into
# the existing GA framework without modifying the schedule_maker library itself.
schedule_maker.Schedule.calculate_fitness = calculate_fitness_multi


# ============================================================================
# CONSTRAINT VIOLATION REPORTING
# ============================================================================
def count_hard_soft(schedule):
    """
    Analyzes a completed schedule and returns a detailed breakdown of all
    hard and soft constraint violations, categorized by type (HC1-HC7).

    This function mirrors the logic of calculate_fitness_multi() but counts
    violations individually by category instead of summing weighted penalties.
    It is used for REPORTING ONLY (after the GA finishes) to give the user
    a clear picture of what constraints were satisfied and which were not.

    Why a separate function?
        The fitness function deliberately combines all violations into a single
        scalar (for the GA's selection mechanism). But for human understanding
        and for the professor discussion, we need to know exactly HOW MANY
        of each violation type remain. This function provides that transparency.

    Args:
        schedule: A Schedule object from the final GA population.

    Returns:
        tuple: (total_hard_violations, total_soft_violations)
               Hard violations are integer counts; soft is a float because
               it is computed as the residual (total_conflicts - hard_count).
    """
    classes = schedule._classes

    # Individual counters for each hard constraint category.
    # These allow the printed report to show exactly where problems remain.
    hc1_instructor = 0  # HC1: Instructor teaching two classes at once
    hc2_room       = 0  # HC2: Two classes assigned to the same room at the same time
    hc3_student    = 0  # HC3: Same student group has conflicting classes
    hc4_capacity   = 0  # HC4: Room too small for enrolled students
    hc5_prayer     = 0  # HC5: Class scheduled during prayer time
    hc6_gender     = 0  # HC6: Gender-room mismatch
    hc7_prereq     = 0  # HC7: Course and its prerequisite overlap in time

    # Build course schedule map for HC7 — same approach as in the fitness function
    course_schedule_map = {}
    for cls in classes:
        cid = cls.get_section().get_course().get_id()
        key = (cls.get_day().get_day(), cls.get_meetingTime().get_time())
        course_schedule_map.setdefault(cid, []).append(key)

    # Check every class individually (HC4-HC7) and every pair (HC1-HC3).
    # This is identical logic to the fitness function but counts occurrences
    # rather than accumulating weighted penalties.
    for i in range(len(classes)):
        ci    = classes[i]
        sec_i = ci.get_section()

        # HC4: Room capacity check
        if ci.get_room().get_seatingCapacity() < sec_i.get_enrolled():
            hc4_capacity += 1

        # HC5: Prayer time check
        if ci.get_meetingTime().get_time() in schedule_maker.PRAYER_SLOTS:
            hc5_prayer += 1

        # HC6: Gender separation check
        room_num = ci.get_room().get_number()
        if sec_i.get_gender() == 'Female' and room_num in schedule_maker.MALE_ROOMS:
            hc6_gender += 1
        if sec_i.get_gender() == 'Male' and room_num in schedule_maker.FEMALE_ROOMS:
            hc6_gender += 1

        # HC7: Prerequisite overlap check
        prereqs = sec_i.get_course().get_prerequisites()
        if prereqs:
            my_key = (ci.get_day().get_day(), ci.get_meetingTime().get_time())
            for prereq_id in prereqs:
                if prereq_id in course_schedule_map:
                    if my_key in course_schedule_map[prereq_id]:
                        hc7_prereq += 1

        # Pairwise checks for HC1 (instructor), HC2 (room), HC3 (student group)
        for j in range(i + 1, len(classes)):
            cj    = classes[j]
            sec_j = cj.get_section()
            same_slot = (ci.get_meetingTime() == cj.get_meetingTime() and
                         ci.get_day()         == cj.get_day())
            if same_slot:
                # HC1: Instructor double-booking
                if ci.get_instructor() == cj.get_instructor():
                    hc1_instructor += 1
                # HC2: Room double-booking
                if ci.get_room() == cj.get_room():
                    hc2_room += 1
                # HC3: Student group conflict (same level+gender, different non-lab courses)
                if (sec_i.get_level()  == sec_j.get_level() and
                    sec_i.get_gender() == sec_j.get_gender() and
                    sec_i.get_course().get_id() != sec_j.get_course().get_id() and
                    not (sec_i.get_is_lab() and sec_j.get_is_lab())):
                    hc3_student += 1

    # Sum all hard violations for the summary
    total_hard = hc1_instructor + hc2_room + hc3_student + hc4_capacity + hc5_prayer + hc6_gender + hc7_prereq
    # Soft violations = total weighted conflicts minus the hard count.
    # This works because hard constraints have weight 1.0 each, so subtracting
    # the integer hard count from the total float leaves only the soft portion.
    total_soft = max(0, schedule.get_numbOfConflicts() - total_hard)

    # Print a human-readable report — only non-zero categories are shown
    # to keep the output clean. This report is critical for the professor
    # discussion as it shows exactly which constraints the GA satisfied.
    print(f"    ── Hard Conflicts ({total_hard}) ──")
    if hc1_instructor: print(f"       HC1 Instructor double-booking : {hc1_instructor}")
    if hc2_room:       print(f"       HC2 Room double-booking        : {hc2_room}")
    if hc3_student:    print(f"       HC3 Student group conflict      : {hc3_student}")
    if hc4_capacity:   print(f"       HC4 Room capacity               : {hc4_capacity}")
    if hc5_prayer:     print(f"       HC5 Prayer time violation       : {hc5_prayer}")
    if hc6_gender:     print(f"       HC6 Gender separation           : {hc6_gender}")
    if hc7_prereq:     print(f"       HC7 Prerequisite overlap        : {hc7_prereq}")
    if total_hard == 0: print(f"       ✅ No hard conflicts!")
    print(f"    ── Soft Conflicts ({total_soft:.2f}) ──")
    print(f"       SC: Preferences/Gaps/Balance    : {total_soft:.2f}")

    return total_hard, total_soft

# ============================================================================
# ImamuData — IMAMU University Data Loader
# ============================================================================
class ImamuData(Data):
    """
    Extends the base Data class from schedule_maker to load IMAMU-specific
    university data from an XML configuration file (university.xml).

    The XML file is the single source of truth for the entire university model:
    - Campus layout (buildings, rooms, labs, gender assignments)
    - Time structure (working days, class periods, prayer breaks)
    - Faculty (instructors with departments, gender, teaching hour limits)
    - Students (enrollment records with completed courses)
    - Academic program (departments, courses, prerequisites, sections)

    The constructor orchestrates the loading in a specific order because
    later steps depend on earlier ones:
        1. Times/Days — needed by everything (defines the scheduling grid)
        2. Rooms — needed by sections (for capacity checks)
        3. Instructors — needed by sections (for instructor assignment pools)
        4. Students — needed by sections (to compute enrollment counts)
        5. Courses/Sections — uses all of the above
        6. Departments — groups sections for the GA's department-based approach
        7. Validate — sanity check that the problem is solvable

    Attributes inherited from Data:
        _days, _meetingTimes, _rooms, _instructors, _courses, _sections, _depts
    """
    def __init__(self, xml_file, max_sections=None, max_rooms=None):
        super().__init__()
        # Resolve XML path relative to this Python file's directory,
        # so the module works regardless of the caller's working directory.
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), xml_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Not found: {path}")
        root = ET.parse(path).getroot()

        # Load data in dependency order (each step may depend on previous ones)
        self._load_times_and_days(root)          # Step 1: Define the time grid
        self._load_rooms(root, max_rooms)         # Step 2: Load physical rooms
        self._load_instructors(root)              # Step 3: Load faculty members
        self._student_enrolled = self._load_students(root)  # Step 4: Load student records
        self._load_courses_and_sections(root, max_sections) # Step 5: Build course sections
        self._build_depts()                       # Step 6: Group sections into departments
        self._validate()                          # Step 7: Verify feasibility

    def _load_times_and_days(self, root):
        """
        Parses the TimeSlots element from university.xml to establish:
        1. Working days (e.g., SUN, MON, TUE, WED, THU — Saudi work week)
        2. Class time slots (periods available for scheduling)
        3. Prayer slots (blocked periods — no scheduling allowed)
        4. Consecutive pairs (adjacent class slots for back-to-back scheduling)

        The distinction between "Class" and "Prayer" period types is central
        to IMAMU scheduling. Prayer periods (Dhuhr, Asr) split the day into
        morning and afternoon blocks, and the GA must respect these gaps.

        Side effects:
            - Populates self._days and self._meetingTimes
            - Sets global schedule_maker.SLOT_ORDER (label -> numeric index)
            - Sets global schedule_maker.PRAYER_SLOTS (set of blocked labels)
            - Sets global schedule_maker.CONSECUTIVE_PAIRS (for SC4 gap checks)
        """
        ts = root.find('TimeSlots')

        # Load working days from XML (typically SUN-THU for Saudi Arabia)
        for d in ts.find('WorkingDays').findall('Day'):
            self._days.append(Day(d.get('code', d.text.strip()[:3].upper())))

        # Load class time slots — iterate all periods, separating Prayer from Class.
        # Prayer slots are stored separately (PRAYER_SLOTS) so the fitness function
        # can penalize any class accidentally placed during prayer time (HC5).
        # SLOT_ORDER maps each time label to a numeric index (0, 1, 2, ...) which
        # is used for gap calculations in soft constraints.
        idx = 0
        prayer_labels = []
        for p in ts.find('DailyPeriods').findall('Period'):
            start = p.find('StartTime').text.strip()
            end   = p.find('EndTime').text.strip()
            label = f"{start}-{end}"
            if p.get('type') == 'Prayer':
                prayer_labels.append(label)
            else:
                self._meetingTimes.append(MeetingTime(label))
                schedule_maker.SLOT_ORDER[label] = idx
                idx += 1

        schedule_maker.PRAYER_SLOTS = set(prayer_labels)

        # Build consecutive pairs — pairs of class slots that are directly adjacent
        # (no prayer break in between). For example, if the day is:
        #   [Class 8-9] [Class 9-10] [Prayer 10-10:30] [Class 10:30-11:30]
        # then (8-9, 9-10) is a consecutive pair, but (9-10, 10:30-11:30) is NOT
        # because a prayer break separates them. This is used by SC4 to determine
        # whether sections of the same course are truly "back-to-back."
        all_periods = ts.find('DailyPeriods').findall('Period')
        consecutive = []
        for i in range(len(all_periods) - 1):
            curr = all_periods[i]
            nxt  = all_periods[i + 1]
            if curr.get('type') == 'Class' and nxt.get('type') == 'Class':
                cl = f"{curr.find('StartTime').text.strip()}-{curr.find('EndTime').text.strip()}"
                nl = f"{nxt.find('StartTime').text.strip()}-{nxt.find('EndTime').text.strip()}"
                consecutive.append((cl, nl))
        schedule_maker.CONSECUTIVE_PAIRS = consecutive

        # Print summary for debugging/verification
        print(f"Days       : {[d.get_day() for d in self._days]}")
        print(f"Slots      : {len(self._meetingTimes)} class slots (from XML)")
        print(f"Prayer     : {' | '.join(prayer_labels)}")
        print(f"Consecutive: {len(consecutive)} valid pairs")

    def _load_rooms(self, root, max_rooms=None):
        """
        Parses the Campus element to load all rooms and labs from each building.

        Each building in the XML has a gender attribute ('Male' or 'Female'),
        and all rooms within that building inherit the same gender tag. This
        reflects IMAMU's physical campus layout where entire buildings are
        designated for one gender.

        Room IDs are also added to the global MALE_ROOMS/FEMALE_ROOMS lists
        in schedule_maker, which are used by HC6 (gender separation constraint)
        in the fitness function to penalize cross-gender room assignments.

        Both <Room> and <Lab> elements are loaded — labs are just rooms with
        potentially different capacities, but they share the same scheduling grid.
        """
        for bldg in root.find('Campus').findall('Building'):
            gender   = bldg.get('gender', '')
            rooms_el = bldg.find('Rooms')
            if rooms_el is None:
                continue
            # Load both regular classrooms and lab rooms
            for r in rooms_el.findall('Room') + rooms_el.findall('Lab'):
                rid  = r.get('id')
                cap  = int(r.get('capacity', 30))
                room = Room(rid, cap, gender)
                self._rooms.append(room)
                # Register room in global gender-specific lists for HC6 checking
                if gender == 'Male':
                    schedule_maker.MALE_ROOMS.append(rid)
                else:
                    schedule_maker.FEMALE_ROOMS.append(rid)
        print(f"Rooms : {len(self._rooms)} "
              f"({len(schedule_maker.MALE_ROOMS)} M / {len(schedule_maker.FEMALE_ROOMS)} F)")

    def _load_instructors(self, root):
        """
        Parses the Faculty element to load all instructors (professors).

        Each instructor has:
        - id: Unique identifier (e.g., "DR001")
        - name: Display name (from <n> or <Name> child element)
        - gender: 'Male' or 'Female' — determines which sections they can teach
        - department: Their home department — used for instructor pool selection
        - TeachingHours: The number of credit hours they should teach per semester.
          This is used by SC5 (instructor hours balance) to ensure fair distribution.

        The _min_hours and _max_hours are set equal (from the XML's TeachingHours),
        meaning each instructor has a fixed target load. The _min_load (in sections)
        is computed later in _load_courses_and_sections() once we know the average
        credits per course.

        Why hours-based instead of section-based?
            Different courses have different credit hours (e.g., a 3-credit lecture
            vs a 1-credit lab). Measuring load in hours rather than raw section
            count gives a fairer distribution of actual teaching effort.
        """
        for grp in root.find('Faculty'):
            for p in grp.findall('Professor'):
                # Name can be in <n> or <Name> element depending on XML version
                n_el = p.find('n') or p.find('Name')
                name = n_el.text.strip() if n_el is not None else p.get('id')
                inst = Instructor(p.get('id'), name)
                inst._gender     = p.get('gender', 'Male')
                inst._department = p.get('department', '')

                # Teaching hours from XML; defaults to 12 if not specified
                # (12 credit hours is a typical full teaching load in Saudi universities)
                th = p.find('TeachingHours')
                inst._min_hours = int(th.text.strip()) if th is not None else 12
                inst._max_hours = inst._min_hours
                # _max_load/_min_load are section-based equivalents used by
                # the base schedule_maker for backward compatibility.
                # _min_load is recalculated later based on average course credits.
                inst._max_load = inst._max_hours
                inst._min_load = 0  # Will be computed in _load_courses_and_sections

                self._instructors.append(inst)
        print(f"Instructors: {len(self._instructors)} (hours-based load)")

    def _load_students(self, root):
        """
        Parses the Students element to build a mapping of student enrollment records.

        Returns:
            dict: Mapping of (dept_name, gender, student_id) -> set of completed course IDs.

        Why track completed courses?
            To compute realistic enrollment counts. A student who has already
            completed "CS101" should NOT be counted as needing a seat in CS101
            this semester. By tracking completions, _compute_enrolled() can
            calculate how many students in a given department+gender still need
            each course, giving realistic section sizes.

        The student data is organized by Department -> Level -> Student in the XML,
        reflecting the university's hierarchical academic structure. We flatten
        this into a single dictionary keyed by (dept, gender, student_id) for
        efficient lookup.
        """
        students_el = root.find('Students')
        if students_el is None:
            return {}

        from collections import defaultdict
        student_data = {}   # (dept, gender, student_id) -> set of completed course IDs

        for dept_el in students_el.findall('Department'):
            dept_name = dept_el.get('name')
            for lv_el in dept_el.findall('Level'):
                for s_el in lv_el.findall('Student'):
                    gender  = s_el.get('gender')
                    sid     = s_el.get('id')
                    comp_el = s_el.find('CompletedCourses')
                    # Parse comma-separated list of completed course IDs, or empty set
                    completed = set(comp_el.text.split(',')) if (comp_el is not None and comp_el.text) else set()
                    student_data[(dept_name, gender, sid)] = completed

        total = len(student_data)
        print(f"Students: {total} loaded")
        return student_data

    def _compute_enrolled(self, dept_name, gender, course_id):
        """
        Computes the number of eligible (not-yet-completed) students for a specific
        course in a given department and gender group.

        This is used during section loading to set realistic enrollment numbers.
        A student is "eligible" for a course if they belong to the right department
        and gender AND have NOT already completed that course.

        Args:
            dept_name: Department name to filter students by.
            gender: 'Male' or 'Female' to filter by gender.
            course_id: The course ID to check eligibility for.

        Returns:
            int or None: Number of eligible students, or None if no student data exists.
        """
        if not self._student_enrolled:
            return None
        return sum(
            1 for (d, g, sid), completed in self._student_enrolled.items()
            if d == dept_name and g == gender and course_id not in completed
        )

    def _load_courses_and_sections(self, root, max_sections):
        """
        Parses the AcademicProgram element to load courses and create sections.

        This is the most complex loading step because it must:
        1. Build a prerequisites map (course -> list of prerequisite course IDs)
        2. Create Course objects with all metadata
        3. Separate sections by gender for balanced loading
        4. Interleave male/female sections using round-robin to ensure equal
           representation when max_sections limits the total
        5. Compute realistic enrollment from student data
        6. Assign instructor pools respecting gender and department constraints
        7. Convert instructor hour-based loads to section-based loads

        Args:
            root: XML root element.
            max_sections: Optional cap on total sections to load. When set (e.g., 30
                for quick_test), the round-robin interleaving ensures we don't load
                all male sections first and zero female sections — we get ~50/50.

        Why round-robin gender interleaving?
            Without it, if max_sections=30 and there are 40 male + 40 female sections,
            a naive sequential load would take 30 male sections and 0 female, making
            all female rooms unused and the schedule heavily biased. The interleaved
            approach guarantees fair gender representation at any cap level.
        """
        # Pre-split instructors by gender for efficient pool assignment later.
        # Each section can only be taught by an instructor of matching gender.
        male_inst   = [i for i in self._instructors if i._gender == 'Male']
        female_inst = [i for i in self._instructors if i._gender == 'Female']
        count       = 0    # Running count of loaded sections (for max_sections cap)

        # --- Step 1: Build prerequisites map ---
        # Parse all courses once to extract prerequisite relationships.
        # This is done first because Course objects need their prerequisites
        # at construction time for HC7 constraint checking.
        prereq_map = {}
        for dept_el in root.find('AcademicProgram').findall('Department'):
            for course_el in dept_el.findall('Course'):
                cid     = course_el.get('id')
                pre_el  = course_el.find('Prerequisites')
                pre_txt = pre_el.text.strip() if pre_el is not None and pre_el.text else None
                if pre_txt and pre_txt.lower() != 'none':
                    prereq_map[cid] = [p.strip() for p in pre_txt.split(',')]
                else:
                    prereq_map[cid] = []

        # --- Step 2: Create Course objects and collect section XML elements ---
        all_courses_data = []
        for dept_el in root.find('AcademicProgram').findall('Department'):
            dept_name = dept_el.get('name', 'Unknown')
            for course_el in dept_el.findall('Course'):
                cid     = course_el.get('id')
                level   = int(course_el.get('level', 1))   # Academic level (1=freshman, etc.)
                credits = int(course_el.get('credits', 3))  # Credit hours per section
                n_el    = course_el.find('Name')
                cname   = n_el.text.strip() if n_el is not None else cid
                t_el    = course_el.find('Type')
                is_lab  = t_el is not None and 'lab' in t_el.text.lower()
                secs_el = course_el.find('Sections')
                if secs_el is None:
                    continue
                course_obj = Course(
                    course_id=cid, name=cname, level=level,
                    is_lab=is_lab, dept_name=dept_name, credits=credits,
                    prerequisites=prereq_map.get(cid, [])
                )
                self._courses.append(course_obj)
                all_courses_data.append((dept_name, course_obj,
                                         secs_el.findall('Section')))

        # --- Step 3: Separate sections by gender ---
        # Collect all section XML elements into two lists: male and female.
        # This enables the round-robin interleaving in Step 4.
        male_sections_data   = []
        female_sections_data = []
        for dept_name, course_obj, sections in all_courses_data:
            for sec_el in sections:
                sec_gender = sec_el.get('gender', 'Male')
                if sec_gender == 'Male':
                    male_sections_data.append((dept_name, course_obj, sec_el))
                else:
                    female_sections_data.append((dept_name, course_obj, sec_el))

        # --- Step 4: Round-robin interleaving ---
        # Alternate between male and female sections: M, F, M, F, ...
        # This ensures that when we truncate at max_sections, both genders
        # are equally represented. Without this, one gender could dominate.
        interleaved = []
        m_idx, f_idx = 0, 0
        while m_idx < len(male_sections_data) or f_idx < len(female_sections_data):
            if m_idx < len(male_sections_data):
                interleaved.append(male_sections_data[m_idx])
                m_idx += 1
            if f_idx < len(female_sections_data):
                interleaved.append(female_sections_data[f_idx])
                f_idx += 1

        # --- Step 5: Create Section objects from the interleaved list ---
        for dept_name, course_obj, sec_el in interleaved:
            if max_sections and count >= max_sections:
                break
            sec_id     = sec_el.get('id')
            sec_gender = sec_el.get('gender', 'Male')
            cap_el     = sec_el.find('Capacity')
            capacity   = int(cap_el.text.strip()) if cap_el is not None else 40

            # Compute realistic enrollment from actual student records.
            # Total eligible students for this course+gender are divided by
            # the number of sections of this course+gender to get per-section enrollment.
            # Floor of 5 ensures every section has at least minimal enrollment.
            # Capped at room capacity to avoid HC4 violations from data alone.
            sections   = [s for d, c, s in (male_sections_data + female_sections_data)
                          if c.get_id() == course_obj.get_id()]
            total_eligible = self._compute_enrolled(dept_name, sec_gender, course_obj.get_id())
            num_secs       = max(1, sum(1 for s in sections if s.get('gender') == sec_gender))
            enrolled       = max(5, (total_eligible or 20) // num_secs)
            enrolled       = min(enrolled, capacity)

            # Build the instructor pool for this section using a cascading fallback:
            # 1. Same gender + same department (ideal match)
            # 2. Same gender, any department (fallback if dept has no instructors)
            # 3. Any instructor at all (last resort, should rarely happen)
            pool = [i for i in (male_inst if sec_gender == 'Male' else female_inst)
                    if i._department == dept_name]
            if not pool:
                pool = male_inst if sec_gender == 'Male' else female_inst
            if not pool:
                pool = self._instructors

            self._sections.append(Section(
                section_id=sec_id, course=course_obj,
                gender=sec_gender, enrolled=enrolled,
                capacity=capacity, eligible_instructors=pool
            ))
            count += 1

        print(f"Courses : {len(self._courses)} (with prerequisites)")
        print(f"Sections: {len(self._sections)}")

        # --- Step 6: Convert instructor hour-based loads to section-based loads ---
        # The base schedule_maker uses _min_load (sections) internally, but our
        # instructors have _min_hours (credit hours). We convert by dividing
        # by the average credits per course. For example, if avg_credits=3 and
        # an instructor's min_hours=12, then min_load = 12/3 = 4 sections.
        if self._instructors and self._courses:
            avg_credits = sum(c.get_credits() for c in self._courses) / len(self._courses)
            for inst in self._instructors:
                inst._min_load = max(0, int(inst._min_hours / avg_credits))
            print(f"Min loads: hours-based (avg_credits={avg_credits:.1f})")

    def _build_depts(self):
        """
        Groups sections into Department objects for the GA.

        Sections are grouped by (department_name, gender), creating separate
        "departments" for male and female sections of the same academic department.
        For example, "CS_M" and "CS_F" are treated as separate departments.

        Why split by gender?
            The GA's crossover and mutation operators work at the department level.
            By splitting departments by gender, the GA can evolve male and female
            schedules semi-independently, which improves convergence because
            male sections only compete for male rooms and male instructors.
        """
        depts_map = {}
        for sec in self._sections:
            key = f"{sec.get_dept_name()}_{'M' if sec.get_gender() == 'Male' else 'F'}"
            depts_map.setdefault(key, []).append(sec)
        for name, sections in depts_map.items():
            self._depts.append(Department(name, sections))
        print(f"Depts   : {len(self._depts)}")

    def _validate(self):
        """
        Sanity check: ensures the scheduling problem is theoretically solvable.

        The total available slots = rooms x time_slots x days. If the number
        of sections exceeds this, no feasible schedule exists (pigeon-hole
        principle — you cannot fit N+1 classes into N slots). This prints
        a warning but does not raise an error, as the GA will simply do its
        best with an overloaded problem and report remaining conflicts.
        """
        total_slots = len(self._rooms) * len(self._meetingTimes) * len(self._days)
        ok = "OK" if len(self._sections) <= total_slots else "WARNING: OVERLOADED"
        print(f"Slots   : {total_slots} -> {ok}\n")


# ============================================================================
# GA RUNNER — Core Evolutionary Loop
# ============================================================================
def _run_ga(label, objective, data, pop_size, mutation_rate, top_n=1):
    """
    Executes the Genetic Algorithm for a single optimization objective.

    This function manages the full evolutionary process:
    1. Sets the global objective (which soft constraints to activate)
    2. Configures the GA engine parameters (population size, mutation rate, elitism)
    3. Runs `top_n` independent GA executions, each starting from a fresh random
       population, to produce multiple candidate schedules
    4. Each run evolves until one of four termination conditions is met

    Termination conditions (whichever triggers first):
        - OPTIMAL: fitness reaches 1.0 (zero conflicts — perfect schedule)
        - STAGNATION: 3000 generations with no improvement in best conflict count.
          This prevents wasting time when the GA is stuck in a local optimum.
        - TIME LIMIT: 1000 seconds (~16.7 minutes) per run. Ensures the system
          always produces results within a reasonable time.
        - MAX GENERATIONS: 10000 generations as an absolute upper bound.

    Why multiple independent runs (top_n)?
        GAs are stochastic — different random initial populations may converge to
        different local optima. By running the GA top_n times independently, we
        get multiple distinct "good" schedules. The user/university can then
        compare them and pick the best one or blend elements from several.

    Elitism:
        NUMB_OF_ELITE_SCHEDULES = max(3, pop_size // 33) ensures the top few
        schedules survive unchanged into the next generation. This prevents the
        best solution found so far from being lost to crossover/mutation.

    Args:
        label: Display label for this run (e.g., "University-Optimized")
        objective: One of OBJECTIVE_UNIVERSITY, OBJECTIVE_INSTRUCTOR, OBJECTIVE_STUDENT
        data: ImamuData object with all university data loaded
        pop_size: Number of schedules in each generation
        mutation_rate: Probability of mutating each gene (class assignment)
        top_n: Number of independent runs to perform

    Returns:
        list: The top_n best Schedule objects (one from each independent run)
    """
    # Set the global objective so calculate_fitness_multi() knows which
    # soft constraints to activate during this run.
    global CURRENT_OBJECTIVE
    CURRENT_OBJECTIVE = objective

    # Configure the schedule_maker GA engine with our parameters.
    # These are module-level globals in schedule_maker that control its behavior.
    schedule_maker.data = data
    schedule_maker.POPULATION_SIZE         = pop_size
    schedule_maker.MUTATION_RATE           = mutation_rate
    # Elitism: preserve the top ~3% of the population (minimum 3) unchanged.
    # This ensures the best solutions are never lost during evolution.
    schedule_maker.NUMB_OF_ELITE_SCHEDULES = max(3, pop_size // 33)

    # Termination thresholds
    MAX_STAGNATION = 3000   # Generations without improvement before giving up
    MAX_TIME       = 1000   # Wall-clock seconds (about 16.7 minutes)
    MAX_GENS       = 10000  # Absolute generation cap

    print(f"\n{'='*60}")
    print(f"  {label}  (top {top_n})")
    print(f"{'='*60}")

    best_schedules = []

    for run in range(top_n):
        # Create a fresh random population for each independent run.
        # Each schedule in the population is a random assignment of
        # (room, instructor, day, timeslot) to each section.
        population = Population(pop_size)
        population.get_schedules().sort(key=lambda x: x.get_fitness(), reverse=True)
        ga = GeneticAlgorithm()
        gen = stagnation = 0
        last_c = float('inf')   # Best conflict count seen so far (lower is better)
        t0     = time.time()

        print(f"  Run {run+1}/{top_n}")

        # Main evolutionary loop — continue until fitness=1.0 or a termination condition
        while population.get_schedules()[0].get_fitness() < 1.0:
            gen += 1
            # Evolve: selection -> crossover -> mutation -> new generation
            population = ga.evolve(population)
            population.get_schedules().sort(key=lambda x: x.get_fitness(), reverse=True)
            best = population.get_schedules()[0]
            c, f = best.get_numbOfConflicts(), best.get_fitness()

            # Track stagnation: reset counter if we found a better solution,
            # otherwise increment. This detects when the GA is stuck.
            stagnation = 0 if c < last_c else stagnation + 1
            last_c = min(c, last_c)

            # Progress logging every 100 generations or when optimal
            if gen % 100 == 0 or f >= 1.0:
                print(f"    Gen {gen:5d} | Conflicts: {c:6.2f} | Fitness: {f:.4f} | {time.time()-t0:.1f}s")

            # Check termination conditions
            if f >= 1.0: break                                          # Perfect schedule found
            if stagnation >= MAX_STAGNATION: print("    Stopped: stagnation"); break  # Stuck in local optimum
            if time.time() - t0 > MAX_TIME:  print("    Stopped: time limit"); break  # Timeout
            if gen >= MAX_GENS:              print("    Stopped: max gens");   break   # Generation cap

        # Report the outcome of this run
        final   = population.get_schedules()[0]
        elapsed = time.time() - t0
        status  = "OPTIMAL ✓" if final.get_fitness() >= 1.0 else f"{final.get_numbOfConflicts():.2f} conflicts"
        print(f"    Result: {status} | Time: {elapsed:.2f}s | Gens: {gen}")
        # Print detailed constraint violation breakdown for analysis
        hard, soft = count_hard_soft(final)
        print(f"    Hard: {hard} | Soft: {soft:.2f}")
        best_schedules.append(final)

    return best_schedules


# ============================================================================
# ORCHESTRATOR — Run GA for All Three Objectives
# ============================================================================

def run_all_schedules(max_sections=None, max_rooms=None, pop_size=100, mutation_rate=0.1,
                      xml_file="university.xml", top_n=3):
    """
    Main entry point: runs the GA for all three objectives and displays results.

    This function:
    1. Loads university data from XML into an ImamuData object
    2. Runs the GA three times — once for each stakeholder objective:
       - University: maximize resource efficiency
       - Instructor: maximize faculty satisfaction
       - Student: maximize student experience
    3. Each run produces top_n candidate schedules (e.g., 3)
    4. Prints all schedules as formatted timetables

    The result is 3 x top_n schedules (e.g., 9 total), giving the university
    administration a rich set of options to evaluate and choose from.

    Args:
        max_sections: Cap on sections to load (None = all). Use smaller values for testing.
        max_rooms: Cap on rooms (currently unused, reserved for future use).
        pop_size: GA population size. Larger = better solutions but slower.
        mutation_rate: Probability of random changes during evolution (0.0-1.0).
            Higher rates explore more broadly but may slow convergence.
        xml_file: Path to the university configuration XML file.
        top_n: Number of independent runs per objective.

    Returns:
        dict: {"university": [schedules], "instructor": [schedules], "student": [schedules]}
    """
    print("\n" + "="*60)
    print(f"  IMAMU — Top {top_n} Schedules per Objective (GA)")
    print("="*60 + "\n")

    # Load all university data from the XML configuration file
    data = ImamuData(xml_file, max_sections=max_sections)

    # Run GA for each of the three optimization objectives
    results = {
        "university": _run_ga("University-Optimized", OBJECTIVE_UNIVERSITY, data, pop_size, mutation_rate, top_n),
        "instructor":  _run_ga("Instructor-Optimized", OBJECTIVE_INSTRUCTOR, data, pop_size, mutation_rate, top_n),
        "student":     _run_ga("Student-Optimized",    OBJECTIVE_STUDENT,    data, pop_size, mutation_rate, top_n),
    }

    # Print all generated schedules as formatted timetables
    display = DisplayMgr()
    obj_labels = {
        "university": "University-Optimized",
        "instructor": "Instructor-Optimized",
        "student":    "Student-Optimized",
    }
    for obj, schedules in results.items():
        for rank, sched in enumerate(schedules, 1):
            print(f"\n{'#'*65}")
            print(f"  {obj_labels[obj]} — Rank #{rank}")
            print(f"  Conflicts: {sched.get_numbOfConflicts():.2f}  |  Fitness: {sched.get_fitness():.4f}")
            print(f"{'#'*65}\n")
            display.print_schedule_as_table(sched)

    print("\n" + "="*60)
    print(f"  Done. {top_n * 3} schedules generated ({top_n} per objective).")
    print("="*60)
    return results


# ============================================================================
# PRESET CONFIGURATIONS
# ============================================================================
# These convenience functions provide pre-tuned parameter sets for different
# use cases. The trade-off is always: more sections + larger population = better
# results but longer runtime.

def quick_test():
    """
    Fast test configuration for development and debugging.
    - 30 sections (small subset of the full university)
    - Population of 15 (very small — fast but may not find optimal solutions)
    - Mutation rate 0.2 (higher than normal to explore broadly with small pop)
    Typical runtime: ~30 seconds to 2 minutes.
    """
    return run_all_schedules(max_sections=30, pop_size=15, mutation_rate=0.2)

def medium_run():
    """
    Moderate configuration for demos and development testing.
    - 60 sections (significant portion of the university)
    - Population of 30 (reasonable for meaningful evolution)
    - Mutation rate 0.2 (slightly high to compensate for medium pop size)
    Typical runtime: ~2 to 10 minutes.
    """
    return run_all_schedules(max_sections=60, pop_size=30, mutation_rate=0.2)


def full_run():
    """
    Production configuration for generating real schedules.
    - All sections (no cap — loads every section from the XML)
    - Population of 100 (large enough for good genetic diversity)
    - Mutation rate 0.1 (conservative — relies more on crossover for refinement)
    Typical runtime: ~10 to 60 minutes depending on university size.
    """
    return run_all_schedules(pop_size=100, mutation_rate=0.1)


# Entry point: when this file is executed directly, run the medium configuration.
# This is suitable for development testing and demo purposes.
if __name__ == "__main__":
    medium_run()