/**
 * ScheduleCalendar.js — Visual Timetable Grid Component
 *
 * Renders a weekly timetable as an interactive grid with:
 *   - Columns: days of the week (SUN through THU, matching IMAMU's academic week)
 *   - Rows: time slots (e.g., 08:25-09:15, 09:20-10:10, etc.)
 *   - Cells: color-coded course event cards showing course code, name, section, room, and instructor
 *
 * This component is reused across all three dashboards (Student, Instructor, Admin)
 * and in the Saved Schedules view. It is a purely presentational (stateless) component
 * that receives its data entirely through props.
 *
 * Props:
 *   @param {Array}  slots     — Array of schedule slot objects, each with:
 *                                { day, time, course_code, course_name, section_id, room, instructor_name }
 *   @param {Array}  timeSlots — Array of time slot labels from the backend (e.g., "08:25-09:15").
 *                                If empty, the component derives time slots from the data or uses defaults.
 *
 * Rendering Strategy:
 *   Uses CSS Grid with a dynamic --days custom property to set the number of columns.
 *   The grid is structured as: [Time Label] [Day1] [Day2] ... [DayN], repeated per time slot row.
 *   Each cell may contain zero or more event cards (for classes scheduled in that day+time).
 *
 * Color Coding:
 *   Each unique course_code is assigned a distinct color from a predefined 10-color palette.
 *   Colors are assigned on first encounter and cached in a local lookup object, ensuring
 *   the same course always gets the same color within a single render.
 */

import React from 'react';

// Academic week days for IMAMU (Saudi Arabia) — Sunday through Thursday
const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU'];

// Full day names for the calendar column headers
const DAY_NAMES = { SUN: 'Sunday', MON: 'Monday', TUE: 'Tuesday', WED: 'Wednesday', THU: 'Thursday' };

/**
 * ScheduleCalendar — Main Timetable Grid Component
 *
 * Renders a responsive CSS Grid calendar displaying scheduled course events.
 * The component handles three cases for time slot determination:
 *   1. If timeSlots prop is provided (from backend), use those labels
 *   2. If no timeSlots but slots data exists, derive times from the data
 *   3. If neither, fall back to hardcoded IMAMU default time slots
 *
 * @param {Object}  props
 * @param {Array}   props.slots     — Schedule slot data to display (default: [])
 * @param {Array}   props.timeSlots — Time slot labels for row headers (default: [])
 */
