/**
 * App.js — Main Application Component
 *
 * This is the root component of the IMAMU Course Scheduler. It orchestrates:
 *
 *   1. Authentication Context (AuthProvider) — wraps the entire app so that any
 *      descendant component can access the current user, login/logout functions,
 *      and loading state via the useAuth() hook.
 *
 *   2. Client-Side Routing (BrowserRouter + Routes) — uses React Router v6 to
 *      map URL paths to page components. The routing is role-aware: students,
 *      instructors, and admins each get their own dashboard at distinct paths.
 *
 *   3. Dark Mode Toggle — persists the user's theme preference in localStorage
 *      and applies it to the <html> element via a data-theme attribute. CSS
 *      variables in App.css respond to this attribute for theming.
 *
 *   4. Route Protection (ProtectedRoute) — a guard component that checks
 *      authentication status and role-based authorization before rendering
 *      protected pages. Unauthorized users are redirected appropriately.
 *
 * Component Hierarchy:
 *   <App>
 *     <AuthProvider>              ← provides auth context to all children
 *       <BrowserRouter>           ← enables declarative routing
 *         <DarkModeButton />      ← fixed-position toggle (top-right corner)
 *         <AppRoutes />           ← defines all route-to-component mappings
 *       </BrowserRouter>
 *     </AuthProvider>
 *   </App>
 */

import React, { useState, useEffect } from 'react';
// React Router v6 imports:
//   BrowserRouter — provides the routing context using the HTML5 History API
//   Routes / Route — declarative route definitions
//   Navigate — programmatic redirect component (replaces <Redirect> from v5)
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
// Auth context provider and hook — see contexts/AuthContext.js
import { AuthProvider, useAuth } from './contexts/AuthContext';
// Page components — each renders a full dashboard for the respective role
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import StudentDashboard from './pages/StudentDashboard';
import InstructorDashboard from './pages/InstructorDashboard';
import AdminDashboard from './pages/AdminDashboard';
// Global stylesheet containing CSS variables, layout rules, and component styles
import './App.css';

/**
 * ProtectedRoute — Route Guard Component
 *
 * Wraps a child component and enforces two levels of access control:
 *   1. Authentication: if the user is not logged in, redirect to /login.
 *   2. Authorization: if the user's role is not in `allowedRoles`, redirect
 *      them to their own role-specific dashboard instead.
 *
 * @param {ReactNode} children      — The protected page component to render
 * @param {string[]}  allowedRoles  — Array of roles permitted to view this route
 *                                     e.g., ['student'], ['admin'], ['instructor']
 */
function ProtectedRoute({ children, allowedRoles }) {
  // Access current user and loading state from AuthContext
  const { user, loading } = useAuth();

  // While auth state is being restored from localStorage, show a loading indicator
  // to prevent a flash of the login page on refresh
  if (loading) return <div className="loading">Loading...</div>;

  // If no authenticated user exists, redirect to the login page
  if (!user) return <Navigate to="/login" />;

  // If the user's role is not in the allowed list, redirect them to their
  // own dashboard rather than showing an "unauthorized" error
  if (allowedRoles && !allowedRoles.includes(user.role)) {
    if (user.role === 'admin') return <Navigate to="/admin" />;
    if (user.role === 'instructor') return <Navigate to="/instructor" />;
    return <Navigate to="/student" />;  // default fallback for students
  }

  // All checks passed — render the protected content
  return children;
}

/**
 * getHomeRoute — Maps a user role string to its dashboard URL path.
 *
 * Used after login and for catch-all route redirects so that each role
 * lands on the correct dashboard.
 *
 * @param  {string} role — 'admin', 'instructor', or 'student'
 * @return {string}      — The URL path for that role's dashboard
 */
function getHomeRoute(role) {
  if (role === 'admin') return '/admin';
  if (role === 'instructor') return '/instructor';
  return '/student';  // default for students and unknown roles
}

