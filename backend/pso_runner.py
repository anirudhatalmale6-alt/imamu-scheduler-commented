"""
pso_runner.py -- Particle Swarm Optimization (PSO) runner for IMAMU course scheduling.

====================================================================================
WHAT IS PSO?
====================================================================================
Particle Swarm Optimization is a metaheuristic optimization algorithm inspired by
the social behavior of bird flocking or fish schooling. A "swarm" of candidate
solutions ("particles") moves through the search space. Each particle tracks:
  - Its current position (a complete schedule).
  - Its personal best position (pbest) -- the best schedule this particle has
    ever found.
  - The global best position (gbest) -- the best schedule ANY particle has found.

In each iteration, every particle updates its position by blending information from
its personal best, the global best, and some random exploration.

====================================================================================
HOW THIS DIFFERS FROM GA (GA_runner.py)
====================================================================================
GA_runner.py uses a Genetic Algorithm: population -> selection -> crossover ->
mutation -> next generation. Individuals are recombined via crossover (mixing
genetic material from two parents) and perturbed via mutation.

PSO has NO crossover and NO selection tournament. Instead, every particle updates
itself independently based on three influences:
  1. Random exploration (analogous to GA mutation) -- try something new.
  2. Cognitive factor (pbest) -- move toward your own best-known solution.
  3. Social factor (gbest)   -- move toward the swarm's best-known solution.

Both runners share identical:
  - ImamuData class (XML parser for university data)
  - calculate_fitness_multi() function (multi-objective fitness with same HC/SC)
  - count_hard_soft() diagnostic function
  - Stopping criteria (stagnation, time limit, max generations)

====================================================================================
WHY DISCRETE PSO?
====================================================================================
Standard PSO operates on continuous real-valued vectors with velocity updates:
    v_new = w*v + c1*r1*(pbest - x) + c2*r2*(gbest - x)
    x_new = x + v_new

This does NOT work for scheduling because the search space is discrete and
combinatorial: each "dimension" is a SectionGroup containing a room, instructor,
time slot, and day -- none of which can be meaningfully added or subtracted as
real numbers.

Instead, this implementation uses a PROBABILISTIC DISCRETE PSO:
  - For each SectionGroup (the atomic scheduling unit), a random number decides
    which source to copy from:
      * With probability `mutation_rate`           -> random exploration (new random schedule)
      * With probability (1 - mutation_rate) / 2   -> cognitive (copy from personal best)
      * With remaining probability                 -> social (copy from global best)
  - This mimics the continuous PSO's balance between exploration, cognitive pull,
    and social pull, but through probabilistic selection rather than arithmetic.

The unit of operation is the SectionGroup (defined in schedule_maker.py), which
represents one section's complete weekly class assignments (room, instructor,
day(s), and time slot(s)). The state is extracted/applied via:
  - Schedule.extract_group_state() -> snapshot of all groups' assignments
  - Schedule.apply_group_state()   -> restore a snapshot onto a schedule

====================================================================================
RELATIONSHIP TO schedule_maker.py
====================================================================================
schedule_maker.py provides all the core domain classes:
  - Data, Course, Section, Room, Instructor, MeetingTime, Day, Department
  - SectionGroup (the atomic scheduling unit: one section's full weekly slots)
  - Schedule (container of SectionGroups, with initialize() and fitness calculation)
  - Population, GeneticAlgorithm (used by GA_runner.py, NOT used here)
  - DisplayMgr (pretty-printing schedules)

This file monkey-patches Schedule.calculate_fitness with a multi-objective version
(calculate_fitness_multi) that supports three optimization objectives (university,
instructor, student). It also uses Schedule.extract_group_state() and
apply_group_state() -- PSO-specific helper methods defined in schedule_maker.py --
to capture and restore the discrete state of each particle.

====================================================================================
"""

import xml.etree.ElementTree as ET
import os, time, random
import schedule_maker

from schedule_maker import (Data, Course, Section, Room, Instructor, MeetingTime,
                             Department, Population, GeneticAlgorithm, DisplayMgr, Day)

# ---------------------------------------------------------------------------
# Optimization objective constants.
# These select which set of soft constraints are active during fitness evaluation.
# The fitness function checks CURRENT_OBJECTIVE to decide which SC penalties apply.
#   - "university": optimizes room utilization, room-student fit, workload balance,
#                   and consecutive section scheduling.
#   - "instructor": minimizes instructor teaching days (prefer <= 3), reduces gaps
#                   between an instructor's slots on the same day, balances workload.
#   - "student":    minimizes student group teaching days, reduces gaps in student
#                   schedules, penalizes late time slots.
# ---------------------------------------------------------------------------
OBJECTIVE_UNIVERSITY = "university"
OBJECTIVE_INSTRUCTOR = "instructor"
OBJECTIVE_STUDENT    = "student"
CURRENT_OBJECTIVE    = OBJECTIVE_UNIVERSITY  # Default; overwritten per run


