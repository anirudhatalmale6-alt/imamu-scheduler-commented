/**
 * StudentDashboard.js
 *
 * Main dashboard view for students in the IMAMU Course Scheduler application.
 * This single-page component manages the entire student workflow:
 *
 *   1. Browse Courses  - View and filter the full course catalog; register or
 *                        drop individual sections (gender-filtered).
 *   2. My Registrations - Summary of courses the student is currently enrolled
 *                         in, with credit totals.
 *   3. Generate Schedule - Choose an optimization algorithm (GA / PSO) and
 *                          objective (student / instructor / university / all),
 *                          then request an optimized timetable from the backend.
 *   4. View Results     - Inspect the generated schedule(s), toggle between a
 *                         personal "My Schedule" view (greedy best-section
 *                         selection) and the full university schedule, and save
 *                         preferred results.
 *   5. Saved Schedules  - Retrieve and manage previously saved schedules.
 *
 * The component uses a sidebar navigation pattern where `activePage` selects
 * which page is rendered inside the <main> area.
 *
 * Key algorithm note:
 *   `getFilteredSlots` implements a greedy best-section selector that, for each
 *   registered course, picks the section with the fewest time conflicts against
 *   already-committed sections.  This gives the student a clean personal view
 *   even when the backend result contains the full university timetable.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { courseAPI, registrationAPI, scheduleAPI, dataAPI } from '../services/api';
import ScheduleCalendar from '../components/ScheduleCalendar';

export default function StudentDashboard() {
  /* ──────────────────────────────────────────────
   * Authentication context
   * `user` contains at minimum: full_name, gender
   * `logout` signs the student out and redirects
   * ────────────────────────────────────────────── */
  const { user, logout } = useAuth();

  /* ──────────────────────────────────────────────
   * State variables
   * ────────────────────────────────────────────── */

  /** Which sidebar page is active: 'courses' | 'registered' | 'generate' | 'schedule' | 'saved' */
  const [activePage, setActivePage] = useState('courses');

  /** Full list of courses fetched from the backend (includes nested sections per course) */
  const [courses, setCourses] = useState([]);

  /** The current student's registrations (course + section info for each enrollment) */
  const [registrations, setRegistrations] = useState([]);

  /** List of academic departments, used to populate the department filter dropdown */
  const [departments, setDepartments] = useState([]);

  /**
   * Array of schedule result objects returned by the backend after generation.
   * Each result contains: rank, label, description, fitness, conflicts, objective,
   * and a `slots` array that feeds into ScheduleCalendar.
   */
  const [scheduleResults, setScheduleResults] = useState([]);

  /** Previously saved schedules retrieved from the backend (persisted by the user) */
  const [savedSchedules, setSavedSchedules] = useState([]);

  /** Index of the currently viewed tab within `scheduleResults` (0-based) */
  const [selectedTab, setSelectedTab] = useState(0);

  /** True while an async schedule generation request is in flight; shows the loading overlay */
  const [generating, setGenerating] = useState(false);

  /** Selected optimization algorithm: 'GA' (Genetic Algorithm) or 'PSO' (Particle Swarm Optimization) */
  const [algorithm, setAlgorithm] = useState('GA');

  /** Optimization objective: 'student' | 'instructor' | 'university' | 'all' */
  const [objective, setObjective] = useState('student');

  /** Department filter value for the Browse Courses page (empty string = all departments) */
  const [filterDept, setFilterDept] = useState('');

  /** Level filter value for the Browse Courses page (empty string = all levels) */
  const [filterLevel, setFilterLevel] = useState('');

  /**
   * Available class time slots fetched from the backend.
   * Filtered to slot_type === 'Class' (excludes exam slots, breaks, etc.).
   * Passed to ScheduleCalendar to define the row headers of the weekly grid.
   */
  const [timeSlots, setTimeSlots] = useState([]);

  /** Transient feedback/error message displayed at the top of the main content area */
  const [msg, setMsg] = useState('');

  /**
   * Toggle between the student's personal schedule (true) and the full
   * university-wide schedule (false).  When true, `getFilteredSlots` applies
   * the greedy best-section selection algorithm to show only the student's
   * registered courses with optimal section picks.
   */
  const [myCoursesOnly, setMyCoursesOnly] = useState(true);

  /* ──────────────────────────────────────────────
   * Data loading
   * ────────────────────────────────────────────── */

  /**
   * loadData - Fetches all primary data required by the dashboard in parallel.
   *
   * Four API calls are issued concurrently via Promise.all:
   *   1. courseAPI.list()              - all courses with nested sections
   *   2. registrationAPI.myRegistrations() - this student's current registrations
   *   3. dataAPI.departments()        - department lookup list (for filter dropdown)
   *   4. dataAPI.timeslots()          - time slot definitions (filtered to 'Class' type)
   *
   * Called once on mount (via useEffect) and again after any register/unregister
   * action so that enrollment counts and registration state stay fresh.
   */
  const loadData = useCallback(async () => {
    try {
      const [coursesRes, regRes, deptRes, tsRes] = await Promise.all([
        courseAPI.list(),
        registrationAPI.myRegistrations(),
        dataAPI.departments(),
        dataAPI.timeslots(),
      ]);
      setCourses(coursesRes.data);
      setRegistrations(regRes.data);
      setDepartments(deptRes.data);
      /* Only keep 'Class' time slots; exam and other slot types are irrelevant here */
      setTimeSlots(tsRes.data.filter(t => t.slot_type === 'Class'));
    } catch (err) { console.error(err); }
  }, []);

  /** Initial data fetch on component mount */
  useEffect(() => { loadData(); }, [loadData]);

  /**
   * loadSaved - Fetches the student's previously saved schedules from the backend.
   * Called lazily only when the "Saved Schedules" page is activated to avoid
   * unnecessary network requests on other pages.
   */
  const loadSaved = async () => {
    try {
      const res = await scheduleAPI.getSaved();
      setSavedSchedules(res.data);
    } catch (err) { console.error(err); }
  };

  /** Trigger loadSaved when the user navigates to the 'saved' page */
  useEffect(() => {
    if (activePage === 'saved') loadSaved();
  }, [activePage]);

  /* ──────────────────────────────────────────────
   * Registration handlers
   * ────────────────────────────────────────────── */

  /**
   * handleRegister - Enrolls the student in a specific section.
   *
   * @param {number} sectionId - The primary key of the section to register for.
   *
   * On success: shows a "Registered!" message and reloads data so the UI
   * reflects the updated enrollment count and registration list.
   * On failure: shows the backend error detail (e.g. "Section full", "Time conflict").
   * The feedback message auto-clears after 2-3 seconds.
   */
  const handleRegister = async (sectionId) => {
    try {
      await registrationAPI.register(sectionId);
      setMsg('Registered!');
      loadData();
      setTimeout(() => setMsg(''), 2000);
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Registration failed');
      setTimeout(() => setMsg(''), 3000);
    }
  };

  /**
   * handleUnregister - Drops the student from a specific section.
   *
   * @param {number} sectionId - The primary key of the section to drop.
   *
   * Follows the same success/error pattern as handleRegister.
   */
  const handleUnregister = async (sectionId) => {
    try {
      await registrationAPI.unregister(sectionId);
      setMsg('Unregistered');
      loadData();
      setTimeout(() => setMsg(''), 2000);
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Failed');
      setTimeout(() => setMsg(''), 3000);
    }
  };

  /* ──────────────────────────────────────────────
   * Schedule generation, saving, and deletion
   * ────────────────────────────────────────────── */

  /**
   * handleGenerate - Requests the backend to generate optimized schedule(s).
   *
   * Collects the unique course IDs from the student's registrations and sends
   * them to scheduleAPI.generate along with the chosen algorithm, objective,
   * and the student's gender (for gender-specific section filtering on the
   * backend side).
   *
   * On success: populates `scheduleResults` with the returned array of ranked
   * schedule options, resets the tab to the first result, defaults to the
   * personal "My Schedule" view, and navigates to the 'schedule' page.
   *
   * Guards:
   *   - Returns early with a message if the student has no registrations.
   *   - Sets `generating` to true during the request to show the loading overlay.
   */
  const handleGenerate = async () => {
    /* Deduplicate course IDs in case the student registered for multiple sections of the same course */
    const courseIds = [...new Set(registrations.map(r => r.course_id))];
    if (courseIds.length === 0) {
      setMsg('Please register for courses first');
      setTimeout(() => setMsg(''), 3000);
      return;
    }
    setGenerating(true);
    try {
      const res = await scheduleAPI.generate({
        selected_course_ids: courseIds,
        algorithm,
        objective: objective,
        preferred_gender: user.gender,
      });
      setScheduleResults(res.data);
      setSelectedTab(0);
      setMyCoursesOnly(true); // default to personal view after generation
      setActivePage('schedule');
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Generation failed');
      setTimeout(() => setMsg(''), 3000);
    } finally {
      setGenerating(false);
    }
  };

  /**
   * handleSave - Persists a schedule result to the backend so the student can
   * retrieve it later from the "Saved Schedules" page.
   *
   * @param {Object} result - One of the objects from `scheduleResults`.
   *
   * The saved record includes a human-readable name (label + today's date),
   * the algorithm used, objective, fitness score, conflict count, and the raw
   * slot data serialized as JSON.
   */
  const handleSave = async (result) => {
    try {
      await scheduleAPI.save({
        name: `${result.label} - ${new Date().toLocaleDateString()}`,
        algorithm,
        objective: result.objective,
        fitness: result.fitness,
        conflicts: result.conflicts,
        schedule_data: JSON.stringify(result.slots),
      });
      setMsg('Schedule saved!');
      setTimeout(() => setMsg(''), 2000);
    } catch (err) {
      setMsg('Failed to save');
      setTimeout(() => setMsg(''), 3000);
    }
  };

  /**
   * handleDeleteSaved - Deletes a previously saved schedule by its ID and
   * refreshes the saved schedules list.
   *
   * @param {number} id - The primary key of the saved schedule to delete.
   */
  const handleDeleteSaved = async (id) => {
    try {
      await scheduleAPI.deleteSaved(id);
      loadSaved();
    } catch (err) { console.error(err); }
  };

  /* ──────────────────────────────────────────────
   * Derived data (computed from state on every render)
   * ────────────────────────────────────────────── */

  /** Set of section IDs the student is currently registered for (for fast O(1) lookup) */
  const registeredSectionIds = new Set(registrations.map(r => r.section_id || r.id));

  /** Set of course IDs the student is registered for (to detect "Enrolled (other section)") */
  const registeredCourseIds = new Set(registrations.map(r => r.course_id));

  /** Set of course codes the student is registered for (used by the greedy algorithm) */
  const registeredCourseCodes = new Set(registrations.map(r => r.course_code));

  /**
   * Filtered course list for the Browse Courses page.
   * Applies the department and level dropdown filters.
   * Both filters are optional; empty string means "show all".
   */
  const filteredCourses = courses.filter(c => {
    if (filterDept && c.department_id !== parseInt(filterDept)) return false;
    if (filterLevel && c.level !== parseInt(filterLevel)) return false;
    return true;
  });

  /* ──────────────────────────────────────────────────────────────────────
   * Greedy best-section selector
   *
   * Purpose:
   *   The backend returns a complete university-wide schedule containing every
   *   section of every course.  When the student toggles "My Schedule", we need
   *   to extract only the sections relevant to their registered courses.  Since
   *   a course may have multiple sections in the schedule, we must choose ONE
   *   section per course.  This function picks the section with the fewest time
   *   conflicts against sections already chosen for other courses.
   *
   * Algorithm (greedy, O(C * S * T) where C = courses, S = sections, T = slots):
   *
   *   Step 1 - Group:
   *     Iterate over all time slots in the generated schedule.  Keep only slots
   *     whose course_code matches one of the student's registered courses.
   *     Organize them into a nested map:
   *       courseGroups[courseCode][sectionId] = [ slot, slot, ... ]
   *
   *   Step 2 - Score:
   *     For each course, evaluate every available section by counting how many
   *     of its slots overlap with already-occupied (day, time) pairs.  A lower
   *     conflict count is better.
   *
   *   Step 3 - Select (greedy choice):
   *     Sort sections by ascending conflict count and pick the first one (the
   *     section with fewest clashes).  This is the greedy decision -- it
   *     minimizes local conflicts without backtracking.
   *
   *   Step 4 - Commit:
   *     Add the chosen section's slots to the result array and mark all of its
   *     (day, time) pairs as used so subsequent courses account for them.
   *
   * Trade-off:
   *   A greedy approach may not find the globally optimal combination of
   *   sections (that would require exhaustive search or dynamic programming),
   *   but it runs in linear time relative to the number of slots and produces
   *   good results in practice because most university schedules have limited
   *   overlap between a single student's courses.
   *
   * @param {Array} slots - The full array of time-slot objects from a schedule result.
   * @returns {Array} - A filtered array containing only the best section per registered course.
   * ────────────────────────────────────────────────────────────────────── */
  const getFilteredSlots = (slots) => {
    /* If the student is viewing the full schedule, return everything unmodified */
    if (!myCoursesOnly) return slots;

    /*
     * Step 1: Group slots into a nested map by course code and section ID.
     *
     * Structure:  courseGroups = {
     *   "CS101": {
     *     42: [ {day:"Sun", time:"08:00", ...}, {day:"Mon", time:"08:00", ...} ],
     *     43: [ {day:"Sun", time:"10:00", ...}, ... ],
     *   },
     *   "MATH201": { ... },
     * }
     *
     * Slots belonging to courses the student is NOT registered for are skipped.
     */
    const courseGroups = {};
    for (const slot of slots) {
      /* Skip slots for courses the student did not register for */
      if (!registeredCourseCodes.has(slot.course_code)) continue;
      if (!courseGroups[slot.course_code]) courseGroups[slot.course_code] = {};
      if (!courseGroups[slot.course_code][slot.section_id]) {
        courseGroups[slot.course_code][slot.section_id] = [];
      }
      courseGroups[slot.course_code][slot.section_id].push(slot);
    }

    /** Accumulator for the final set of slots to display */
    const chosenSlots = [];

    /**
     * Set of "day-time" keys (e.g. "Sun-08:00") representing time cells already
     * occupied by a previously chosen section.  Used to detect conflicts when
     * scoring subsequent sections.
     */
    const usedTimes = new Set();

    /* Iterate over each registered course */
    for (const courseCode of Object.keys(courseGroups)) {
      /* Collect all candidate sections for this course (array of slot arrays) */
      const sections = Object.values(courseGroups[courseCode]);

      /*
       * Step 2: Score each section by counting how many of its slots collide
       * with already-occupied time cells.
       *
       * Example: if section A has slots on Sun-08:00 and Mon-10:00, and
       * Sun-08:00 is already used, its conflict count is 1.
       */
      const ranked = sections
        .map(sectionSlots => {
          const conflicts = sectionSlots.filter(s =>
            usedTimes.has(`${s.day}-${s.time}`)
          ).length;
          return { sectionSlots, conflicts };
        })
        .sort((a, b) => a.conflicts - b.conflicts); // fewest clashes first

      /*
       * Step 3: Commit the best-ranked section (index 0 after sorting).
       * Step 4: Mark all of its (day, time) pairs as used for future scoring.
       */
      const best = ranked[0].sectionSlots;
      for (const slot of best) {
        usedTimes.add(`${slot.day}-${slot.time}`);
        chosenSlots.push(slot);
      }
    }

    return chosenSlots;
  };

  /* ──────────────────────────────────────────────
   * Pre-computed values for the View Results page
   * ────────────────────────────────────────────── */

  /** The schedule result object currently selected by the tab strip */
  const currentResult = scheduleResults[selectedTab];

  /**
   * The slots to render in the ScheduleCalendar.
   * If a result is selected, apply the greedy filter (or pass all slots through
   * if myCoursesOnly is false).
   */
  const filteredSlots = currentResult ? getFilteredSlots(currentResult.slots || []) : [];

  /* ──────────────────────────────────────────────
   * Render
   * ────────────────────────────────────────────── */
  return (
    <div className="dashboard">

      {/* ================================================================
       *  SIDEBAR - Fixed navigation rail with page links and user info.
       *  Shows the app title, the student role badge, the student's name,
       *  navigation buttons (one per page), and a sign-out button at the
       *  bottom.  The "View Results" button only appears after schedules
       *  have been generated.
       * ================================================================ */}
      <aside className="sidebar">
        {/* Sidebar header: branding + role badge + student name */}
        <div className="sidebar-header">
          <h2>IMAMU Scheduler</h2>
          <span className="role-badge role-student">Student</span>
          <div style={{ marginTop: 8, fontSize: '0.8rem', opacity: 0.7 }}>{user.full_name}</div>
        </div>

        {/* Sidebar navigation buttons */}
        <nav className="sidebar-nav">
          <button className={`nav-item ${activePage === 'courses' ? 'active' : ''}`} onClick={() => setActivePage('courses')}>
            Browse Courses
          </button>
          <button className={`nav-item ${activePage === 'registered' ? 'active' : ''}`} onClick={() => setActivePage('registered')}>
            My Registrations ({registrations.length})
          </button>
          <button className={`nav-item ${activePage === 'generate' ? 'active' : ''}`} onClick={() => setActivePage('generate')}>
            Generate Schedule
          </button>
          {/* "View Results" tab is conditionally rendered only after generation produces results */}
          {scheduleResults.length > 0 && (
            <button className={`nav-item ${activePage === 'schedule' ? 'active' : ''}`} onClick={() => setActivePage('schedule')}>
              View Results
            </button>
          )}
          <button className={`nav-item ${activePage === 'saved' ? 'active' : ''}`} onClick={() => setActivePage('saved')}>
            Saved Schedules
          </button>
        </nav>

        {/* Sign-out button pinned to the bottom of the sidebar */}
        <div className="sidebar-footer">
          <button className="nav-item" onClick={logout}>Sign Out</button>
        </div>
      </aside>

      {/* ================================================================
       *  MAIN CONTENT AREA
       *  Renders the active page based on `activePage` state.
       *  A transient message banner and a full-screen loading overlay
       *  are layered above the page content when appropriate.
       * ================================================================ */}
      <main className="main-content">

        {/* ── Transient feedback / error message banner ──
         *  Displayed at the top of the main area whenever `msg` is non-empty.
         *  Background and text color switch between red (error) and green (success)
         *  based on whether the message text contains "fail" or "Please".
         */}
        {msg && (
          <div className="error-msg" style={{
            marginBottom: 16,
            background: msg.includes('fail') || msg.includes('Please') ? '#fef2f2' : '#ecfdf5',
            color: msg.includes('fail') || msg.includes('Please') ? '#dc2626' : '#059669',
          }}>
            {msg}
          </div>
        )}

        {/* ── Full-screen loading overlay ──
         *  Shown while `generating` is true (schedule generation in progress).
         *  Contains a spinner animation and descriptive text indicating the
         *  selected algorithm.
         */}
        {generating && (
          <div className="generating-overlay">
            <div className="big-spinner"></div>
            <p>Generating optimal schedules using {algorithm}...</p>
            <p style={{ fontSize: '0.85rem', opacity: 0.7, marginTop: 8 }}>This may take a moment</p>
          </div>
        )}

        {/* ================================================================
         *  PAGE 1: BROWSE COURSES
         *
         *  Displays the full course catalog in a filterable table.
         *  - Department and level dropdowns narrow the list.
         *  - Each course expands into one row per section that matches the
         *    student's gender (IMAMU uses gender-segregated sections).
         *  - For each section, the student sees enrollment count (enrolled/capacity)
         *    and an action button:
         *      * "Drop"    if the student is enrolled in that exact section
         *      * "Enrolled (other section)" badge if enrolled in a different section
         *        of the same course
         *      * "Register" otherwise
         *  - If a course has zero sections for the student's gender, a single
         *    row is shown with a "No sections for {gender}" warning badge.
         * ================================================================ */}
        {activePage === 'courses' && (
          <>
            <div className="page-header"><h1>Browse Courses</h1></div>

            {/* Filter bar: department and level dropdowns */}
            <div className="filters">
              <select value={filterDept} onChange={e => setFilterDept(e.target.value)}>
                <option value="">All Departments</option>
                {departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
              <select value={filterLevel} onChange={e => setFilterLevel(e.target.value)}>
                <option value="">All Levels</option>
                {[1,2,3,4,5,6,7,8].map(l => <option key={l} value={l}>Level {l}</option>)}
              </select>
            </div>

            {/* Course table card */}
            <div className="card">
              <div className="table-container">
                <table>
                  <thead>
                    <tr><th>Code</th><th>Course</th><th>Dept</th><th>Level</th><th>Credits</th><th>Section</th><th>Action</th></tr>
                  </thead>
                  <tbody>
                    {filteredCourses.map(course => (
                      <React.Fragment key={course.id}>
                        {/* Render one row per gender-matching section of this course */}
                        {(course.sections || []).filter(s => s.gender === user.gender).map(section => (
                          <tr key={section.id}>
                            <td><strong>{course.code}</strong></td>
                            <td>{course.name}</td>
                            <td><span className="badge badge-blue">{course.department_name}</span></td>
                            <td>Level {course.level}</td>
                            <td>{course.credits}</td>
                            {/* Section identifier + enrollment fraction (e.g. "A (23/40)") */}
                            <td>
                              <span className="badge badge-gray">{section.section_id}</span>
                              <span style={{ fontSize: '0.75rem', color: '#6b7280', marginLeft: 4 }}>
                                ({section.enrolled}/{section.capacity})
                              </span>
                            </td>
                            {/* Action column: contextual button based on enrollment state */}
                            <td>
                              {registeredSectionIds.has(section.id) ? (
                                /* Student is in THIS section -> offer to drop */
                                <button className="btn btn-danger btn-sm" onClick={() => handleUnregister(section.id)}>Drop</button>
                              ) : registeredCourseIds.has(course.id) ? (
                                /* Student is in a DIFFERENT section of the same course */
                                <span className="badge badge-green">Enrolled (other section)</span>
                              ) : (
                                /* Student is not enrolled in any section of this course */
                                <button className="btn btn-primary btn-sm" onClick={() => handleRegister(section.id)}>Register</button>
                              )}
                            </td>
                          </tr>
                        ))}
                        {/* Fallback row when no sections match the student's gender */}
                        {(course.sections || []).filter(s => s.gender === user.gender).length === 0 && (
                          <tr key={course.id}>
                            <td><strong>{course.code}</strong></td>
                            <td>{course.name}</td>
                            <td><span className="badge badge-blue">{course.department_name}</span></td>
                            <td>Level {course.level}</td>
                            <td>{course.credits}</td>
                            <td><span className="badge badge-orange">No sections for {user.gender}</span></td>
                            <td>-</td>
                          </tr>
                        )}
                      </React.Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {/* ================================================================
         *  PAGE 2: MY REGISTRATIONS
         *
         *  Shows two stat cards (registered course count, total credits) and
         *  a table of the student's current registrations with a "Drop" button
         *  on each row.  Displays an empty-state message if no registrations
         *  exist.
         *
         *  Total credits are computed by looking up each registration's course
         *  in the `courses` array and summing the credit values (default 3 if
         *  the course is not found, which can happen if data loads are out of
         *  sync).
         * ================================================================ */}
        {activePage === 'registered' && (
          <>
            <div className="page-header"><h1>My Registrations</h1></div>

            {/* Summary stat cards */}
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-value">{registrations.length}</div>
                <div className="stat-label">Registered Courses</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">
                  {/* Sum credits by matching each registration's course_id to the courses array */}
                  {registrations.reduce((sum, r) => sum + (courses.find(c => c.id === r.course_id)?.credits || 3), 0)}
                </div>
                <div className="stat-label">Total Credits</div>
              </div>
            </div>

            {/* Registration table or empty-state placeholder */}
            <div className="card">
              {registrations.length === 0 ? (
                <div className="empty-state">
                  <p>No registrations yet. Browse courses and register to get started.</p>
                </div>
              ) : (
                <div className="table-container">
                  <table>
                    <thead>
                      <tr><th>Course Code</th><th>Course Name</th><th>Section</th><th>Gender</th><th>Action</th></tr>
                    </thead>
                    <tbody>
                      {registrations.map(reg => (
                        <tr key={reg.id}>
                          <td><strong>{reg.course_code}</strong></td>
                          <td>{reg.course_name}</td>
                          <td><span className="badge badge-gray">{reg.section_id}</span></td>
                          <td><span className="badge badge-purple">{reg.gender}</span></td>
                          <td><button className="btn btn-danger btn-sm" onClick={() => handleUnregister(reg.id)}>Drop</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}

        {/* ================================================================
         *  PAGE 3: GENERATE SCHEDULE
         *
         *  Lets the student choose an optimization algorithm and objective,
         *  then trigger schedule generation.
         *
         *  Algorithm options:
         *    - GA  (Genetic Algorithm)  - evolutionary approach
         *    - PSO (Particle Swarm Optimization) - swarm intelligence approach
         *
         *  Objective options (shown for both GA and PSO):
         *    - Student-Optimized    : minimize student time conflicts & gaps
         *    - Instructor-Optimized : minimize instructor workload issues
         *    - University-Optimized : maximize room utilization & balance
         *    - All Three            : multi-objective (returns 3 results)
         *
         *  The "Generate Schedule" button is disabled when there are no
         *  registrations or when generation is already in progress.
         * ================================================================ */}
        {activePage === 'generate' && (
          <>
            <div className="page-header"><h1>Generate Schedule</h1></div>
            <div className="card">
              {/* Algorithm selector: toggle between GA and PSO */}
              <h3 style={{ marginBottom: 16 }}>Algorithm Selection</h3>
              <div className="algo-selector">
                <button className={`algo-btn ${algorithm === 'GA' ? 'active' : ''}`} onClick={() => setAlgorithm('GA')}>
                  Genetic Algorithm (GA)
                </button>
                <button className={`algo-btn ${algorithm === 'PSO' ? 'active' : ''}`} onClick={() => setAlgorithm('PSO')}>
                  Particle Swarm (PSO)
                </button>
              </div>

              {/* Objective selector: only shown when an algorithm is selected (always true since default is 'GA') */}
              {(algorithm === 'GA' || algorithm === 'PSO') && (
                <>
                  <h3 style={{ marginBottom: 12, marginTop: 20 }}>Optimization Objective</h3>
                  <div className="algo-selector">
                    <button className={`algo-btn ${objective === 'student' ? 'active' : ''}`} onClick={() => setObjective('student')}>Student-Optimized</button>
                    <button className={`algo-btn ${objective === 'instructor' ? 'active' : ''}`} onClick={() => setObjective('instructor')}>Instructor-Optimized</button>
                    <button className={`algo-btn ${objective === 'university' ? 'active' : ''}`} onClick={() => setObjective('university')}>University-Optimized</button>
                    <button className={`algo-btn ${objective === 'all' ? 'active' : ''}`} onClick={() => setObjective('all')}>All Three</button>
                  </div>
                </>
              )}

              {/* Status text and generate button */}
              <div style={{ marginTop: 24 }}>
                <p style={{ fontSize: '0.9rem', color: '#6b7280', marginBottom: 16 }}>
                  {registrations.length > 0
                    ? `Will generate schedules for your ${registrations.length} registered course(s).`
                    : 'You need to register for courses first before generating a schedule.'
                  }
                </p>
                <button className="btn btn-primary" onClick={handleGenerate} disabled={registrations.length === 0 || generating}>
                  {generating ? <><span className="spinner"></span> Generating...</> : 'Generate Schedule'}
                </button>
              </div>
            </div>
          </>
        )}

        {/* ================================================================
         *  PAGE 4: VIEW RESULTS
         *
         *  Displays the generated schedule results in a tabbed interface.
         *  Each tab represents one schedule option ranked by the backend
         *  (showing rank #, label, fitness score, and conflict count).
         *
         *  Within a selected tab:
         *  - A toggle switches between "My Schedule" (greedy best-section
         *    selection showing only the student's courses) and "Full Schedule"
         *    (the complete university-wide timetable for this solution).
         *  - A "Save This Schedule" button persists the result.
         *  - When in "My Schedule" mode, a blue info bar shows which courses
         *    were auto-selected with the total slot count.
         *  - The ScheduleCalendar component renders the weekly grid.
         *
         *  This page only renders when scheduleResults is non-empty.
         * ================================================================ */}
        {activePage === 'schedule' && scheduleResults.length > 0 && (
          <>
            <div className="page-header"><h1>Generated Schedules</h1></div>

            {/* Tab strip: one tab per generated schedule result */}
            <div className="schedule-tabs">
              {scheduleResults.map((result, idx) => (
                <button key={idx} className={`schedule-tab ${selectedTab === idx ? 'active' : ''}`} onClick={() => setSelectedTab(idx)}>
                  <div className="tab-rank">#{result.rank}</div>
                  <div className="tab-label">{result.label}</div>
                  <div className="tab-fitness">Fitness: {result.fitness} | Conflicts: {result.conflicts}</div>
                </button>
              ))}
            </div>

            {/* Selected schedule detail card */}
            <div className="card">
              {/* Card header: schedule label/description + toggle + save button */}
              <div className="card-header">
                <div>
                  <h3>{currentResult?.label}</h3>
                  <p style={{ fontSize: '0.85rem', color: '#6b7280' }}>{currentResult?.description}</p>
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>

                  {/* Toggle: "My Schedule" (greedy best-section) vs "Full Schedule" (all slots)
                   *  Rendered as a pill-style toggle with two inline buttons.
                   *  The active button gets a dark background (#1e3a5f) while the
                   *  inactive one is transparent.
                   */}
                  <div style={{ display: 'flex', alignItems: 'center', background: '#f3f4f6', borderRadius: 8, padding: '4px' }}>
                    <button
                      style={{
                        padding: '4px 14px', borderRadius: 6, border: 'none', cursor: 'pointer',
                        fontSize: '0.82rem', fontWeight: 600, transition: 'all 0.15s',
                        background: myCoursesOnly ? '#1e3a5f' : 'transparent',
                        color: myCoursesOnly ? '#fff' : '#6b7280',
                      }}
                      onClick={() => setMyCoursesOnly(true)}
                    >
                      My Schedule
                    </button>
                    <button
                      style={{
                        padding: '4px 14px', borderRadius: 6, border: 'none', cursor: 'pointer',
                        fontSize: '0.82rem', fontWeight: 600, transition: 'all 0.15s',
                        background: !myCoursesOnly ? '#1e3a5f' : 'transparent',
                        color: !myCoursesOnly ? '#fff' : '#6b7280',
                      }}
                      onClick={() => setMyCoursesOnly(false)}
                    >
                      Full Schedule
                    </button>
                  </div>

                  {/* Save button: persists this schedule result to the backend */}
                  <button className="btn btn-success btn-sm" onClick={() => handleSave(currentResult)}>
                    Save This Schedule
                  </button>
                </div>
              </div>

              {/* ── Course tags + slot count summary bar ──
               *  Shown only in "My Schedule" mode.  Displays a blue info bar listing
               *  each registered course code as a tag, followed by the total number of
               *  time slots selected by the greedy algorithm.
               */}
              {myCoursesOnly && (
                <div style={{
                  display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16,
                  padding: '10px 16px', background: '#f0f9ff',
                  borderRadius: 8, border: '1px solid #bae6fd', alignItems: 'center',
                }}>
                  <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#0369a1', marginRight: 4 }}>
                    Best sections auto-selected for:
                  </span>
                  {registrations.map(reg => (
                    <span key={reg.id} style={{
                      fontSize: '0.78rem', background: '#1e3a5f', color: '#fff',
                      padding: '2px 8px', borderRadius: 4,
                    }}>
                      {reg.course_code}
                    </span>
                  ))}
                  <span style={{ fontSize: '0.78rem', color: '#6b7280', marginLeft: 4 }}>
                    — {filteredSlots.length} slot(s)
                  </span>
                </div>
              )}

              {/* Weekly calendar grid rendered by the ScheduleCalendar component.
               *  Receives the (possibly greedy-filtered) slots and the time slot
               *  definitions that form the row headers.
               */}
              <ScheduleCalendar slots={filteredSlots} timeSlots={timeSlots} />
            </div>
          </>
        )}

        {/* ================================================================
         *  PAGE 5: SAVED SCHEDULES
         *
         *  Lists all schedules the student has previously saved.  Each saved
         *  schedule is rendered in its own card with a header showing the name,
         *  algorithm, fitness, and conflict count, a delete button, and a full
         *  ScheduleCalendar rendering of the saved slot data.
         *
         *  If no schedules have been saved yet, an empty-state message is shown.
         *  Data is fetched lazily via loadSaved() when the student navigates to
         *  this page.
         * ================================================================ */}
        {activePage === 'saved' && (
          <>
            <div className="page-header"><h1>Saved Schedules</h1></div>
            {savedSchedules.length === 0 ? (
              <div className="card">
                <div className="empty-state">
                  <p>No saved schedules. Generate a schedule and save it to see it here.</p>
                </div>
              </div>
            ) : (
              savedSchedules.map(saved => (
                <div key={saved.id} className="card">
                  {/* Card header: schedule name, metadata, and delete button */}
                  <div className="card-header">
                    <div>
                      <h3>{saved.name}</h3>
                      <p style={{ fontSize: '0.85rem', color: '#6b7280' }}>
                        {saved.algorithm} | Fitness: {saved.fitness} | Conflicts: {saved.conflicts}
                      </p>
                    </div>
                    <button className="btn btn-danger btn-sm" onClick={() => handleDeleteSaved(saved.id)}>Delete</button>
                  </div>
                  {/* Render the saved schedule's slot data in a calendar grid */}
                  <ScheduleCalendar slots={saved.schedule_data || []} timeSlots={timeSlots} />
                </div>
              ))
            )}
          </>
        )}
      </main>
    </div>
  );
}