/**
 * AppRoutes — Defines all application routes.
 *
 * This component must be rendered inside both <AuthProvider> and <BrowserRouter>
 * so that it can access auth state (via useAuth) and routing context.
 *
 * Route configuration:
 *   /login           — Login page (redirects to dashboard if already logged in)
 *   /register        — Registration page (redirects to dashboard if already logged in)
 *   /student/*       — Student dashboard (protected, students only)
 *   /instructor/*    — Instructor dashboard (protected, instructors only)
 *   /admin/*         — Admin dashboard (protected, admins only)
 *   * (catch-all)    — Redirects to the appropriate page based on auth status
 *
 * The /* suffix on dashboard routes allows nested routing within each dashboard
 * if sub-routes are added in the future.
 */
function AppRoutes() {
  const { user, loading } = useAuth();

  // Show loading indicator while auth state is being restored
  if (loading) return <div className="loading">Loading...</div>;

  return (
    <Routes>
      {/* Login page: if already authenticated, skip to dashboard */}
      <Route path="/login" element={user ? <Navigate to={getHomeRoute(user.role)} /> : <LoginPage />} />

      {/* Register page: if already authenticated, skip to dashboard */}
      <Route path="/register" element={user ? <Navigate to={getHomeRoute(user.role)} /> : <RegisterPage />} />

      {/* Student dashboard — only accessible by users with the 'student' role */}
      <Route path="/student/*" element={<ProtectedRoute allowedRoles={['student']}><StudentDashboard /></ProtectedRoute>} />

      {/* Instructor dashboard — only accessible by users with the 'instructor' role */}
      <Route path="/instructor/*" element={<ProtectedRoute allowedRoles={['instructor']}><InstructorDashboard /></ProtectedRoute>} />

      {/* Admin dashboard — only accessible by users with the 'admin' role */}
      <Route path="/admin/*" element={<ProtectedRoute allowedRoles={['admin']}><AdminDashboard /></ProtectedRoute>} />

      {/* Catch-all route: redirect to dashboard if logged in, or login page if not */}
      <Route path="*" element={<Navigate to={user ? getHomeRoute(user.role) : '/login'} />} />
    </Routes>
  );
}

/**
 * App — Root Component
 *
 * Manages the dark/light theme toggle and wraps the entire application in
 * the AuthProvider and BrowserRouter providers.
 *
 * State Management Pattern:
 *   - Uses `useState` with a lazy initializer (callback form) to read the
 *     initial dark mode preference from localStorage only once on mount.
 *   - Uses `useEffect` to synchronize the theme state to both the DOM
 *     (data-theme attribute on <html>) and localStorage whenever it changes.
 *
 * Provider Nesting Order:
 *   AuthProvider must be the outermost provider because BrowserRouter's
 *   children (AppRoutes) need access to auth state. The dark mode toggle
 *   button sits inside BrowserRouter but outside AppRoutes so it appears
 *   on every page (fixed position, top-right corner).
 */
function App() {
  // Initialize dark mode state from localStorage.
  // The callback form of useState ensures localStorage is read only once (on mount),
  // not on every re-render.
  const [dark, setDark] = useState(() => localStorage.getItem('darkMode') === 'true');

  // Synchronize theme changes to DOM and localStorage.
  // The data-theme attribute is used by CSS variables in App.css to swap
  // between light and dark color palettes.
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    localStorage.setItem('darkMode', dark);
  }, [dark]); // Re-run only when `dark` state changes

  return (
    <AuthProvider>
      <BrowserRouter>
        {/* Dark mode toggle button — fixed position, always visible on all pages.
            Uses a circular button with sun/moon emoji to indicate current mode.
            The toggle uses a functional updater (d => !d) to safely flip the boolean. */}
        <button
          onClick={() => setDark(d => !d)}
          title={dark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          style={{
            position: 'fixed', top: 12, right: 16, zIndex: 9999,  // stays above all content
            width: 38, height: 38, borderRadius: '50%',            // circular shape
            border: 'none', cursor: 'pointer',
            background: dark ? '#374151' : '#e5e7eb',              // background matches current theme
            fontSize: '1.1rem',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: '0 2px 8px rgba(0,0,0,0.2)',               // subtle shadow for depth
            transition: 'background 0.2s',                          // smooth color transition
          }}
        >
          {/* Show sun icon in dark mode (click to go light), moon in light mode (click to go dark) */}
          {dark ? '☀️' : '🌙'}
        </button>

        {/* Render all route definitions */}
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