export default function ScheduleCalendar({ slots = [], timeSlots = [] }) {
  // Extract unique time values from the slot data and sort them chronologically.
  // This is used as a fallback when no explicit timeSlots prop is provided.
  const usedTimes = [...new Set(slots.map(s => s.time))].sort();

  // Determine which time labels to display as row headers.
  // Priority: 1) Backend-provided timeSlots → 2) Times from data → 3) Hardcoded defaults
  const displayTimes = timeSlots.length > 0
    ? timeSlots.map(t => t.label || t)  // timeSlots may be objects with a label property or plain strings
    : (usedTimes.length > 0 ? usedTimes : ['08:25-09:15', '09:20-10:10', '10:15-11:05', '11:10-12:00', '12:30-13:20', '13:25-14:15', '14:20-15:10', '15:40-16:30', '16:35-17:25']);

  /**
   * getSlotForCell — Finds all schedule slots for a specific day + time combination.
   *
   * A cell can contain multiple events if, for example, the full university schedule
   * has two different sections scheduled in the same time slot (different rooms).
   *
   * @param  {string} day  — Day abbreviation (e.g., 'SUN')
   * @param  {string} time — Time slot label (e.g., '08:25-09:15')
   * @return {Array}       — Matching slot objects for that cell
   */
  const getSlotForCell = (day, time) => {
    return slots.filter(s => s.day === day && s.time === time);
  };

  // ── Color Assignment System ──
  // Assigns a unique color to each course code for visual distinction.
  // Uses a closure-based cache: once a course gets a color, it keeps it for the
  // entire render cycle. The palette has 10 colors; courses beyond 10 wrap around.

  const colors = {};       // Cache: courseCode → color object
  let colorIdx = 0;        // Tracks which palette color to assign next

  // Color palette — 10 carefully chosen color schemes with background, border, and text colors.
  // Each scheme provides sufficient contrast for readability in both light and dark contexts.
  const palette = [
    { bg: '#dbeafe', border: '#2563eb', text: '#1e40af' },   // Blue
    { bg: '#dcfce7', border: '#16a34a', text: '#166534' },   // Green
    { bg: '#fef3c7', border: '#d97706', text: '#92400e' },   // Amber
    { bg: '#f3e8ff', border: '#7c3aed', text: '#5b21b6' },   // Purple
    { bg: '#ffe4e6', border: '#e11d48', text: '#9f1239' },   // Rose
    { bg: '#e0f2fe', border: '#0284c7', text: '#075985' },   // Sky
    { bg: '#fce7f3', border: '#db2777', text: '#9d174d' },   // Pink
    { bg: '#ecfdf5', border: '#059669', text: '#065f46' },   // Emerald
    { bg: '#fff7ed', border: '#ea580c', text: '#9a3412' },   // Orange
    { bg: '#f5f3ff', border: '#6d28d9', text: '#4c1d95' },   // Violet
  ];

  /**
   * getColor — Returns the color scheme for a given course code.
   *
   * If the course has been seen before, returns its cached color.
   * If it's a new course, assigns the next color from the palette
   * using modular arithmetic to cycle through the 10 available colors.
   *
   * @param  {string} courseCode — e.g., "CS101", "MATH201"
   * @return {Object}           — { bg, border, text } hex color strings
   */
  const getColor = (courseCode) => {
    if (!colors[courseCode]) {
      colors[courseCode] = palette[colorIdx % palette.length];  // Modular wrap-around
      colorIdx++;
    }
    return colors[courseCode];
  };

  return (
    <div className="schedule-calendar">
      {/* CSS Grid container — the --days custom property controls column count.
          Grid structure: 1 time column + N day columns.
          The gap: 1px with background on the parent creates grid lines between cells. */}
      <div className="calendar-grid" style={{ '--days': DAYS.length }}>

        {/* ── Header Row ── */}
        {/* First header cell: "Time" label for the time column */}
        <div className="calendar-header">Time</div>
        {/* Day name headers: Sunday through Thursday */}
        {DAYS.map(d => (
          <div key={d} className="calendar-header">{DAY_NAMES[d] || d}</div>
        ))}

        {/* ── Data Rows ── */}
        {/* One row per time slot, each containing a time label + cells for each day */}
        {displayTimes.map(time => (
          // React.Fragment groups the time cell + day cells without adding extra DOM nodes.
          // The key ensures efficient re-rendering when the time slot list changes.
          <React.Fragment key={time}>
            {/* Time label cell (leftmost column) */}
            <div className="calendar-time">{time}</div>

            {/* Day cells — one for each day in the academic week */}
            {DAYS.map(day => {
              // Find all scheduled events for this specific day + time combination
              const cellSlots = getSlotForCell(day, time);
              return (
                <div key={`${day}-${time}`} className="calendar-cell">
                  {/* Render each event as a color-coded card within the cell */}
                  {cellSlots.map((slot, i) => {
                    const color = getColor(slot.course_code);  // Get/assign color for this course
                    return (
                      <div key={i} className="calendar-event" style={{
                        background: color.bg,               // Light-colored card background
                        borderLeftColor: color.border,      // Thick left border in accent color
                      }}>
                        {/* Course code — prominent, colored text (e.g., "CS101") */}
                        <div className="event-course" style={{ color: color.text }}>
                          {slot.course_code}
                        </div>
                        {/* Course name (e.g., "Introduction to Computer Science") */}
                        <div className="event-detail">{slot.course_name}</div>
                        {/* Section identifier with reduced opacity (e.g., "Section A") */}
                        <div className="event-detail" style={{ opacity: 0.75 }}>{slot.section_id}</div>
                        {/* Room and instructor (e.g., "Room 201 | Dr. Ahmed") */}
                        <div className="event-detail">{slot.room} | {slot.instructor_name}</div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