# ===========================================================================
# Multi-Objective Fitness Function
# ===========================================================================
# This function replaces the default Schedule.calculate_fitness() from
# schedule_maker.py via monkey-patching (see line after the function).
# It is IDENTICAL to the one in GA_runner.py -- both runners share the same
# fitness logic so that results are directly comparable between GA and PSO.
#
# Returns a float in (0, 1]. A fitness of 1.0 means zero conflicts (optimal).
# The formula is: fitness = 1 / (1 + total_weighted_conflicts)
#
# Conflicts are divided into:
#   HARD constraints (weight 1.0 each) -- must be satisfied for a valid schedule
#   SOFT constraints (weight 0.1 each) -- preferences that improve quality
# ===========================================================================
def calculate_fitness_multi(schedule_self):
    # Penalty weights: hard constraints are 10x more costly than soft ones.
    HARD = 1.0
    SOFT = 0.1
    conflicts = 0.0
    # Flatten all SectionGroups into individual ClassSlot objects for evaluation.
    # Each ClassSlot represents one 50-minute meeting (a section may have 2-4 slots).
    classes   = schedule_self._classes

    # -----------------------------------------------------------------------
    # Accumulator dictionaries -- built in a single pass over all class slots.
    # These aggregate statistics needed by both hard and soft constraint checks.
    # -----------------------------------------------------------------------
    instructor_days     = {}  # iid -> set of day codes the instructor teaches on
    instructor_slots    = {}  # iid -> list of (day, slot_index) tuples
    instructor_workload = {}  # iid -> total number of class slots assigned
    room_usage          = {}  # room_id -> total number of class slots hosted
    student_days        = {}  # (level, gender) -> set of day codes students attend

    instructor_hours = {}   # iid -> total teaching hours (sum of credits across slots)
    for cls in classes:
        iid      = cls.get_instructor().get_id()
        rid      = cls.get_room().get_number()
        level    = cls.get_section().get_level()
        gender   = cls.get_section().get_gender()
        day      = cls.get_day().get_day()
        slot_idx = schedule_maker.SLOT_ORDER.get(cls.get_meetingTime().get_time(), 0)
        credits  = cls.get_section().get_credits()

        instructor_workload[iid] = instructor_workload.get(iid, 0) + 1
        instructor_hours[iid]    = instructor_hours.get(iid, 0) + credits
        room_usage[rid]          = room_usage.get(rid, 0) + 1
        instructor_days.setdefault(iid, set()).add(day)
        instructor_slots.setdefault(iid, []).append((day, slot_idx))
        student_days.setdefault((level, gender), set()).add(day)

    # Index of the latest time slot in the day (used by student objective for late-slot penalty).
    last_slot = max(schedule_maker.SLOT_ORDER.values()) if schedule_maker.SLOT_ORDER else 99

    # ── Hard Constraints ──────────────────────────────────────────────────
    # These represent absolute violations that make a schedule infeasible.
    # Each violation adds HARD (1.0) to the conflict score.

    # Build a lookup: course_id -> list of (day, time) pairs where it is scheduled.
    # Used by HC7 (prerequisite overlap check).
    course_schedule_map = {}
    for cls in classes:
        cid = cls.get_section().get_course().get_id()
        key = (cls.get_day().get_day(), cls.get_meetingTime().get_time())
        course_schedule_map.setdefault(cid, []).append(key)

    # Pairwise and per-slot constraint checking.
    # The outer loop checks per-slot constraints (HC4-HC7) for each class.
    # The inner loop checks pairwise constraints (HC1-HC3) for each pair.
    for i in range(len(classes)):
        ci    = classes[i]
        sec_i = ci.get_section()

        # HC4: Room capacity -- the room must have enough seats for enrolled students.
        if ci.get_room().get_seatingCapacity() < sec_i.get_enrolled():
            conflicts += HARD

        # HC5: Prayer time -- no classes may be scheduled during prayer slots.
        # (Prayer slots are excluded during initialization, but PSO state blending
        # could theoretically reintroduce them, so this is a safety check.)
        if ci.get_meetingTime().get_time() in schedule_maker.PRAYER_SLOTS:
            conflicts += HARD

        # HC6: Gender separation -- IMAMU enforces gender-segregated rooms.
        # Female sections cannot be in male-designated rooms and vice versa.
        room_num = ci.get_room().get_number()
        if sec_i.get_gender() == 'Female' and room_num in schedule_maker.MALE_ROOMS:
            conflicts += HARD
        if sec_i.get_gender() == 'Male' and room_num in schedule_maker.FEMALE_ROOMS:
            conflicts += HARD

        # HC7: Prerequisites -- a course and its prerequisite must not be scheduled
        # at the exact same (day, time) slot. Students taking the course need to have
        # already completed the prerequisite, so overlapping times would block
        # students who are taking both in the same semester.
        prereqs = sec_i.get_course().get_prerequisites()
        if prereqs:
            my_key = (ci.get_day().get_day(), ci.get_meetingTime().get_time())
            for prereq_id in prereqs:
                if prereq_id in course_schedule_map:
                    if my_key in course_schedule_map[prereq_id]:
                        conflicts += HARD

        # Pairwise checks: compare class i with every class j > i (avoid double-counting).
        for j in range(i + 1, len(classes)):
            cj    = classes[j]
            sec_j = cj.get_section()
            # Two classes occupy the same time slot if they share both day and meeting time.
            same_slot = (ci.get_meetingTime() == cj.get_meetingTime() and
                         ci.get_day()         == cj.get_day())
            if same_slot:
                # HC1: Instructor double-booking -- one instructor cannot teach two
                # classes at the same time.
                if ci.get_instructor() == cj.get_instructor():
                    conflicts += HARD
                # HC2: Room double-booking -- one room cannot host two classes at once.
                if ci.get_room() == cj.get_room():
                    conflicts += HARD
                # HC3: Student group conflict -- two different non-lab courses for
                # the same (level, gender) group cannot overlap. Students at the same
                # level and gender are assumed to share a common timetable.
                # Exception: two lab sections CAN overlap (students only attend one).
                same_level  = sec_i.get_level()  == sec_j.get_level()
                same_gender = sec_i.get_gender() == sec_j.get_gender()
                same_course = sec_i.get_course().get_id() == sec_j.get_course().get_id()
                both_labs   = sec_i.get_is_lab() and sec_j.get_is_lab()
                if same_level and same_gender and not same_course and not both_labs:
                    conflicts += HARD

    # SC5: Instructor hours balance (applies to ALL objectives).
    # Penalizes instructors whose total teaching hours fall below 50% of the average.
    # This encourages an even distribution of teaching load across the faculty.
    active_insts = [i for i in schedule_maker.data.get_instructors()
                    if i.get_id() in instructor_hours]
    if active_insts:
        avg_hrs = sum(instructor_hours.values()) / len(active_insts)
        for inst in active_insts:
            actual = instructor_hours.get(inst.get_id(), 0)
            if actual < avg_hrs * 0.5:
                # Penalty proportional to how far below 50% of average the instructor is.
                conflicts += SOFT * (avg_hrs * 0.5 - actual)

    # ── Soft Constraints by Objective ─────────────────────────────────────
    # Only the soft constraints for the currently active objective are evaluated.
    # This allows the same fitness function to produce schedules optimized for
    # different stakeholders (university admin, instructors, or students).
    obj = CURRENT_OBJECTIVE

    if obj == OBJECTIVE_UNIVERSITY:
        # SC1: Room utilization -- penalize rooms used in fewer than 10% of available slots.
        # Encourages consolidation of classes into fewer rooms (energy/maintenance savings).
        num_slots = len(schedule_maker.data.get_days()) * len(schedule_maker.data.get_meetingTimes())
        for rid, used in room_usage.items():
            util = used / num_slots
            if util < 0.1:
                conflicts += SOFT * 0.5

        # SC2: Room-student fit -- penalize rooms that are more than 2x the enrolled size.
        # Putting 10 students in a 200-seat auditorium wastes resources.
        for cls in classes:
            capacity = cls.get_room().get_seatingCapacity()
            enrolled = cls.get_section().get_enrolled()
            if enrolled > 0 and capacity > enrolled * 2:
                ratio = capacity / enrolled
                conflicts += SOFT * 0.5 * min(ratio / 2, 2.0)

        # SC3: Workload balance -- penalize any instructor with > 2x the average hours.
        if len(instructor_hours) > 1:
            avg_h = sum(instructor_hours.values()) / len(instructor_hours)
            for hrs in instructor_hours.values():
                if hrs > avg_h * 2:
                    conflicts += SOFT

        # SC4: Consecutive sections -- same course sections on the same day should be
        # back-to-back. Gaps between them waste student and room time.
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
                        # Penalty scales with gap size (larger gaps are worse).
                        conflicts += SOFT * 0.3 * gap

    elif obj == OBJECTIVE_INSTRUCTOR:
        # Penalize instructors who teach on more than 3 days per week.
        # Instructors prefer compact schedules with fewer commuting days.
        for iid, days in instructor_days.items():
            if len(days) > 3:
                conflicts += SOFT * (len(days) - 3)
        # Penalize gaps between an instructor's slots on the same day.
        # E.g., teaching at 8am and 2pm with nothing in between is undesirable.
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
        # Workload balance (same as university SC3).
        if len(instructor_hours) > 1:
            avg_h = sum(instructor_hours.values()) / len(instructor_hours)
            for hrs in instructor_hours.values():
                if hrs > avg_h * 2:
                    conflicts += SOFT

    elif obj == OBJECTIVE_STUDENT:
        # Penalize student groups (level, gender) that have classes on > 3 days/week.
        for (level, gender), days in student_days.items():
            if len(days) > 3:
                conflicts += SOFT * (len(days) - 3)
        # Penalize gaps in student schedules on the same day.
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
        # Penalize late time slots -- students prefer not to have classes at the end
        # of the day. Last slot gets full penalty, second-to-last gets half.
        for cls in classes:
            idx = schedule_maker.SLOT_ORDER.get(cls.get_meetingTime().get_time(), 0)
            if idx == last_slot:
                conflicts += SOFT
            elif idx == last_slot - 1:
                conflicts += SOFT * 0.5

    # Store total conflicts and return fitness score.
    # fitness = 1.0 means zero conflicts (perfect schedule).
    schedule_self._numbOfConflicts = conflicts
    return 1.0 / (1.0 + conflicts)


