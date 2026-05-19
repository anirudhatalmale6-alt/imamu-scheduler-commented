/**
 * InstructorDashboard.js
 *
 * Dashboard page for users with the "instructor" role in the IMAMU Course
 * Scheduler application. Instructors can generate a full university schedule
 * and then browse / filter the results by instructor name.
 *
 * ---- How the Instructor view differs from the Student view ----
 *
 * 1. Schedule scope
 *    - StudentDashboard generates a schedule using only the courses the
 *      student has registered for (a personal subset).
 *    - InstructorDashboard generates a schedule using ALL courses in the
 *      system, producing a university-wide timetable.
 *
 * 2. Filtering mechanism
 *    - StudentDashboard provides a "My Schedule" toggle that shows only the
 *      student's own registered courses vs. the full result.
 *    - InstructorDashboard provides a free-text instructor name filter with
 *      an autocomplete datalist, allowing the instructor (or admin reviewing
 *      as instructor) to isolate any instructor's assigned slots.
 *
 * 3. No course registration
 *    - Students have a "My Courses" page for adding/dropping courses.
 *    - Instructors skip that step entirely; the full course catalog is used
 *      as-is when generating the schedule.
 *
 * Component state:
 *   activePage           – which sidebar page is shown: 'generate' | 'schedule'
 *   courses              – full list of courses fetched from the API
 *   scheduleResults      – array of result objects returned after generation
 *   selectedTab          – index of the currently viewed result tab
 *   generating           – true while the backend is computing a schedule
 *   algorithm            – selected metaheuristic: 'GA' | 'PSO'
 *   objective            – optimization target: 'instructor' | 'student' | 'university' | 'all'
 *   timeSlots            – class-type time slots used by ScheduleCalendar
 *   msg                  – transient error/info message string
 *   instructorFilter     – the committed (applied) instructor name query
 *   instructorFilterInput – the live value of the filter text input (not yet applied)
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { courseAPI, scheduleAPI, dataAPI } from '../services/api';
import ScheduleCalendar from '../components/ScheduleCalendar';

export default function InstructorDashboard() {
  /* ---- Auth context ---- */
  // Destructure the logged-in user object and logout action from AuthContext.
  const { user, logout } = useAuth();

  /* ---- Component state ---- */

  // Which sidebar page is active: 'generate' (algorithm picker) or 'schedule' (results viewer).
  const [activePage, setActivePage] = useState('generate');

  // Full list of courses fetched from the backend. Every course is included
  // when generating a schedule (unlike the student view which only uses
  // registered courses).
  const [courses, setCourses] = useState([]);

  // Array of schedule result objects returned by the generation endpoint.
  // Each result contains a label, fitness score, conflict count, and an
  // array of slot assignments.
  const [scheduleResults, setScheduleResults] = useState([]);

  // Index of the currently selected result tab (0-based).
  const [selectedTab, setSelectedTab] = useState(0);

  // True while the backend is running the optimization algorithm. Drives
  // the full-screen spinner overlay.
  const [generating, setGenerating] = useState(false);

  // Selected optimization algorithm: 'GA' (Genetic Algorithm) or 'PSO'
  // (Particle Swarm Optimization).
  const [algorithm, setAlgorithm] = useState('GA');

  // Optimization objective: whose preference the algorithm should
  // prioritize. Options: 'instructor', 'student', 'university', or 'all'.
  const [objective, setObjective] = useState('instructor');

  // Time slot definitions (filtered to 'Class' type) used by the
  // ScheduleCalendar component to render the grid rows.
  const [timeSlots, setTimeSlots] = useState([]);

  // Transient message string shown as an error banner. Auto-cleared after
  // 3 seconds on generation failure.
  const [msg, setMsg] = useState('');

  // The committed (applied) instructor name filter. When non-empty, only
  // slots whose instructor_name matches this substring are displayed.
  const [instructorFilter, setInstructorFilter] = useState('');

  // The live text input value for the instructor filter, which has not yet
  // been committed. The user commits it by pressing Enter or clicking "Show".
  const [instructorFilterInput, setInstructorFilterInput] = useState('');

  /* ---- Data loading ---- */

  /**
   * loadData
   *
   * Fetches the full course catalog and all time-slot definitions from the
   * API in parallel. Time slots are filtered to keep only those with
   * slot_type === 'Class' (excluding lab, office-hour, etc. slots).
   *
   * Wrapped in useCallback with an empty dependency array so it is created
   * once and can safely appear in the useEffect dependency list.
   */
  const loadData = useCallback(async () => {
    try {
      const [coursesRes, tsRes] = await Promise.all([
        courseAPI.list(),
        dataAPI.timeslots(),
      ]);
      setCourses(coursesRes.data);
      setTimeSlots(tsRes.data.filter(t => t.slot_type === 'Class'));
    } catch (err) { console.error(err); }
  }, []);

  // Run loadData once on mount. The dependency on the memoized loadData
  // reference satisfies the exhaustive-deps lint rule without causing
  // repeated fetches.
  useEffect(() => { loadData(); }, [loadData]);

  /* ---- Schedule generation ---- */

  /**
   * handleGenerate
   *
   * Sends a schedule generation request to the backend. Unlike the student
   * dashboard, which sends only the student's registered course IDs, this
   * handler collects the IDs of ALL courses in the catalog. This produces
   * a complete university-wide timetable.
   *
   * Parameters sent to scheduleAPI.generate():
   *   selected_course_ids – IDs of every course in `courses`
   *   algorithm           – 'GA' or 'PSO'
   *   objective           – optimization target chosen by the user
   *   preferred_gender    – null (not applicable for instructor view)
   *
   * On success:
   *   - Stores the array of result objects in scheduleResults
   *   - Resets selectedTab to the first result
   *   - Clears any active instructor filter
   *   - Switches to the 'schedule' page automatically
   *
   * On failure:
   *   - Shows the backend error detail (or a generic message) as a
   *     transient banner that auto-clears after 3 seconds.
   */
  const handleGenerate = async () => {
    setGenerating(true);
    try {
      // Collect every course ID — full university schedule, not a subset.
      const allCourseIds = courses.map(c => c.id);
      const res = await scheduleAPI.generate({
        selected_course_ids: allCourseIds,
        algorithm,
        objective,
        preferred_gender: null,
      });
      setScheduleResults(res.data);
      setSelectedTab(0);
      // Reset the instructor filter so the new results start unfiltered.
      setInstructorFilter('');
      setInstructorFilterInput('');
      // Automatically navigate to the results page.
      setActivePage('schedule');
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Generation failed');
      setTimeout(() => setMsg(''), 3000);
    } finally {
      setGenerating(false);
    }
  };

  /* ---- Instructor-specific filtering helpers ---- */

  /**
   * getInstructorSlots
   *
   * Filters a slot array to keep only those whose instructor_name contains
   * the current filter string. Matching is case-insensitive and
   * substring-based, so typing "ahmed" will match "Dr. Ahmed Al-Rashid".
   *
   * If no filter is active (instructorFilter is empty), all slots are
   * returned unchanged.
   *
   * This is the instructor view's equivalent of the student view's
   * "My Schedule" toggle — but instead of matching by the logged-in
   * student's registered courses, it matches by typed instructor name.
   *
   * @param {Array} slots - Array of slot objects from the current result.
   * @returns {Array} Filtered (or unfiltered) slot array.
   */
  const getInstructorSlots = (slots) => {
    if (!instructorFilter) return slots;
    const query = instructorFilter.toLowerCase().trim();
    return slots.filter(slot =>
      slot.instructor_name?.toLowerCase().includes(query)
    );
  };

  /**
   * getInstructorsInSchedule
   *
   * Extracts a sorted, deduplicated list of all instructor names present
   * in the given slots array. Used to populate the HTML <datalist> that
   * provides browser-native autocomplete suggestions in the instructor
   * filter input field.
   *
   * @param {Array} slots - Array of slot objects from the current result.
   * @returns {Array<string>} Sorted unique instructor names.
   */
  const getInstructorsInSchedule = (slots) => {
    const names = new Set(slots.map(s => s.instructor_name).filter(Boolean));
    return [...names].sort();
  };

  /* ---- Derived values for render ---- */

  // The result object for the currently selected tab.
  const currentResult = scheduleResults[selectedTab];

  // All slot assignments in the current result (empty array if no result yet).
  const allSlots = currentResult?.slots || [];

  // Slots after applying the instructor name filter.
  const filteredSlots = getInstructorSlots(allSlots);

  // Unique instructor names for the autocomplete datalist.
  const instructorsInSchedule = getInstructorsInSchedule(allSlots);

  /* ========================================================================
   * JSX Render
   * ======================================================================== */
  return (
    <div className="dashboard">

      {/* ----------------------------------------------------------------
       * Sidebar
       * ----------------------------------------------------------------
       * Shows the app title, an "Instructor" role badge, the user's full
       * name, navigation buttons, and a sign-out button.
       * Two navigation items:
       *   - "Generate Schedule" – always visible
       *   - "View Results" – only visible after at least one generation
       * ---------------------------------------------------------------- */}
      <aside className="sidebar">
        {/* Sidebar header: branding, role badge, and user name */}
        <div className="sidebar-header">
          <h2>IMAMU Scheduler</h2>
          <span className="role-badge role-instructor">Instructor</span>
          <div style={{ marginTop: 8, fontSize: '0.8rem', opacity: 0.7 }}>{user.full_name}</div>
        </div>

        {/* Sidebar navigation buttons */}
        <nav className="sidebar-nav">
          {/* Always-visible button to return to the generation form */}
          <button className={`nav-item ${activePage === 'generate' ? 'active' : ''}`} onClick={() => setActivePage('generate')}>
            Generate Schedule
          </button>
          {/* Conditionally rendered button — only appears once results exist */}
          {scheduleResults.length > 0 && (
            <button className={`nav-item ${activePage === 'schedule' ? 'active' : ''}`} onClick={() => setActivePage('schedule')}>
              View Results
            </button>
          )}
        </nav>

        {/* Sidebar footer with sign-out action */}
        <div className="sidebar-footer">
          <button className="nav-item" onClick={logout}>Sign Out</button>
        </div>
      </aside>

      {/* ----------------------------------------------------------------
       * Main content area
       * ----------------------------------------------------------------
       * Renders one of two pages based on activePage:
       *   'generate'  – Algorithm and objective selector form
       *   'schedule'  – Tabbed results viewer with instructor filter
       * Also shows a transient error banner and a full-screen spinner
       * overlay when a generation is in progress.
       * ---------------------------------------------------------------- */}
      <main className="main-content">

        {/* Transient error / info message banner.
            Appears when msg is non-empty (e.g., after a failed generation).
            Auto-cleared after 3 seconds by the setTimeout in handleGenerate. */}
        {msg && (
          <div className="error-msg" style={{
            marginBottom: 16,
            background: '#fef2f2',
            color: '#dc2626',
          }}>
            {msg}
          </div>
        )}

        {/* Full-screen spinner overlay shown while the backend is computing.
            Displays the selected algorithm name so the user knows what is
            running. */}
        {generating && (
          <div className="generating-overlay">
            <div className="big-spinner"></div>
            <p>Generating optimal schedules using {algorithm}...</p>
            <p style={{ fontSize: '0.85rem', opacity: 0.7, marginTop: 8 }}>This may take a moment</p>
          </div>
        )}

        {/* ==============================================================
         * PAGE: Generate Schedule
         * ==============================================================
         * Shown when activePage === 'generate'.
         * Contains two selector groups (algorithm and objective) and a
         * generate button. A note reminds the instructor that the full
         * university schedule will be created and that they can filter by
         * their name afterward.
         * ============================================================== */}
        {activePage === 'generate' && (
          <>
            <div className="page-header"><h1>Generate Schedule</h1></div>
            <div className="card">

              {/* Algorithm selector: Genetic Algorithm vs Particle Swarm.
                  Each button toggles the `algorithm` state. The active
                  button receives the 'active' CSS class for highlighting. */}
              <h3 style={{ marginBottom: 16 }}>Algorithm</h3>
              <div className="algo-selector">
                <button className={`algo-btn ${algorithm === 'GA' ? 'active' : ''}`} onClick={() => setAlgorithm('GA')}>
                  Genetic Algorithm (GA)
                </button>
                <button className={`algo-btn ${algorithm === 'PSO' ? 'active' : ''}`} onClick={() => setAlgorithm('PSO')}>
                  Particle Swarm (PSO)
                </button>
              </div>

              {/* Objective selector: determines whose preferences are
                  prioritized by the optimization algorithm.
                  - 'instructor' – minimize instructor conflicts / gaps
                  - 'student'    – minimize student conflicts / gaps
                  - 'university' – optimize room utilization / balance
                  - 'all'        – multi-objective combining all three */}
              <h3 style={{ marginBottom: 12, marginTop: 20 }}>Optimization Objective</h3>
              <div className="algo-selector">
                <button className={`algo-btn ${objective === 'instructor' ? 'active' : ''}`} onClick={() => setObjective('instructor')}>Instructor</button>
                <button className={`algo-btn ${objective === 'student' ? 'active' : ''}`} onClick={() => setObjective('student')}>Student</button>
                <button className={`algo-btn ${objective === 'university' ? 'active' : ''}`} onClick={() => setObjective('university')}>University</button>
                <button className={`algo-btn ${objective === 'all' ? 'active' : ''}`} onClick={() => setObjective('all')}>All Three</button>
              </div>

              {/* Instructional note and generate button.
                  The button is disabled while generating or when no courses
                  are available (courses.length === 0). */}
              <div style={{ marginTop: 24 }}>
                <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
                  Generates the full university schedule. After generation, filter by your name to view your assigned slots.
                </p>
                <button className="btn btn-primary" onClick={handleGenerate} disabled={generating || courses.length === 0}>
                  {generating ? <><span className="spinner"></span> Generating...</> : 'Generate Schedule'}
                </button>
              </div>
            </div>
          </>
        )}

        {/* ==============================================================
         * PAGE: View Results
         * ==============================================================
         * Shown when activePage === 'schedule' and at least one result
         * exists. Contains three sub-sections:
         *   1. Result tabs – one per generated schedule variant
         *   2. Instructor name filter with autocomplete datalist
         *   3. ScheduleCalendar rendering the (filtered) slots
         * ============================================================== */}
        {activePage === 'schedule' && scheduleResults.length > 0 && (
          <>
            <div className="page-header"><h1>Generated Schedules</h1></div>

            {/* ---- Result tabs ----
                Each tab shows the result's label, fitness score, and
                conflict count. Clicking a tab updates selectedTab, which
                in turn recalculates allSlots, filteredSlots, and the
                autocomplete list. */}
            <div className="schedule-tabs">
              {scheduleResults.map((result, idx) => (
                <button key={idx} className={`schedule-tab ${selectedTab === idx ? 'active' : ''}`} onClick={() => setSelectedTab(idx)}>
                  <div className="tab-label">{result.label}</div>
                  <div className="tab-fitness">Fitness: {result.fitness} | Conflicts: {result.conflicts}</div>
                </button>
              ))}
            </div>

            <div className="card">
              {/* Result header: label and description of the selected tab */}
              <div className="card-header">
                <div>
                  <h3>{currentResult?.label}</h3>
                  <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{currentResult?.description}</p>
                </div>
              </div>

              {/* ---- Instructor name filter ----
                  This is the instructor view's main filtering mechanism,
                  replacing the student view's "My Schedule" toggle.
                  - A text input with a native <datalist> for autocomplete.
                    The datalist is populated by getInstructorsInSchedule()
                    with every unique instructor name in the current result.
                  - Pressing Enter or clicking "Show" commits the input value
                    to instructorFilter, which triggers getInstructorSlots().
                  - "Clear" resets both the input and the committed filter. */}
              <div style={{
                display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16,
                padding: '12px 16px', background: 'var(--bg-table-header)',
                borderRadius: 8, border: '1px solid var(--border-color)',
              }}>
                <span style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>
                  🔍 Filter by Instructor:
                </span>
                {/* Text input bound to the live (uncommitted) filter value.
                    Pressing Enter commits the value without needing to click
                    the "Show" button.
                    The list attribute connects this input to the datalist
                    below for browser-native autocomplete suggestions. */}
                <input
                  type="text"
                  placeholder="Type your name (e.g. Dr. Ahmed, Ibrahim)..."
                  value={instructorFilterInput}
                  onChange={e => setInstructorFilterInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') setInstructorFilter(instructorFilterInput); }}
                  style={{
                    flex: 1, padding: '8px 14px', borderRadius: 6,
                    border: '1px solid var(--input-border)',
                    background: 'var(--input-bg)', color: 'var(--text-primary)',
                    fontSize: '0.9rem', outline: 'none',
                  }}
                  list="instructor-list"
                />
                {/* HTML datalist providing autocomplete suggestions.
                    Populated with sorted unique instructor names from the
                    current schedule result. The browser renders these as a
                    dropdown when the user types in the connected input. */}
                <datalist id="instructor-list">
                  {instructorsInSchedule.map(name => <option key={name} value={name} />)}
                </datalist>
                {/* "Show" button: commits the current input as the active filter */}
                <button className="btn btn-primary btn-sm" onClick={() => setInstructorFilter(instructorFilterInput)}>
                  Show
                </button>
                {/* "Clear" button: only visible when a filter is active.
                    Resets both the committed filter and the input text. */}
                {instructorFilter && (
                  <button className="btn btn-outline btn-sm" onClick={() => { setInstructorFilter(''); setInstructorFilterInput(''); }}>
                    Clear
                  </button>
                )}
              </div>

              {/* ---- Filter result summary ----
                  Shown only when an instructor filter is active.
                  Displays the count of matching slots and the query string.
                  If zero matches, an additional hint suggests trying a
                  different name. */}
              {instructorFilter && (
                <div style={{
                  marginBottom: 12, padding: '8px 16px', background: '#eff6ff',
                  borderRadius: 6, border: '1px solid #bfdbfe',
                  fontSize: '0.85rem', color: '#1e40af',
                }}>
                  Showing <strong>{filteredSlots.length}</strong> slot(s) for <strong>"{instructorFilter}"</strong>
                  {filteredSlots.length === 0 && ' — no match found. Try a different name.'}
                </div>
              )}

              {/* ---- Schedule calendar ----
                  Renders the weekly timetable grid using the (possibly
                  filtered) slot data and the class-type time slots.
                  CSS custom properties override the default event font
                  sizes with slightly larger values for the instructor view
                  to improve readability. */}
              <div style={{ '--event-font-size': '0.8rem', '--event-detail-font-size': '0.72rem' }}>
                <ScheduleCalendar slots={filteredSlots} timeSlots={timeSlots} />
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
