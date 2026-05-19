/**
 * RegisterPage.js — User Registration Form
 *
 * Renders the account creation page for the IMAMU Course Scheduler.
 * New users provide their profile information and are automatically
 * logged in upon successful registration.
 *
 * Form Fields:
 *   - Full Name     — display name for the user profile
 *   - Username       — unique login identifier
 *   - Email          — contact email address
 *   - Password       — minimum 4 characters (enforced by minLength attribute)
 *   - Role           — student, instructor, or admin (determines which dashboard they access)
 *   - Gender         — Male or Female (used for section filtering in IMAMU's gender-separated system)
 *   - Department     — academic department (loaded dynamically from the backend API)
 *   - Level          — academic level 1-8 (only shown for students, hidden for instructors/admins)
 *
 * Data Loading:
 *   On mount, the component fetches the list of departments from the backend
 *   via dataAPI.departments() to populate the department dropdown dynamically.
 *   This ensures the form always reflects the current department list.
 *
 * Auth Flow:
 *   1. User fills in all fields and submits the form
 *   2. handleSubmit calls register() from AuthContext → POST /api/auth/register
 *   3. Backend creates the user, returns a JWT token + user object
 *   4. AuthContext stores both → App.js detects the user and redirects to their dashboard
 *
 * State Management:
 *   - form: object holding all field values (controlled inputs pattern)
 *   - departments: array of department objects fetched from the API
 *   - error: string for server-side validation errors (e.g., "Username already exists")
 *   - loading: boolean for button disabled state during form submission
 */

import React, { useState, useEffect } from 'react';
// Link — client-side navigation to login page
import { Link } from 'react-router-dom';
// useAuth — provides the register() function from AuthContext
import { useAuth } from '../contexts/AuthContext';
// dataAPI — provides the departments() endpoint for populating the dropdown
import { dataAPI } from '../services/api';

/**
 * RegisterPage Component
 *
 * A functional component that renders the multi-field registration form.
 * Uses a responsive two-column grid layout for paired fields (username/email,
 * role/gender, department/level) via the .form-row CSS class.
 */
export default function RegisterPage() {
  // Destructure the register function from the auth context
  const { register } = useAuth();

  // Form state — holds all registration field values in a single object.
  // Default values: role is 'student', gender is 'Male', level is 1.
  // Department starts empty (user must select from the dropdown).
  const [form, setForm] = useState({
    username: '', email: '', password: '', full_name: '',
    role: 'student', gender: 'Male', department: '', level: 1,
  });

  // Departments array — populated from the backend on component mount.
  // Each department object has { id, name }.
  const [departments, setDepartments] = useState([]);

  // Error message — displayed when registration fails
  const [error, setError] = useState('');

  // Loading state — prevents double-submission while API call is in progress
  const [loading, setLoading] = useState(false);

  /**
   * Department Loading Effect
   *
   * Runs once on mount (empty dependency array) to fetch the list of academic
   * departments from the backend. The response populates the department dropdown.
   *
   * Error handling: silently catches errors (.catch(() => {})) because a failed
   * department fetch should not prevent the user from seeing the form — the
   * dropdown will simply be empty, and they can try refreshing.
   */
  useEffect(() => {
    dataAPI.departments().then(res => setDepartments(res.data)).catch(() => {});
  }, []);

  /**
   * handleSubmit — Form submission handler
   *
   * Validates the form via HTML5 attributes (required, minLength, type="email"),
   * then sends the entire form object to the backend via AuthContext's register().
   *
   * On success: AuthContext stores the JWT and user → App.js redirects to dashboard.
   * On failure: extracts the error detail from the FastAPI response and displays it.
   *
   * @param {Event} e — The form submission event
   */
  const handleSubmit = async (e) => {
    e.preventDefault();     // Prevent default browser form submission
    setError('');           // Clear previous errors
    setLoading(true);       // Disable the submit button
    try {
      // Send all form fields to the backend — register() handles token storage
      await register(form);
    } catch (err) {
      // Display server-side validation errors (e.g., duplicate username)
      setError(err.response?.data?.detail || 'Registration failed');
    } finally {
      setLoading(false);    // Re-enable the submit button
    }
  };

  return (
    // Full-screen centered container with gradient background (same as LoginPage)
    <div className="auth-page">
      <div className="auth-card">
        <h1>Create Account</h1>
        <p className="subtitle">IMAMU Course Scheduler - Register to get started</p>

        {/* Error banner — displayed only when an error occurs */}
        {error && <div className="error-msg">{error}</div>}

        <form onSubmit={handleSubmit}>
          {/* Full Name — single full-width field */}
          <div className="form-group">
            <label>Full Name</label>
            <input type="text" value={form.full_name} onChange={e => setForm({...form, full_name: e.target.value})} required />
          </div>

          {/* Username + Email — side by side in a two-column row */}
          <div className="form-row">
            <div className="form-group">
              <label>Username</label>
              <input type="text" value={form.username} onChange={e => setForm({...form, username: e.target.value})} required />
            </div>
            <div className="form-group">
              <label>Email</label>
              {/* type="email" provides built-in browser validation for email format */}
              <input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} required />
            </div>
          </div>

          {/* Password — full width, with minimum length validation */}
          <div className="form-group">
            <label>Password</label>
            <input type="password" value={form.password} onChange={e => setForm({...form, password: e.target.value})} required minLength={4} />
          </div>

          {/* Role + Gender — side by side dropdown selectors */}
          <div className="form-row">
            <div className="form-group">
              <label>Role</label>
              {/* Role selector — determines which dashboard the user accesses after login */}
              <select value={form.role} onChange={e => setForm({...form, role: e.target.value})}>
                <option value="student">Student</option>
                <option value="instructor">Instructor</option>
                <option value="admin">Admin</option>
              </select>
            </div>
            <div className="form-group">
              <label>Gender</label>
              {/* Gender selector — IMAMU has gender-separated sections;
                  this determines which course sections are visible to the user */}
              <select value={form.gender} onChange={e => setForm({...form, gender: e.target.value})}>
                <option value="Male">Male</option>
                <option value="Female">Female</option>
              </select>
            </div>
          </div>

          {/* Department + Level — side by side; Level only shown for students */}
          <div className="form-row">
            <div className="form-group">
              <label>Department</label>
              {/* Departments loaded from the backend via useEffect above */}
              <select value={form.department} onChange={e => setForm({...form, department: e.target.value})}>
                <option value="">Select...</option>
                {/* Map each department object to an <option> element */}
                {departments.map(d => <option key={d.id} value={d.name}>{d.name}</option>)}
              </select>
            </div>
            {/* Conditional rendering: Level dropdown only appears when role is 'student'.
                Instructors and admins don't have an academic level.
                parseInt() ensures the value is stored as a number, not a string. */}
            {form.role === 'student' && (
              <div className="form-group">
                <label>Level</label>
                <select value={form.level} onChange={e => setForm({...form, level: parseInt(e.target.value)})}>
                  {/* IMAMU has 8 academic levels */}
                  {[1,2,3,4,5,6,7,8].map(l => <option key={l} value={l}>Level {l}</option>)}
                </select>
              </div>
            )}
          </div>

          {/* Submit button — shows loading text and is disabled during API call */}
          <button type="submit" className="btn btn-primary btn-full" disabled={loading}>
            {loading ? 'Creating Account...' : 'Create Account'}
          </button>
        </form>

        {/* Navigation link back to login page for existing users */}
        <p className="auth-link">Already have an account? <Link to="/login">Sign In</Link></p>
      </div>
    </div>
  );
}