# Monkey-patch: replace the default fitness function in schedule_maker.Schedule
# with our multi-objective version. This affects ALL Schedule instances created
# after this point, including those created inside _run_pso().
schedule_maker.Schedule.calculate_fitness = calculate_fitness_multi


# ===========================================================================
# Diagnostic: Hard vs. Soft Conflict Breakdown
# ===========================================================================
def count_hard_soft(schedule):
    """
    Analyzes a finished schedule and prints a detailed breakdown of constraint
    violations, separated into hard (feasibility) and soft (quality) categories.

    This is called after each PSO run completes to give the user visibility into
    what types of conflicts remain (if any). It re-checks each hard constraint
    individually and counts occurrences, then infers the soft conflict total by
    subtracting hard conflicts from the overall conflict score.

    Args:
        schedule: A Schedule object whose fitness has already been calculated.

    Returns:
        (total_hard, total_soft): Tuple of integer hard count and float soft count.
    """
    classes = schedule._classes

    # Individual hard constraint violation counters.
    hc1_instructor = 0  # HC1: same instructor, same time slot
    hc2_room       = 0  # HC2: same room, same time slot
    hc3_student    = 0  # HC3: same student group, different courses, same slot
    hc4_capacity   = 0  # HC4: room too small for enrolled students
    hc5_prayer     = 0  # HC5: class scheduled during prayer time
    hc6_gender     = 0  # HC6: gender-room mismatch
    hc7_prereq     = 0  # HC7: course overlaps with its prerequisite

    # Build course -> (day, time) lookup for prerequisite overlap detection.
    course_schedule_map = {}
    for cls in classes:
        cid = cls.get_section().get_course().get_id()
        key = (cls.get_day().get_day(), cls.get_meetingTime().get_time())
        course_schedule_map.setdefault(cid, []).append(key)

    for i in range(len(classes)):
        ci    = classes[i]
        sec_i = ci.get_section()

        # HC4: Room capacity check.
        if ci.get_room().get_seatingCapacity() < sec_i.get_enrolled():
            hc4_capacity += 1

        # HC5: Prayer time check.
        if ci.get_meetingTime().get_time() in schedule_maker.PRAYER_SLOTS:
            hc5_prayer += 1

        # HC6: Gender separation check.
        room_num = ci.get_room().get_number()
        if sec_i.get_gender() == 'Female' and room_num in schedule_maker.MALE_ROOMS:
            hc6_gender += 1
        if sec_i.get_gender() == 'Male' and room_num in schedule_maker.FEMALE_ROOMS:
            hc6_gender += 1

        # HC7: Prerequisite overlap check.
        prereqs = sec_i.get_course().get_prerequisites()
        if prereqs:
            my_key = (ci.get_day().get_day(), ci.get_meetingTime().get_time())
            for prereq_id in prereqs:
                if prereq_id in course_schedule_map:
                    if my_key in course_schedule_map[prereq_id]:
                        hc7_prereq += 1

        # Pairwise checks for HC1, HC2, HC3.
        for j in range(i + 1, len(classes)):
            cj    = classes[j]
            sec_j = cj.get_section()
            same_slot = (ci.get_meetingTime() == cj.get_meetingTime() and
                         ci.get_day()         == cj.get_day())
            if same_slot:
                if ci.get_instructor() == cj.get_instructor():
                    hc1_instructor += 1
                if ci.get_room() == cj.get_room():
                    hc2_room += 1
                if (sec_i.get_level()  == sec_j.get_level() and
                    sec_i.get_gender() == sec_j.get_gender() and
                    sec_i.get_course().get_id() != sec_j.get_course().get_id() and
                    not (sec_i.get_is_lab() and sec_j.get_is_lab())):
                    hc3_student += 1

    # Compute totals. Soft conflicts are inferred as the remainder after removing
    # hard conflict weight from the total conflict score.
    total_hard = hc1_instructor + hc2_room + hc3_student + hc4_capacity + hc5_prayer + hc6_gender + hc7_prereq
    total_soft = max(0, schedule.get_numbOfConflicts() - total_hard)

    # Print the breakdown for console visibility during optimization runs.
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


