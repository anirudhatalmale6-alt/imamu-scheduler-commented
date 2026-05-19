/**
 * LoginPage.js — User Authentication Form
 *
 * Renders the login page for the IMAMU Course Scheduler. This is the default
 * page users see when not authenticated.
 *
 * Features:
 *   - Username and password form fields with controlled inputs
 *   - Form validation via HTML5 "required" attributes
 *   - Error display for failed login attempts (wrong credentials, server errors)
 *   - Loading state with disabled button to prevent double-submission
 *   - Automatic redirect to the user's role-specific dashboard on success
 *     (handled by App.js route configuration — once user state is set in
 *     AuthContext, the /login route redirects to the appropriate dashboard)
 *   - Link to the registration page for new users
 *
 * State Management:
 *   Uses three local state variables (useState):
 *     - form: object holding username and password values (controlled inputs)
 *     - error: string for displaying server-side error messages
 *     - loading: boolean to show a "Signing in..." indicator and disable the button
 *
 * Auth Flow:
 *   1. User fills in username + password
 *   2. Form submits → handleSubmit called
 *   3. login() from AuthContext sends POST to /api/auth/login
 *   4. On success: AuthContext stores token + user → App.js redirects to dashboard
 *   5. On failure: error message extracted from server response and displayed
 */

import React, { useState } from 'react';
// Link from React Router — renders a client-side navigation link to /register
import { Link } from 'react-router-dom';
// useAuth hook — provides the login() function from AuthContext
import { useAuth } from '../contexts/AuthContext';

/**
 * LoginPage Component
 *
 * A functional component that renders the login form inside a centered card
 * on a gradient background (styled by .auth-page and .auth-card in App.css).
 */
export default function LoginPage() {
  // Destructure the login function from the auth context
  const { login } = useAuth();

  // Form state — holds both field values in a single object.
  // Using an object (vs separate state variables) keeps related data together
  // and makes it easy to add more fields later.
  const [form, setForm] = useState({ username: '', password: '' });

  // Error message state — populated when login fails, displayed above the form
  const [error, setError] = useState('');

  // Loading flag — true while the login API call is in progress
  const [loading, setLoading] = useState(false);

  /**
   * handleSubmit — Form submission handler
   *
   * Prevents the default HTML form submission (which would cause a page reload),
   * clears any previous error, sets loading state, then calls the login function
   * from AuthContext.
   *
   * Error handling: If the API returns an error, extracts the detail message
   * from the response (FastAPI's standard error format: { detail: "message" })
   * and falls back to a generic "Login failed" message.
   *
   * The finally block ensures loading is always set back to false, regardless
   * of success or failure.
   *
   * @param {Event} e — The form submission event
   */
  const handleSubmit = async (e) => {
    e.preventDefault();     // Prevent default browser form submission (page reload)
    setError('');           // Clear any previous error message
    setLoading(true);       // Show loading indicator
    try {
      // Attempt login — on success, AuthContext updates user state,
      // which causes App.js to redirect away from /login to the dashboard
      await login(form.username, form.password);
    } catch (err) {
      // Extract error message from the server response.
      // FastAPI returns errors as { detail: "Error description" }.
      // Optional chaining (?.) safely handles cases where response is undefined
      // (e.g., network errors with no server response).
      setError(err.response?.data?.detail || 'Login failed');
    } finally {
      setLoading(false);    // Always re-enable the form after the attempt
    }
  };

  return (
    // Full-screen centered container with gradient background
    <div className="auth-page">
      {/* White card containing the login form */}
      <div className="auth-card">
        <h1>Welcome Back</h1>
        <p className="subtitle">IMAMU Course Scheduler - Sign in to continue</p>

        {/* Error message banner — only shown when error state is non-empty */}
        {error && <div className="error-msg">{error}</div>}

        {/* Login form — uses onSubmit to intercept submission */}
        <form onSubmit={handleSubmit}>
          {/* Username field — controlled input (value bound to form.username) */}
          <div className="form-group">
            <label>Username</label>
            <input
              type="text"
              value={form.username}
              onChange={e => setForm({...form, username: e.target.value})}  // Spread existing form, update username
              required  // HTML5 validation — prevents empty submission
            />
          </div>

          {/* Password field — controlled input (value bound to form.password) */}
          <div className="form-group">
            <label>Password</label>
            <input
              type="password"
              value={form.password}
              onChange={e => setForm({...form, password: e.target.value})}
              required
            />
          </div>

          {/* Submit button — disabled while loading to prevent double-submission.
              Shows "Signing in..." text during the loading state for user feedback. */}
          <button type="submit" className="btn btn-primary btn-full" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        {/* Navigation link to the registration page for users who don't have an account.
            Uses React Router's <Link> for client-side navigation (no page reload). */}
        <p className="auth-link">Don't have an account? <Link to="/register">Register</Link></p>
      </div>
    </div>
  );
}
