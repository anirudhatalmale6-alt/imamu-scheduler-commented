"""
xml_import.py -- University Data Importer from XML
====================================================

This module parses the university.xml file and populates the SQLite database
with the seed data required by the IMAMU Course Scheduler. It is called once
at application startup when the database is empty (Day table has zero rows).

The university.xml file is the canonical data source for:
    - Working days of the week (e.g., Sunday through Thursday).
    - Daily time periods / slots (class periods and prayer breaks).
    - Campus buildings, rooms, and laboratories with capacities and gender.
    - Faculty members / instructors with workload constraints.
    - Academic departments, courses, sections, and prerequisite relationships.

Import order matters because of foreign key dependencies:
    1. Days and TimeSlots  (no FK dependencies)
    2. Rooms               (no FK dependencies)
    3. Instructors          (no FK dependencies)
    4. Departments, Courses, and Sections  (Course -> Department, Section -> Course)
    5. Prerequisites        (Course -> Course, requires all courses to exist first)

Architecture note:
    Each helper function (_import_days_and_timeslots, _import_rooms, etc.)
    handles one section of the XML tree. They all use flush() (not commit())
    to assign auto-increment IDs within the same transaction. The final
    commit() is done in the top-level import_university_xml() function.
"""

import xml.etree.ElementTree as ET
import os
from sqlalchemy.orm import Session
from app.models.models import (
    Department, Course, Section, Instructor, Room, Day, TimeSlot,
)


def import_university_xml(db: Session, xml_path: str):
    """
    Main entry point: parse the university XML file and import all data.

    This function orchestrates the full import by calling specialized
    helper functions in the correct dependency order. Each helper
    reads a specific section of the XML tree and inserts the corresponding
    ORM objects into the database session.

    Args:
        db:       An active SQLAlchemy session (not yet committed).
        xml_path: Absolute filesystem path to the university.xml file.

    Side effects:
        - Inserts rows into days, time_slots, rooms, instructors,
          departments, courses, sections, and course_prerequisites tables.
        - Calls db.commit() to persist all changes.
        - Prints progress messages to stdout for server log monitoring.
    """
    # Guard: verify the XML file exists before attempting to parse
    if not os.path.exists(xml_path):
        print(f"XML file not found: {xml_path}")
        return

    # Parse the XML file into an ElementTree and get the root element
    root = ET.parse(xml_path).getroot()

    # Import each category of data in dependency order
    _import_days_and_timeslots(db, root)       # Step 1: Days + time periods
    _import_rooms(db, root)                     # Step 2: Buildings + rooms
    _import_instructors(db, root)               # Step 3: Faculty members
    dept_map = _import_courses_and_sections(db, root)  # Step 4+5: Depts, courses, sections, prerequisites

    # Commit all inserts in a single transaction
    db.commit()
    print("XML import complete.")


def _import_days_and_timeslots(db: Session, root):
    """
    Import working days and daily time periods from the <TimeSlots> XML section.

    XML structure expected:
        <TimeSlots>
          <WorkingDays>
            <Day code="SUN">Sunday</Day>
            ...
          </WorkingDays>
          <DailyPeriods>
            <Period type="Class">
              <StartTime>08:00</StartTime>
              <EndTime>08:50</EndTime>
            </Period>
            <Period type="Break">...</Period>
            ...
          </DailyPeriods>
        </TimeSlots>

    Args:
        db:   Active SQLAlchemy session.
        root: Root element of the parsed XML tree.
    """
    # Locate the <TimeSlots> section in the XML
    ts = root.find("TimeSlots")
    if ts is None:
        return  # XML has no TimeSlots section; skip

    # --- Import Working Days ---
    # Each <Day> element has a 'code' attribute and text content for the full name
    for d_el in ts.find("WorkingDays").findall("Day"):
        # Extract the day code (e.g., "SUN"); fall back to first 3 chars of text
        code = d_el.get("code", d_el.text.strip()[:3].upper())
        # Extract the full day name from the element text
        name = d_el.text.strip() if d_el.text else code

        # Only insert if this day code does not already exist (idempotent)
        if not db.query(Day).filter(Day.code == code).first():
            db.add(Day(code=code, name=name))

    # Flush to assign auto-increment IDs (needed for later FK references)
    db.flush()

    # --- Import Daily Time Periods ---
    # slot_order tracks the chronological position of "Class" type periods only
    # (prayer breaks are excluded from the ordering used by the scheduler)
    slot_order = 0
    for p_el in ts.find("DailyPeriods").findall("Period"):
        # Extract start and end times from child elements
        start = p_el.find("StartTime").text.strip()
        end = p_el.find("EndTime").text.strip()

        # Create a human-readable label combining start and end times
        label = f"{start}-{end}"

        # Determine the period type: "Class" for teaching, "Break" for prayer/lunch
        slot_type = p_el.get("type", "Class")

        # Only insert if this time slot label does not already exist
        if not db.query(TimeSlot).filter(TimeSlot.label == label).first():
            db.add(TimeSlot(
                label=label,
                start_time=start,
                end_time=end,
                slot_type=slot_type,
                slot_order=slot_order,
            ))

        # Increment the ordering counter only for class periods
        if slot_type == "Class":
            slot_order += 1

    db.flush()
    print(f"Days: {db.query(Day).count()}, TimeSlots: {db.query(TimeSlot).count()}")