# ============================================================
# ImamuData -- XML Parser for IMAMU University Data
# ============================================================
# This class is IDENTICAL to the one in GA_runner.py. Both runners need to parse
# the same university.xml file to load rooms, instructors, students, courses, and
# sections. It extends the base Data class from schedule_maker.py.
#
# The XML structure expected:
#   <University>
#     <TimeSlots>
#       <WorkingDays> <Day code="SUN"/> ... </WorkingDays>
#       <DailyPeriods> <Period type="Class|Prayer"> ... </DailyPeriods>
#     </TimeSlots>
#     <Campus> <Building gender="Male|Female"> <Rooms> ... </Rooms> </Building> </Campus>
#     <Faculty> <Department> <Professor> ... </Professor> </Department> </Faculty>
#     <Students> <Department> <Level> <Student> ... </Student> </Level> </Department> </Students>
#     <AcademicProgram> <Department> <Course> <Sections> ... </Sections> </Course> </Department> </AcademicProgram>
#   </University>
# ============================================================
class ImamuData(Data):
    def __init__(self, xml_file, max_sections=None, max_rooms=None):
        """
        Parse the IMAMU university XML file and populate all scheduling data.

        Args:
            xml_file:     Filename of the XML data file (looked up relative to this script).
            max_sections: Optional cap on the number of sections to load (for testing).
            max_rooms:    Optional cap on rooms (currently unused in loader but reserved).

        Populates (inherited from Data):
            self._days, self._meetingTimes, self._rooms, self._instructors,
            self._courses, self._sections, self._depts

        Also sets module-level constants in schedule_maker:
            SLOT_ORDER, PRAYER_SLOTS, CONSECUTIVE_PAIRS, MALE_ROOMS, FEMALE_ROOMS
        """
        super().__init__()
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), xml_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Not found: {path}")
        root = ET.parse(path).getroot()
        # Load data in dependency order: times/days first (needed by scheduling),
        # then rooms, instructors, students, courses+sections, and finally departments.
        self._load_times_and_days(root)
        self._load_rooms(root, max_rooms)
        self._load_instructors(root)
        self._student_enrolled = self._load_students(root)
        self._load_courses_and_sections(root, max_sections)
        self._build_depts()
        self._validate()

    def _load_times_and_days(self, root):
        """
        Parse working days and daily time periods from the XML TimeSlots element.

        Populates:
            self._days           -- list of Day objects (e.g., SUN, MON, TUE, ...)
            self._meetingTimes   -- list of MeetingTime objects (class periods only)
            schedule_maker.SLOT_ORDER          -- {time_label: integer_index}
            schedule_maker.PRAYER_SLOTS        -- set of prayer time labels (blocked)
            schedule_maker.CONSECUTIVE_PAIRS   -- list of (slot_a, slot_b) valid back-to-back pairs
        """
        ts = root.find('TimeSlots')
        # Parse working days: each <Day> element has a 'code' attribute or text content.
        for d in ts.find('WorkingDays').findall('Day'):
            self._days.append(Day(d.get('code', d.text.strip()[:3].upper())))

        # Parse daily periods. Prayer periods are recorded but excluded from scheduling.
        # Class periods get sequential integer indices in SLOT_ORDER for gap calculations.
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

        # Build consecutive pairs: two adjacent Class periods (no Prayer in between)
        # form a valid pair for scheduling 3-credit courses (2 consecutive + 1 separate).
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

        print(f"Days       : {[d.get_day() for d in self._days]}")
        print(f"Slots      : {len(self._meetingTimes)} class slots (from XML)")
        print(f"Prayer     : {' | '.join(prayer_labels)}")
        print(f"Consecutive: {len(consecutive)} valid pairs")

    def _load_rooms(self, root, max_rooms=None):
        """
        Parse campus buildings and rooms from XML.

        Each building has a gender attribute. Rooms and labs within it inherit that gender.
        Populates self._rooms and the module-level MALE_ROOMS / FEMALE_ROOMS lists,
        which are used by HC6 (gender separation constraint) in the fitness function.
        """
        for bldg in root.find('Campus').findall('Building'):
            gender   = bldg.get('gender', '')
            rooms_el = bldg.find('Rooms')
            if rooms_el is None:
                continue
            for r in rooms_el.findall('Room') + rooms_el.findall('Lab'):
                rid  = r.get('id')
                cap  = int(r.get('capacity', 30))
                room = Room(rid, cap, gender)
                self._rooms.append(room)
                if gender == 'Male':
                    schedule_maker.MALE_ROOMS.append(rid)
                else:
                    schedule_maker.FEMALE_ROOMS.append(rid)
        print(f"Rooms : {len(self._rooms)} "
              f"({len(schedule_maker.MALE_ROOMS)} M / {len(schedule_maker.FEMALE_ROOMS)} F)")

    def _load_instructors(self, root):
        """
        Parse faculty/professor data from XML.

        Each professor has: id, name, gender, department, and TeachingHours.
        TeachingHours defines both min and max hours (set equal here).
        The _min_load (minimum sections) is computed later in _load_courses_and_sections
        once average credits per course is known.
        """
        for grp in root.find('Faculty'):
            for p in grp.findall('Professor'):
                # Name can be in <n> or <Name> element (XML format varies).
                n_el = p.find('n') or p.find('Name')
                name = n_el.text.strip() if n_el is not None else p.get('id')
                inst = Instructor(p.get('id'), name)
                inst._gender     = p.get('gender', 'Male')
                inst._department = p.get('department', '')

                # Teaching hours from XML; defaults to 12 if not specified.
                th = p.find('TeachingHours')
                inst._min_hours = int(th.text.strip()) if th is not None else 12
                inst._max_hours = inst._min_hours
                inst._max_load = inst._max_hours
                inst._min_load = 0  # Will be computed after courses are loaded.

                self._instructors.append(inst)
        print(f"Instructors: {len(self._instructors)} (hours-based load)")

    def _load_students(self, root):
        """
        Parse student enrollment data from XML.

        Builds a dictionary: (dept_name, gender, student_id) -> set of completed course IDs.
        This is used by _compute_enrolled() to determine how many students are eligible
        for each course section (those who haven't completed it yet).

        Returns:
            dict mapping (dept, gender, sid) to set of completed course ID strings.
            Empty dict if no <Students> element exists in the XML.
        """
        students_el = root.find('Students')
        if students_el is None:
            return {}

        student_data = {}   # (dept, gender, sid) -> set(completed course IDs)

        for dept_el in students_el.findall('Department'):
            dept_name = dept_el.get('name')
            for lv_el in dept_el.findall('Level'):
                for s_el in lv_el.findall('Student'):
                    gender  = s_el.get('gender')
                    sid     = s_el.get('id')
                    comp_el = s_el.find('CompletedCourses')
                    completed = set(comp_el.text.split(',')) if (comp_el is not None and comp_el.text) else set()
                    student_data[(dept_name, gender, sid)] = completed

        total = len(student_data)
        print(f"Students: {total} loaded")
        return student_data

    def _compute_enrolled(self, dept_name, gender, course_id):
        """
        Count the number of students eligible for a course (have NOT completed it).

        Args:
            dept_name: Department name to filter students by.
            gender:    Gender to filter students by (IMAMU is gender-segregated).
            course_id: The course ID to check against completed courses.

        Returns:
            Integer count of eligible students, or None if no student data is loaded.
        """
        if not self._student_enrolled:
            return None
        return sum(
            1 for (d, g, sid), completed in self._student_enrolled.items()
            if d == dept_name and g == gender and course_id not in completed
        )

    def _load_courses_and_sections(self, root, max_sections):
        """
        Parse courses and their sections from the AcademicProgram XML element.

        This method:
          1. Builds a prerequisite map (course_id -> [prereq_ids]).
          2. Creates Course objects for each course in each department.
          3. Loads sections in round-robin order across courses (one section per course
             per round) to ensure balanced coverage when max_sections caps the total.
          4. Assigns eligible instructors to each section based on gender and department.
          5. Computes enrolled count from student data (or defaults to 20 per section).
          6. After all sections are loaded, computes instructor _min_load based on
             average credits per course.

        Args:
            root:         XML root element.
            max_sections: Optional integer cap on total sections to load.
        """
        # Separate instructors by gender for assignment.
        male_inst   = [i for i in self._instructors if i._gender == 'Male']
        female_inst = [i for i in self._instructors if i._gender == 'Female']
        count       = 0

        # Phase 1: Build prerequisite map for all courses.
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

        # Phase 2: Create Course objects and collect section XML elements.
        all_courses_data = []
        for dept_el in root.find('AcademicProgram').findall('Department'):
            dept_name = dept_el.get('name', 'Unknown')
            for course_el in dept_el.findall('Course'):
                cid     = course_el.get('id')
                level   = int(course_el.get('level', 1))
                credits = int(course_el.get('credits', 3))
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

        # Phase 3: Round-robin section loading.
        # Each round takes one section from each course. This ensures even coverage
        # when max_sections is set (e.g., 60 sections spread across all courses
        # rather than loading all sections from the first few courses).
        round_idx = 0
        while max_sections is None or count < max_sections:
            added_this_round = 0
            for dept_name, course_obj, sections in all_courses_data:
                if max_sections and count >= max_sections:
                    break
                if round_idx >= len(sections):
                    continue  # This course has no more sections to add.
                sec_el     = sections[round_idx]
                sec_id     = sec_el.get('id')
                sec_gender = sec_el.get('gender', 'Male')
                cap_el     = sec_el.find('Capacity')
                capacity   = int(cap_el.text.strip()) if cap_el is not None else 40

                # Compute enrolled count from actual student data.
                # Falls back to 20 if no student data; splits evenly across same-gender sections.
                total_eligible = self._compute_enrolled(dept_name, sec_gender, course_obj.get_id())
                num_secs       = max(1, sum(1 for s in sections if s.get('gender') == sec_gender))
                enrolled       = max(5, (total_eligible or 20) // num_secs)
                enrolled       = min(enrolled, capacity)

                # Build the pool of eligible instructors:
                # 1st choice: same gender + same department.
                # 2nd choice: same gender, any department.
                # 3rd choice: any instructor (last resort).
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
                added_this_round += 1

            if added_this_round == 0:
                break  # All courses exhausted their sections.
            round_idx += 1

        print(f"Courses : {len(self._courses)} (with prerequisites)")
        print(f"Sections: {len(self._sections)}")

        # Phase 4: Compute instructor minimum section load from teaching hours.
        # min_load = floor(min_hours / avg_credits_per_course).
        # This feeds into HC8 (instructor min load) in the default fitness function.
        if self._instructors and self._courses:
            avg_credits = sum(c.get_credits() for c in self._courses) / len(self._courses)
            for inst in self._instructors:
                inst._min_load = max(0, int(inst._min_hours / avg_credits))
            print(f"Min loads: hours-based (avg_credits={avg_credits:.1f})")

    def _build_depts(self):
        """
        Group loaded sections into Department objects by (department_name, gender).

        Each Department represents a gender-specific subset of a department's sections.
        E.g., "CS_M" for male CS sections, "CS_F" for female CS sections.
        The Schedule.initialize() method iterates over departments to build SectionGroups.
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
        Sanity check: verify that the number of sections does not exceed the total
        available time-room slots (rooms x time_slots x days). If it does, scheduling
        is physically impossible and a warning is printed.
        """
        total_slots = len(self._rooms) * len(self._meetingTimes) * len(self._days)
        ok = "OK" if len(self._sections) <= total_slots else "WARNING: OVERLOADED"
        print(f"Slots   : {total_slots} -> {ok}\n")


# ============================================================
# PSO Runner -- Core Discrete Particle Swarm Optimization
# ============================================================
def _run_pso(label, objective, data, swarm_size, mutation_rate, top_n=1):
    """
    Run Particle Swarm Optimization to find optimal course schedules.

    This is the PSO equivalent of _run_ga() in GA_runner.py. Instead of evolving a
    population through selection/crossover/mutation, it maintains a swarm of particles
    that each independently explore the search space guided by:
      - Their personal best known position (pbest)
      - The swarm's global best known position (gbest)
      - Random exploration

    The update is DISCRETE (not continuous): for each SectionGroup in the schedule,
    a random number determines which source to copy from. This is necessary because
    scheduling variables (rooms, instructors, times, days) are categorical -- you
    cannot meaningfully interpolate between "Room 101" and "Room 205".

    Args:
        label:         Display label for this run (e.g., "University-Optimized").
        objective:     One of OBJECTIVE_UNIVERSITY/INSTRUCTOR/STUDENT.
        data:          ImamuData instance with all university data loaded.
        swarm_size:    Number of particles in the swarm (analogous to population size in GA).
        mutation_rate: Probability of random exploration per group per iteration.
                       Controls the exploration vs. exploitation trade-off:
                       - Higher values (e.g., 0.3) = more exploration, slower convergence.
                       - Lower values (e.g., 0.05) = faster convergence, risk of local optima.
        top_n:         Number of independent runs; returns the best schedule from each.

    Returns:
        List of `top_n` Schedule objects, one from each independent PSO run.
    """
    # Set the active optimization objective (affects which soft constraints are used).
    global CURRENT_OBJECTIVE
    CURRENT_OBJECTIVE = objective
    # Inject data into schedule_maker so that Schedule() constructors can access it.
    schedule_maker.data = data

    # Stopping criteria (same thresholds as GA_runner.py for fair comparison):
    MAX_STAGNATION = 3000   # Stop if no improvement for 3000 consecutive generations.
    MAX_TIME       = 1000   # Stop after 1000 seconds (~16.7 minutes).
    MAX_GENS       = 10000  # Absolute generation limit.

    print(f"\n{'='*60}")
    print(f"  {label} (PSO) (top {top_n})")
    print(f"{'='*60}")

    best_schedules = []

    # Each run is independent: fresh swarm, fresh pbest/gbest tracking.
    # Multiple runs (top_n) produce multiple candidate schedules for comparison.
    for run in range(top_n):
        # ---------------------------------------------------------------
        # STEP 1: Initialize the swarm.
        # Each particle is a complete random Schedule (all sections assigned
        # random rooms, instructors, days, and time slots via Schedule.initialize()).
        # ---------------------------------------------------------------
        particles = [schedule_maker.Schedule().initialize() for _ in range(swarm_size)]

        # ---------------------------------------------------------------
        # STEP 2: Initialize personal bests (pbest) for each particle.
        #
        # extract_group_state() captures the current assignment of every
        # SectionGroup as a list of dictionaries:
        #   [ [{'room': R, 'instructor': I, 'time': T, 'day': D}, ...], ... ]
        # Each outer element corresponds to one SectionGroup; each inner
        # element corresponds to one ClassSlot within that group.
        #
        # Since particles just initialized, their current state IS their best.
        # ---------------------------------------------------------------
        pbest_states = [p.extract_group_state() for p in particles]
        pbest_fitnesses = [p.get_fitness() for p in particles]

        # ---------------------------------------------------------------
        # STEP 3: Initialize global best (gbest).
        # Scan all particles to find the one with the highest initial fitness.
        # ---------------------------------------------------------------
        gbest_state = None
        gbest_fitness = -1.0

        for i, p in enumerate(particles):
            fit = pbest_fitnesses[i]
            if fit > gbest_fitness:
                gbest_fitness = fit
                gbest_state = pbest_states[i]

        # Generation counter and stagnation tracker.
        gen = 0
        stagnation = 0
        last_best_f = -1.0
        t0 = time.time()

        print(f"  Run {run+1}/{top_n}")

        # ---------------------------------------------------------------
        # MAIN PSO LOOP
        # Runs until an optimal schedule is found (fitness = 1.0) or a
        # stopping criterion is met (stagnation, time, or generation limit).
        # ---------------------------------------------------------------
        while gbest_fitness < 1.0:
            gen += 1

            # -----------------------------------------------------------
            # STEP 4: Update every particle's position.
            # This is the core PSO logic, adapted for discrete scheduling.
            # -----------------------------------------------------------
            for i, p in enumerate(particles):
                # Snapshot the particle's current state (not directly used in
                # the update formula, but extract is needed for structural
                # compatibility -- we know how many groups exist).
                current_state = p.extract_group_state()
                new_state = []

                # Create a fresh random schedule to serve as the "exploration"
                # source. This is the discrete PSO equivalent of adding a random
                # velocity component -- it introduces completely new genetic
                # material that neither pbest nor gbest contain.
                rand_sch = schedule_maker.Schedule().initialize()
                rand_state = rand_sch.extract_group_state()

                # ---------------------------------------------------
                # DISCRETE PSO UPDATE (group-by-group)
                #
                # For each SectionGroup index g_idx, we probabilistically
                # choose ONE source to copy the entire group's assignment from:
                #
                #   r in [0, mutation_rate):
                #       EXPLORATION -- copy from random schedule.
                #       Analogous to the inertia/random velocity term in
                #       continuous PSO. Prevents premature convergence by
                #       injecting diversity.
                #
                #   r in [mutation_rate, mutation_rate + (1-mutation_rate)/2):
                #       COGNITIVE FACTOR -- copy from this particle's pbest.
                #       "Remember what worked well for me before."
                #       Analogous to c1 * r1 * (pbest - x) in standard PSO.
                #
                #   r in [mutation_rate + (1-mutation_rate)/2, 1.0):
                #       SOCIAL FACTOR -- copy from gbest.
                #       "Follow the swarm's best-known solution."
                #       Analogous to c2 * r2 * (gbest - x) in standard PSO.
                #
                # The probability split gives equal weight to cognitive and
                # social factors: each gets (1 - mutation_rate) / 2.
                #
                # Example with mutation_rate = 0.2:
                #   20% random exploration
                #   40% cognitive (personal best)
                #   40% social (global best)
                # ---------------------------------------------------
                for g_idx in range(len(current_state)):
                    r = random.random()

                    if r < mutation_rate:
                        # Exploration factor (Randomization):
                        # Take this group's assignment from a brand-new random schedule.
                        new_state.append(rand_state[g_idx])
                    elif r < mutation_rate + (1.0 - mutation_rate) / 2.0:
                        # Cognitive factor (Personal Best / pbest):
                        # Revert this group to whatever assignment gave this
                        # particle its best-ever fitness.
                        new_state.append(pbest_states[i][g_idx])
                    else:
                        # Social factor (Global Best / gbest):
                        # Copy this group's assignment from the best schedule
                        # any particle in the swarm has ever found.
                        new_state.append(gbest_state[g_idx])

                # ---------------------------------------------------
                # Apply the assembled state back to the particle's Schedule.
                #
                # apply_group_state() iterates over each SectionGroup and each
                # ClassSlot within it, setting room, instructor, time, and day
                # from the state dictionary. It also calls flag_state_changed()
                # to ensure the next get_fitness() call triggers a full
                # recalculation of the fitness function.
                # ---------------------------------------------------
                p.apply_group_state(new_state)
                fit = p.get_fitness() # Recalculates fitness on the new state.

                # Update Personal Best: if this new position is better than
                # anything this particle has seen before, remember it.
                if fit > pbest_fitnesses[i]:
                    pbest_fitnesses[i] = fit
                    pbest_states[i] = new_state

                # Update Global Best: if this particle just found a new
                # swarm-wide best, update gbest for all particles to follow.
                if fit > gbest_fitness:
                    gbest_fitness = fit
                    gbest_state = new_state

            # -----------------------------------------------------------
            # STEP 5: Reconstruct the global best schedule to read its
            # conflict count for logging purposes.
            # We create a temporary schedule, apply gbest_state to it, and
            # evaluate fitness to get the actual conflict numbers.
            # -----------------------------------------------------------
            _tmp = schedule_maker.Schedule().initialize()
            _tmp.apply_group_state(gbest_state)
            _tmp.get_fitness()
            best_c = _tmp.get_numbOfConflicts()

            # -----------------------------------------------------------
            # STEP 6: Stagnation detection.
            # If gbest_fitness hasn't improved since the last generation,
            # increment the stagnation counter. Reset it on any improvement.
            # -----------------------------------------------------------
            stagnation = 0 if gbest_fitness > last_best_f else stagnation + 1
            last_best_f = max(gbest_fitness, last_best_f)

            # Progress logging every 100 generations or when optimal.
            if gen % 100 == 0 or gbest_fitness >= 1.0:
                print(f"    Gen {gen:5d} | Conflicts: {best_c:6.2f} | Fitness: {gbest_fitness:.4f} | {time.time()-t0:.1f}s")

            # -----------------------------------------------------------
            # STEP 7: Check stopping criteria.
            # Same thresholds as GA_runner.py for apples-to-apples comparison.
            # -----------------------------------------------------------
            if gbest_fitness >= 1.0: break                                    # Optimal found!
            if stagnation >= MAX_STAGNATION: print("    Stopped: stagnation"); break  # No progress
            if time.time() - t0 > MAX_TIME:  print("    Stopped: time limit"); break  # Time budget
            if gen >= MAX_GENS:              print("    Stopped: max gens");   break   # Gen limit

        # ---------------------------------------------------------------
        # STEP 8: Reconstruct the final best schedule from gbest_state.
        # A new Schedule is initialized (to get the correct group structure),
        # then gbest_state is applied to set all assignments to the optimal
        # configuration found during the search.
        # ---------------------------------------------------------------
        final = schedule_maker.Schedule().initialize()
        final.apply_group_state(gbest_state)
        final.get_fitness() # Ensures internal conflict count is up-to-date.

        elapsed = time.time() - t0
        status  = "OPTIMAL ✓" if final.get_fitness() >= 1.0 else f"{final.get_numbOfConflicts():.2f} conflicts"
        print(f"    Result: {status} | Time: {elapsed:.2f}s | Gens: {gen}")
        # Print detailed hard/soft conflict breakdown for diagnostics.
        hard, soft = count_hard_soft(final)
        print(f"    Hard: {hard} | Soft: {soft:.2f}")
        best_schedules.append(final)

    return best_schedules


# ============================================================
# Main Entry Points
# ============================================================
def run_all_schedules(max_sections=None, max_rooms=None, pop_size=100, mutation_rate=0.1,
                      xml_file="university.xml", top_n=3):
    """
    Run PSO optimization for all three objectives and display results.

    This is the main orchestrator. It:
      1. Loads university data from XML via ImamuData.
      2. Runs _run_pso() three times -- once per objective (university, instructor, student).
      3. For each objective, produces `top_n` independent schedule solutions.
      4. Prints all resulting schedules as formatted tables.

    Args:
        max_sections:  Cap on sections to load (None = all).
        max_rooms:     Reserved for future use (room capping).
        pop_size:      Swarm size (number of particles). Larger swarms explore more
                       but each generation is slower.
        mutation_rate: Exploration probability per group per iteration (0.0 - 1.0).
        xml_file:      Path to the IMAMU university XML data file.
        top_n:         Number of independent PSO runs per objective (produces top_n
                       candidate schedules per objective).

    Returns:
        dict with keys "university", "instructor", "student", each mapping to a list
        of `top_n` Schedule objects.
    """
    print("\n" + "="*60)
    print(f"  IMAMU — Top {top_n} Schedules per Objective (PSO)")
    print("="*60 + "\n")

    # Load all university data (rooms, instructors, students, courses, sections).
    data = ImamuData(xml_file, max_sections=max_sections)

    # Run PSO for each of the three optimization objectives.
    results = {
        "university": _run_pso("University-Optimized", OBJECTIVE_UNIVERSITY, data, pop_size, mutation_rate, top_n),
        "instructor": _run_pso("Instructor-Optimized", OBJECTIVE_INSTRUCTOR, data, pop_size, mutation_rate, top_n),
        "student":    _run_pso("Student-Optimized",    OBJECTIVE_STUDENT,    data, pop_size, mutation_rate, top_n),
    }

    # Display all generated schedules as formatted tables.
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


# ---------------------------------------------------------------------------
# Convenience wrappers with pre-configured parameters for different run sizes.
# These mirror the same functions in GA_runner.py for easy benchmarking.
# ---------------------------------------------------------------------------
def quick_test():
    """Fast test run: 30 sections, 15 particles, high exploration (0.2). ~1-2 min."""
    return run_all_schedules(max_sections=30, pop_size=15, mutation_rate=0.2)

def medium_run():
    """Medium run: 60 sections, 30 particles, moderate exploration (0.2). ~5-10 min."""
    return run_all_schedules(max_sections=60, pop_size=30, mutation_rate=0.2)

def full_run():
    """Full production run: all sections, 100 particles, low exploration (0.1). ~15-30 min."""
    return run_all_schedules(pop_size=100, mutation_rate=0.1)


if __name__ == "__main__":
    medium_run()