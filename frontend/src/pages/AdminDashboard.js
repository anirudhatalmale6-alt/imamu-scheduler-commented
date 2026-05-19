/**
 * @file AdminDashboard.js
 * @description Admin-level dashboard for the IMAMU Course Scheduler.
 *
 * This is the most privileged view in the application. Unlike the Student and
 * Instructor dashboards (which are scoped to a single gender or a single
 * instructor's courses), the Admin dashboard operates on the **entire
 * university catalog** and can display schedules for **both genders**
 * simultaneously.
 *
 * Key differences from the Student / Instructor views:
 *  - **Generate page**: The admin triggers full-university schedule generation
 *    (all courses, all departments, both Male and Female sections).  Student
 *    and Instructor views only consume the generated results.
 *  - **Multi-criteria filter bar**: Gender, Department, Day-of-week, and
 *    free-text Instructor search can all be combined (logical AND).  The
 *    Student view filters only by department/day; the Instructor view shows
 *    only that instructor's slots.
 *  - **Stats overview**: Four summary cards (total slots, male count, female
 *    count, currently-visible count after filters) give the admin a quick
 *    bird's-eye picture of schedule density and gender balance.
 *
 * Component state (all managed via React hooks):
 *  - activePage        – which sidebar page is shown ('generate' | 'schedule')
 *  - courses           – full course catalog fetched from the API
 *  - departments       – list of academic departments
 *  - scheduleResults   – array of generated schedule result objects (each with
 *                         label, fitness, conflicts, slots[])
 *  - selectedTab       – index into scheduleResults for the active tab
 *  - generating        – boolean flag; true while the API call is in flight
 *  - algorithm         – selected optimisation algorithm ('GA' | 'PSO')
 *  - objective         – optimisation target ('university' | 'instructor' |
 *                         'student' | 'all')
 *  - timeSlots         – class-type time-slot definitions from the backend
 *  - msg               – transient error/info string (auto-cleared after 3 s)
 *  - filterGender      – gender filter value ('' for all, 'Male', 'Female')
 *  - filterDept        – department filter value ('' for all)
 *  - filterDay         – day-of-week filter value ('' for all)
 *  - filterInstructor  – live instructor search string
 *  - filterInstructorInput – controlled value for the instructor text input
 *                            (kept in sync with filterInstructor)
 *
 * @module pages/AdminDashboard
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { courseAPI, scheduleAPI, dataAPI } from '../services/api';
import ScheduleCalendar from '../components/ScheduleCalendar';

export default function AdminDashboard() {
  /* ------------------------------------------------------------------ */
  /*  Auth context – provides the logged-in user object and logout fn   */
  /* ------------------------------------------------------------------ */
  const { user, logout } = useAuth();

  /* ------------------------------------------------------------------ */
  /*  Page / navigation state                                           */
  /*  'generate' = algorithm picker page; 'schedule' = results viewer   */
  /* ------------------------------------------------------------------ */
  const [activePage, setActivePage] = useState('generate');

  /* ------------------------------------------------------------------ */
  /*  Data fetched from the backend                                     */
  /*  courses      – every course in the catalog (used to build the     */
  /*                 selected_course_ids payload when generating)        */
  /*  departments  – academic departments (currently used to populate    */
  /*                 the department filter dropdown)                     */
  /*  timeSlots    – only "Class"-type slots; passed to ScheduleCalendar*/
  /*                 so it can render the correct time-axis labels       */
  /* ------------------------------------------------------------------ */
  const [courses, setCourses] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [timeSlots, setTimeSlots] = useState([]);

  /* ------------------------------------------------------------------ */
  /*  Schedule generation results and UI state                          */
  /*  scheduleResults – array returned by the API; each entry contains  */
  /*      { label, description, fitness, conflicts, slots[] }           */
  /*  selectedTab     – which result the admin is currently viewing     */
  /*  generating      – loading flag shown during generation            */
  /*  algorithm       – 'GA' (Genetic Algorithm) or 'PSO' (Particle    */
  /*                     Swarm Optimisation)                            */
  /*  objective       – the optimisation target that the backend should */
  /*                     maximise                                       */
  /*  msg             – transient error string displayed at the top     */
  /* ------------------------------------------------------------------ */
  const [scheduleResults, setScheduleResults] = useState([]);
  const [selectedTab, setSelectedTab] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [algorithm, setAlgorithm] = useState('GA');
  const [objective, setObjective] = useState('university');
  const [msg, setMsg] = useState('');

  /* ------------------------------------------------------------------ */
  /*  Filter state – the admin's comprehensive multi-filter system      */
  /*  All filters are combined with logical AND in getFilteredSlots().  */
  /*  An empty string ('') means "show all" for that dimension.         */
  /*                                                                    */
  /*  filterInstructorInput mirrors filterInstructor so the text input  */
  /*  stays a controlled component while both values update together.   */
  /* ------------------------------------------------------------------ */
  const [filterGender, setFilterGender] = useState('');       // 'Male' | 'Female' | ''
  const [filterDept, setFilterDept] = useState('');
  const [filterDay, setFilterDay] = useState('');
  const [filterInstructor, setFilterInstructor] = useState('');
  const [filterInstructorInput, setFilterInstructorInput] = useState('');

  /* ================================================================== */
  /*  DATA LOADING                                                      */
  /* ================================================================== */

  /**
   * loadData – Fetches the three reference datasets the admin dashboard needs:
   *   1. Full course catalog  (courseAPI.list)
   *   2. Department list      (dataAPI.departments)
   *   3. Time-slot definitions (dataAPI.timeslots), filtered to 'Class' type
   *      only (exam slots are excluded since the calendar shows classes)
   *
   * All three requests fire in parallel via Promise.all for speed.
   * Wrapped in useCallback so it can be a stable dependency for useEffect.
   */
  const loadData = useCallback(async () => {
    try {
      const [coursesRes, deptRes, tsRes] = await Promise.all([
        courseAPI.list(),
        dataAPI.departments(),
        dataAPI.timeslots(),
      ]);
      setCourses(coursesRes.data);
      setDepartments(deptRes.data);
      /* Only keep "Class" time-slots; exam/break slots are irrelevant here */
      setTimeSlots(tsRes.data.filter(t => t.slot_type === 'Class'));
    } catch (err) { console.error(err); }
  }, []);

  /**
   * Effect: call loadData once on mount (loadData is stable via useCallback
   * with an empty dependency array, so this runs exactly once).
   */
  useEffect(() => { loadData(); }, [loadData]);

  /* ================================================================== */
  /*  SCHEDULE GENERATION                                               */
  /* ================================================================== */

  /**
   * handleGenerate – Kicks off a full-university schedule generation.
   *
   * Unlike the Student or Instructor views (which may pre-filter by gender or
   * by a single instructor's courses), the admin sends **every course ID** and
   * sets preferred_gender to null so the backend produces sections for both
   * Male and Female students.
   *
   * On success:
   *  - Stores the result array in scheduleResults.
   *  - Resets the selected tab and all filters so the admin starts from a
   *    clean slate.
   *  - Switches the active page to 'schedule' automatically.
   *
   * On failure:
   *  - Displays the backend's error detail (or a generic message) in the
   *    transient msg banner, which auto-clears after 3 seconds.
   */
  const handleGenerate = async () => {
    setGenerating(true);
    try {
      /* Collect every course ID – admin generates the whole university */
      const allCourseIds = courses.map(c => c.id);
      const res = await scheduleAPI.generate({
        selected_course_ids: allCourseIds,
        algorithm,
        objective,
        preferred_gender: null, // null = include both Male and Female sections
      });
      setScheduleResults(res.data);
      setSelectedTab(0);
      /* Reset every filter so the fresh results are shown unfiltered */
      setFilterGender('');
      setFilterDept('');
      setFilterDay('');
      setFilterInstructor('');
      setFilterInstructorInput('');
      /* Navigate to the results page automatically */
      setActivePage('schedule');
    } catch (err) {
      setMsg(err.response?.data?.detail || 'Generation failed');
      setTimeout(() => setMsg(''), 3000);
    } finally {
      setGenerating(false);
    }
  };

  /* ================================================================== */
  /*  FILTERING HELPERS                                                 */
  /* ================================================================== */

  /**
   * getFilteredSlots – Applies every active filter (logical AND) to a
   * given array of schedule slots.
   *
   * Filter logic:
   *  - Gender:     exact match against slot.gender
   *  - Department: exact match against slot.department
   *  - Day:        exact match against slot.day
   *  - Instructor: case-insensitive substring match against
   *                slot.instructor_name (uses optional chaining because
   *                some slots may lack an instructor)
   *
   * A filter value of '' (empty string) is treated as "no filter" – the
   * corresponding check is skipped.
   *
   * @param {Array} slots - The unfiltered schedule slot array.
   * @returns {Array} Only the slots that satisfy every active filter.
   */
  const getFilteredSlots = (slots) => {
    return slots.filter(slot => {
      if (filterGender && slot.gender !== filterGender) return false;
      if (filterDept && slot.department !== filterDept) return false;
      if (filterDay && slot.day !== filterDay) return false;
      if (filterInstructor && !slot.instructor_name?.toLowerCase().includes(filterInstructor.toLowerCase())) return false;
      return true;
    });
  };

  /**
   * getUniqueValues – Extracts sorted, unique, truthy values for a given
   * key from an array of slot objects.  Used to populate the filter dropdowns
   * and the instructor datalist dynamically based on the current result set.
   *
   * @param {Array}  slots - The slot array to scan.
   * @param {string} key   - The property name to extract (e.g. 'day',
   *                          'department', 'instructor_name').
   * @returns {Array} Sorted array of unique non-falsy values.
   */
  const getUniqueValues = (slots, key) => {
    return [...new Set(slots.map(s => s[key]).filter(Boolean))].sort();
  };

  /* ================================================================== */
  /*  DERIVED / COMPUTED VALUES                                         */
  /* ================================================================== */

  /** The schedule result object for the currently selected tab */
  const currentResult = scheduleResults[selectedTab];

  /** All slots in the currently selected result (unfiltered) */
  const allSlots = currentResult?.slots || [];

  /** The subset of slots that pass every active filter */
  const filteredSlots = getFilteredSlots(allSlots);

  /** Unique day values present in the current result (for Day dropdown) */
  const uniqueDays = getUniqueValues(allSlots, 'day');

  /** Unique department names present in the current result (for Dept dropdown) */
  const uniqueDepts = getUniqueValues(allSlots, 'department');

  /** Unique instructor names present in the current result (for datalist) */
  const uniqueInstructors = getUniqueValues(allSlots, 'instructor_name');

  /* ------------------------------------------------------------------ */
  /*  Stats counters – give the admin a high-level overview              */
  /*  maleSlots / femaleSlots show gender balance at a glance.           */
  /*  filteredSlots.length (displayed in the fourth stat card) tells the */
  /*  admin how many slots survive the current filter combination.       */
  /* ------------------------------------------------------------------ */
  const maleSlots = allSlots.filter(s => s.gender === 'Male').length;
  const femaleSlots = allSlots.filter(s => s.gender === 'Female').length;

  /* ================================================================== */
  /*  RENDER                                                            */
  /* ================================================================== */
  return (
    <div className="dashboard">

      {/* ============================================================ */}
      {/*  SIDEBAR – Navigation and user info                          */}
      {/*  Contains the IMAMU branding, an "Admin" role badge, the     */}
      {/*  user's full name, nav buttons for the two pages, and a      */}
      {/*  Sign Out button at the bottom.                              */}
      {/* ============================================================ */}
      <aside className="sidebar">
        {/* Sidebar header: branding + role badge + user name */}
        <div className="sidebar-header">
          <h2>IMAMU Scheduler</h2>
          <span className="role-badge" style={{ background: '#c9a84c', color: '#1e3a5f' }}>Admin</span>
          <div style={{ marginTop: 8, fontSize: '0.8rem', opacity: 0.7 }}>{user.full_name}</div>
        </div>

        {/* Navigation: two pages – Generate and (conditionally) View Results */}
        <nav className="sidebar-nav">
          {/* "Generate Schedule" is always visible */}
          <button className={`nav-item ${activePage === 'generate' ? 'active' : ''}`} onClick={() => setActivePage('generate')}>
            Generate Schedule
          </button>
          {/* "View Results" only appears after at least one generation */}
          {scheduleResults.length > 0 && (
            <button className={`nav-item ${activePage === 'schedule' ? 'active' : ''}`} onClick={() => setActivePage('schedule')}>
              View Results
            </button>
          )}
        </nav>

        {/* Footer: sign-out button */}
        <div class="sidebar-footer">
          <button className="nav-item" onClick={logout}>Sign Out</button>
        </div>
      </aside>

      {/* ============================================================ */}
      {/*  MAIN CONTENT AREA                                           */}
      {/* ============================================================ */}
      <main className="main-content">

        {/* ----------------------------------------------------------
            Transient error/info banner.
            Displayed when `msg` is non-empty (e.g. generation failure).
            Auto-clears after 3 seconds via setTimeout in handleGenerate.
        ---------------------------------------------------------- */}
        {msg && (
          <div className="error-msg" style={{ marginBottom: 16, background: '#fef2f2', color: '#dc2626' }}>
            {msg}
          </div>
        )}

        {/* ----------------------------------------------------------
            Generating overlay – full-area spinner + status text.
            Blocks interaction while the backend is computing the
            schedule (can take several seconds for large catalogs).
        ---------------------------------------------------------- */}
        {generating && (
          <div className="generating-overlay">
            <div className="big-spinner"></div>
            <p>Generating optimal schedules using {algorithm}...</p>
            <p style={{ fontSize: '0.85rem', opacity: 0.7, marginTop: 8 }}>This may take a moment</p>
          </div>
        )}

        {/* ========================================================== */}
        {/*  PAGE 1: GENERATE SCHEDULE                                  */}
        {/*  Lets the admin pick an algorithm (GA or PSO) and an        */}
        {/*  optimisation objective, then fire off a full-university     */}
        {/*  generation.  The "Generate Full Schedule" button is         */}
        {/*  disabled while generating or when courses have not loaded.  */}
        {/* ========================================================== */}
        {activePage === 'generate' && (
          <>
            <div className="page-header"><h1>Generate University Schedule</h1></div>
            <div className="card">

              {/* --- Algorithm selector --- */}
              {/* Two toggle buttons: Genetic Algorithm and Particle Swarm */}
              <h3 style={{ marginBottom: 16 }}>Algorithm</h3>
              <div className="algo-selector">
                <button className={`algo-btn ${algorithm === 'GA' ? 'active' : ''}`} onClick={() => setAlgorithm('GA')}>
                  Genetic Algorithm (GA)
                </button>
                <button className={`algo-btn ${algorithm === 'PSO' ? 'active' : ''}`} onClick={() => setAlgorithm('PSO')}>
                  Particle Swarm (PSO)
                </button>
              </div>

              {/* --- Optimisation objective selector --- */}
              {/* Four options controlling what the backend's fitness
                  function prioritises:
                    university  – minimise room & time conflicts
                    instructor  – respect instructor time preferences
                    student     – minimise student schedule gaps
                    all         – balanced multi-objective             */}
              <h3 style={{ marginBottom: 12, marginTop: 20 }}>Optimization Objective</h3>
              <div className="algo-selector">
                <button className={`algo-btn ${objective === 'university' ? 'active' : ''}`} onClick={() => setObjective('university')}>University</button>
                <button className={`algo-btn ${objective === 'instructor' ? 'active' : ''}`} onClick={() => setObjective('instructor')}>Instructor</button>
                <button className={`algo-btn ${objective === 'student' ? 'active' : ''}`} onClick={() => setObjective('student')}>Student</button>
                <button className={`algo-btn ${objective === 'all' ? 'active' : ''}`} onClick={() => setObjective('all')}>All Three</button>
              </div>

              {/* --- Generate action --- */}
              {/* Descriptive text + primary action button.
                  Disabled when already generating or when course data
                  has not loaded yet (courses.length === 0).           */}
              <div style={{ marginTop: 24 }}>
                <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
                  Generates the complete university schedule including all departments and both genders.
                </p>
                <button className="btn btn-primary" onClick={handleGenerate} disabled={generating || courses.length === 0}>
                  {generating ? <><span className="spinner"></span> Generating...</> : 'Generate Full Schedule'}
                </button>
              </div>
            </div>
          </>
        )}

        {/* ========================================================== */}
        {/*  PAGE 2: VIEW RESULTS                                       */}
        {/*  Only rendered when at least one generation has completed.   */}
        {/*  Contains:                                                   */}
        {/*    1. Stats grid     – 4 summary cards                      */}
        {/*    2. Schedule tabs  – one per result (label + fitness)      */}
        {/*    3. Filter bar     – gender / dept / day / instructor      */}
        {/*    4. Result count   – "Showing X of Y slots"               */}
        {/*    5. ScheduleCalendar – the week-view grid component       */}
        {/* ========================================================== */}
        {activePage === 'schedule' && scheduleResults.length > 0 && (
          <>
            <div className="page-header"><h1>University Schedule</h1></div>

            {/* ----------------------------------------------------------
                Stats grid – four cards giving a quick summary.
                  Total Slots   : size of the unfiltered slot array
                  Male Slots    : count where gender === 'Male'
                  Female Slots  : count where gender === 'Female'
                  Showing       : how many slots pass the current filters
                This is unique to the admin view; student/instructor
                dashboards do not show gender-split statistics.
            ---------------------------------------------------------- */}
            <div className="stats-grid" style={{ marginBottom: 20 }}>
              <div className="stat-card">
                <div className="stat-value">{allSlots.length}</div>
                <div className="stat-label">Total Slots</div>
              </div>
              <div className="stat-card">
                <div className="stat-value" style={{ color: '#1e3a5f' }}>{maleSlots}</div>
                <div className="stat-label">Male Slots</div>
              </div>
              <div className="stat-card">
                <div className="stat-value" style={{ color: '#7c3aed' }}>{femaleSlots}</div>
                <div className="stat-label">Female Slots</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{filteredSlots.length}</div>
                <div className="stat-label">Showing</div>
              </div>
            </div>

            {/* ----------------------------------------------------------
                Schedule tabs – one button per generated result.
                When the backend returns multiple candidates (e.g. for
                the 'all' objective it returns university, instructor,
                and student variants), the admin can switch between them.
                Each tab shows the result label plus fitness & conflict
                counts to aid comparison.
            ---------------------------------------------------------- */}
            <div className="schedule-tabs">
              {scheduleResults.map((result, idx) => (
                <button key={idx} className={`schedule-tab ${selectedTab === idx ? 'active' : ''}`} onClick={() => setSelectedTab(idx)}>
                  <div className="tab-label">{result.label}</div>
                  <div className="tab-fitness">Fitness: {result.fitness} | Conflicts: {result.conflicts}</div>
                </button>
              ))}
            </div>

            {/* ----------------------------------------------------------
                Result card – contains the filter bar and the calendar.
            ---------------------------------------------------------- */}
            <div className="card">
              {/* Card header: result label and description */}
              <div className="card-header">
                <div>
                  <h3>{currentResult?.label}</h3>
                  <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{currentResult?.description}</p>
                </div>
              </div>

              {/* --------------------------------------------------------
                  Multi-criteria filter bar (admin-exclusive feature).
                  All filters combine with logical AND in
                  getFilteredSlots().  An empty/blank value means "all".

                  Layout: horizontal flex row that wraps on small screens.
                  The instructor filter takes remaining space (flex: 1)
                  and provides a <datalist> for autocomplete suggestions.

                  A "Reset Filters" button appears when any filter is
                  active, clearing all five filter state variables.
              -------------------------------------------------------- */}
              <div style={{
                display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 16,
                padding: '12px 16px', background: 'var(--bg-table-header)',
                borderRadius: 8, border: '1px solid var(--border-color)',
                alignItems: 'center',
              }}>

                {/* --- Gender filter: toggle buttons (All / Male / Female) --- */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-secondary)' }}>Gender:</span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {['', 'Male', 'Female'].map(g => (
                      <button key={g} onClick={() => setFilterGender(g)} style={{
                        padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border-color)',
                        background: filterGender === g ? '#1e3a5f' : 'var(--bg-card)',
                        color: filterGender === g ? '#fff' : 'var(--text-primary)',
                        fontSize: '0.8rem', cursor: 'pointer', fontWeight: 500,
                      }}>
                        {g || 'All'}
                      </button>
                    ))}
                  </div>
                </div>

                {/* --- Department filter: <select> dropdown --- */}
                {/* Options are dynamically built from uniqueDepts so only
                    departments present in the current result appear.       */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-secondary)' }}>Dept:</span>
                  <select value={filterDept} onChange={e => setFilterDept(e.target.value)} style={{
                    padding: '5px 10px', borderRadius: 6, border: '1px solid var(--input-border)',
                    background: 'var(--input-bg)', color: 'var(--text-primary)', fontSize: '0.82rem',
                  }}>
                    <option value="">All</option>
                    {uniqueDepts.map(d => <option key={d} value={d}>{d}</option>)}
                  </select>
                </div>

                {/* --- Day filter: <select> dropdown --- */}
                {/* Only days that actually appear in the schedule are listed. */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-secondary)' }}>Day:</span>
                  <select value={filterDay} onChange={e => setFilterDay(e.target.value)} style={{
                    padding: '5px 10px', borderRadius: 6, border: '1px solid var(--input-border)',
                    background: 'var(--input-bg)', color: 'var(--text-primary)', fontSize: '0.82rem',
                  }}>
                    <option value="">All</option>
                    {uniqueDays.map(d => <option key={d} value={d}>{d}</option>)}
                  </select>
                </div>

                {/* --- Instructor filter: free-text search with datalist --- */}
                {/* Uses a <datalist> for browser-native autocomplete.
                    Both filterInstructorInput (display) and filterInstructor
                    (used in getFilteredSlots) are updated together so
                    the filtering is instant as the admin types.              */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 200 }}>
                  <span style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>Instructor:</span>
                  <input
                    type="text"
                    placeholder="Search..."
                    value={filterInstructorInput}
                    onChange={e => { setFilterInstructorInput(e.target.value); setFilterInstructor(e.target.value); }}
                    style={{
                      flex: 1, padding: '5px 10px', borderRadius: 6,
                      border: '1px solid var(--input-border)',
                      background: 'var(--input-bg)', color: 'var(--text-primary)',
                      fontSize: '0.82rem', outline: 'none',
                    }}
                    list="admin-instructor-list"
                  />
                  {/* Datalist provides autocomplete suggestions drawn from
                      the unique instructor names in the current result set */}
                  <datalist id="admin-instructor-list">
                    {uniqueInstructors.map(n => <option key={n} value={n} />)}
                  </datalist>
                </div>

                {/* --- Reset Filters button (only visible when filters are active) --- */}
                {(filterGender || filterDept || filterDay || filterInstructor) && (
                  <button className="btn btn-outline btn-sm" onClick={() => {
                    setFilterGender(''); setFilterDept(''); setFilterDay('');
                    setFilterInstructor(''); setFilterInstructorInput('');
                  }}>
                    Reset Filters
                  </button>
                )}
              </div>

              {/* ----------------------------------------------------------
                  Result count indicator – shows "Showing X of Y slots"
                  so the admin knows how much the filters are narrowing
                  the view.
              ---------------------------------------------------------- */}
              <div style={{ marginBottom: 12, fontSize: '0.83rem', color: 'var(--text-secondary)' }}>
                Showing <strong style={{ color: 'var(--text-primary)' }}>{filteredSlots.length}</strong> of {allSlots.length} slots
              </div>

              {/* ----------------------------------------------------------
                  ScheduleCalendar – the shared week-view grid component.
                  Receives only the filtered slots and the class time-slot
                  definitions.  The calendar itself is role-agnostic; the
                  admin specificity comes from the filter bar above it.
              ---------------------------------------------------------- */}
              <ScheduleCalendar slots={filteredSlots} timeSlots={timeSlots} />
            </div>
          </>
        )}
      </main>
    </div>
  );
}