def _import_rooms(db: Session, root):
    """
    Import campus buildings, classrooms, and laboratories from the <Campus> XML section.

    XML structure expected:
        <Campus>
          <Building name="Building A" gender="Male">
            <Rooms>
              <Room id="A-101" capacity="40" type="Lecture Hall" floor="1"/>
              <Lab id="A-L01" capacity="30" type="Lab" floor="1" equipment="Computers"/>
            </Rooms>
          </Building>
          ...
        </Campus>

    Args:
        db:   Active SQLAlchemy session.
        root: Root element of the parsed XML tree.
    """
    # Locate the <Campus> section
    campus = root.find("Campus")
    if campus is None:
        return  # No campus data in the XML

    # Iterate over each <Building> in the campus
    for bldg in campus.findall("Building"):
        # Building-level gender designation (applies to all rooms inside)
        gender = bldg.get("gender", "")
        bldg_name = bldg.get("name", "")

        # Find the <Rooms> container within this building
        rooms_el = bldg.find("Rooms")
        if rooms_el is None:
            continue  # Building has no rooms defined

        # Process both <Room> and <Lab> elements (they share the same attributes)
        for r_el in list(rooms_el.findall("Room")) + list(rooms_el.findall("Lab")):
            rid = r_el.get("id")  # Unique room identifier

            # Only insert if this room ID does not already exist
            if not db.query(Room).filter(Room.room_id == rid).first():
                db.add(Room(
                    room_id=rid,
                    capacity=int(r_el.get("capacity", 40)),
                    gender=gender,                            # Inherited from building
                    room_type=r_el.get("type", "Lecture Hall"),
                    floor=int(r_el.get("floor", 1)),
                    building=bldg_name,
                    equipment=r_el.get("equipment", ""),
                ))

    db.flush()
    print(f"Rooms: {db.query(Room).count()}")


def _import_instructors(db: Session, root):
    """
    Import faculty members from the <Faculty> XML section.

    XML structure expected:
        <Faculty>
          <Group>
            <Professor id="P001" gender="Male" department="Computer Science">
              <Name>Dr. Ahmed</Name>
              <Rank>Professor</Rank>
              <MinTeachingHours>12</MinTeachingHours>
              <MaxTeachingHours>18</MaxTeachingHours>
            </Professor>
            ...
          </Group>
        </Faculty>

    Args:
        db:   Active SQLAlchemy session.
        root: Root element of the parsed XML tree.
    """
    # Locate the <Faculty> section
    faculty = root.find("Faculty")
    if faculty is None:
        return  # No faculty data in the XML

    # Faculty may be organized into <Group> elements (by department, rank, etc.)
    for group in faculty:
        # Each <Professor> element represents one instructor
        for p_el in group.findall("Professor"):
            pid = p_el.get("id")  # Unique instructor ID

            # Skip if this instructor already exists (idempotent import)
            if db.query(Instructor).filter(Instructor.instructor_id == pid).first():
                continue

            # Extract instructor details from child elements
            n_el = p_el.find("Name")
            name = n_el.text.strip() if n_el is not None else pid  # Fall back to ID

            rank_el = p_el.find("Rank")
            min_h = p_el.find("MinTeachingHours")
            max_h = p_el.find("MaxTeachingHours")

            db.add(Instructor(
                instructor_id=pid,
                name=name,
                gender=p_el.get("gender", "Male"),
                department=p_el.get("department", ""),
                rank=rank_el.text.strip() if rank_el is not None else "",
                min_hours=int(min_h.text.strip()) if min_h is not None else 12,  # Default 12h/week
                max_hours=int(max_h.text.strip()) if max_h is not None else 18,  # Default 18h/week
            ))

    db.flush()
    print(f"Instructors: {db.query(Instructor).count()}")


def _import_courses_and_sections(db: Session, root):
    """
    Import departments, courses, course sections, and prerequisites from
    the <AcademicProgram> XML section.

    This is the most complex import step because it handles:
    1. Department creation (with auto-generated short codes).
    2. Course creation (with metadata: level, credits, lab flag).
    3. Section creation (gender-segregated offerings of each course).
    4. Prerequisite linking (self-referential M2M on Course).

    Prerequisites are handled in a second pass after all courses are inserted,
    because a prerequisite course might be defined later in the XML than the
    course that requires it.

    XML structure expected:
        <AcademicProgram>
          <Department name="Computer Science">
            <Course id="CS201" level="3" credits="3">
              <Name>Data Structures</Name>
              <Type>Lecture</Type>
              <Prerequisites>CS101, CS102</Prerequisites>
              <Sections>
                <Section id="CS201-M1" gender="Male">
                  <Capacity>40</Capacity>
                </Section>
              </Sections>
            </Course>
            ...
          </Department>
        </AcademicProgram>

    Args:
        db:   Active SQLAlchemy session.
        root: Root element of the parsed XML tree.

    Returns:
        dict: Mapping of department name -> Department ORM object.
    """
    # Locate the <AcademicProgram> section
    program = root.find("AcademicProgram")
    if program is None:
        return {}

    # Dictionary to cache created Department objects by name
    dept_map = {}

    # --- First pass: Create departments, courses, and sections ---
    for dept_el in program.findall("Department"):
        dept_name = dept_el.get("name", "Unknown")

        # Auto-generate a short department code from the department name
        # Uses known mappings for common IMAMU departments
        dept_code = dept_name[:2].upper() + "S" if "Systems" in dept_name else dept_name[:2].upper()
        if dept_name == "Computer Science":
            dept_code = "CS"
        elif dept_name == "Information Technology":
            dept_code = "IT"
        elif dept_name == "Information Systems":
            dept_code = "IS"

        # Create the department if it doesn't already exist
        dept = db.query(Department).filter(Department.name == dept_name).first()
        if not dept:
            dept = Department(name=dept_name, code=dept_code)
            db.add(dept)
            db.flush()  # Flush to get the auto-generated dept.id
        dept_map[dept_name] = dept

        # Process each <Course> within this department
        for course_el in dept_el.findall("Course"):
            cid = course_el.get("id")  # Course code (e.g., "CS371")

            # Skip if this course code already exists (idempotent)
            if db.query(Course).filter(Course.code == cid).first():
                continue

            # Extract course name from child <Name> element
            n_el = course_el.find("Name")
            cname = n_el.text.strip() if n_el is not None else cid

            # Determine if this is a lab course by checking the <Type> element
            t_el = course_el.find("Type")
            is_lab = t_el is not None and "lab" in t_el.text.lower()

            # Create the Course ORM object
            course = Course(
                code=cid,
                name=cname,
                level=int(course_el.get("level", 1)),
                credits=int(course_el.get("credits", 3)),
                is_lab=is_lab,
                department_id=dept.id,  # FK to the department created above
            )
            db.add(course)
            db.flush()  # Flush to get the auto-generated course.id

            # Process sections for this course
            secs_el = course_el.find("Sections")
            if secs_el is None:
                continue  # Some courses may not have sections defined

            for sec_el in secs_el.findall("Section"):
                sid = sec_el.get("id")  # Section identifier (e.g., "CS371-M1")

                # Skip if this section ID already exists
                if db.query(Section).filter(Section.section_id == sid).first():
                    continue

                # Extract capacity from child <Capacity> element
                cap_el = sec_el.find("Capacity")
                capacity = int(cap_el.text.strip()) if cap_el is not None else 40

                db.add(Section(
                    section_id=sid,
                    gender=sec_el.get("gender", "Male"),  # Section gender designation
                    capacity=capacity,
                    enrolled=0,                             # Starts with zero enrollment
                    course_id=course.id,                    # FK to parent course
                ))

    db.flush()

    # --- Second pass: Link prerequisites ---
    # This must happen after ALL courses are inserted, because a prerequisite
    # course (e.g., CS101) might appear in a different department or later
    # in the XML than the course that requires it (e.g., CS201).
    for dept_el in program.findall("Department"):
        for course_el in dept_el.findall("Course"):
            # Check if this course has a <Prerequisites> element
            pre_el = course_el.find("Prerequisites")
            if pre_el is None or not pre_el.text or pre_el.text.strip().lower() == "none":
                continue  # No prerequisites defined

            # Look up the course that has these prerequisites
            cid = course_el.get("id")
            course = db.query(Course).filter(Course.code == cid).first()
            if not course:
                continue  # Course not found (shouldn't happen after first pass)

            # Parse the comma-separated list of prerequisite course codes
            prereq_codes = [p.strip() for p in pre_el.text.strip().split(",")]

            # Link each prerequisite course to this course via the M2M relationship
            for pc in prereq_codes:
                prereq = db.query(Course).filter(Course.code == pc).first()
                if prereq and prereq not in course.prerequisites:
                    course.prerequisites.append(prereq)

    db.flush()

    # Print summary statistics for the import
    print(f"Departments: {db.query(Department).count()}")
    print(f"Courses: {db.query(Course).count()}")
    print(f"Sections: {db.query(Section).count()}")

    return dept_map
