/**
 * AuthContext.js — Authentication Context & Provider
 *
 * This module implements the authentication layer for the IMAMU Course Scheduler
 * using React's Context API pattern. It provides:
 *
 *   - Centralized auth state (user object, loading flag) accessible from any component
 *   - login() / register() / logout() functions that handle API calls, token storage,
 *     and state updates in one place
 *   - Persistent sessions via localStorage (survives page refresh)
 *
 * Architecture: React Context + Provider Pattern
 * ───────────────────────────────────────────────
 *   1. `AuthContext` is created with createContext(null) — a "container" for auth data.
 *   2. `AuthProvider` is a component that wraps the app and populates the context
 *      with the current `user`, `loading` state, and auth functions.
 *   3. `useAuth()` is a custom hook that any child component calls to access
 *      the context values — avoiding the need to import AuthContext directly.
 *
 * Token Strategy:
 *   The backend issues a JWT (JSON Web Token) on login/register. This token is:
 *     - Stored in localStorage under the key "token"
 *     - Attached to every API request via an Axios interceptor (see services/api.js)
 *     - Removed on logout or when the backend returns a 401 (token expired/invalid)
 *
 * State Flow:
 *   Mount → check localStorage for existing token/user → restore session if found
 *   Login → API call → store token + user in localStorage + state → re-render
 *   Logout → clear localStorage + state → triggers redirect via ProtectedRoute
 */

import React, { createContext, useContext, useState, useEffect } from 'react';
// authAPI contains the login/register/getMe endpoint functions (see services/api.js)
import { authAPI } from '../services/api';

/**
 * Create the AuthContext with a default value of null.
 * The actual value is provided by <AuthContext.Provider> in AuthProvider below.
 * Any component calling useContext(AuthContext) outside of an AuthProvider
 * would receive null, which helps catch misconfiguration bugs.
 */
const AuthContext = createContext(null);

/**
 * AuthProvider — Context Provider Component
 *
 * Wraps the entire application (in App.js) to make auth state and functions
 * available to all descendant components via the useAuth() hook.
 *
 * @param {ReactNode} children — All child components that need access to auth
 */
export function AuthProvider({ children }) {
  // `user` holds the authenticated user object (id, username, role, gender, etc.)
  // or null if not logged in.
  const [user, setUser] = useState(null);

  // `loading` is true while we check localStorage for an existing session on mount.
  // This prevents a brief flash of the login page before the stored session is restored.
  const [loading, setLoading] = useState(true);

  /**
   * Session Restoration Effect
   *
   * Runs once on component mount (empty dependency array []).
   * Checks localStorage for a previously stored token and user object.
   * If both exist, restores the user state so the user stays logged in
   * across page refreshes without needing to re-authenticate.
   *
   * Note: This does NOT validate the token with the server. If the token
   * has expired, the next API call will return 401, and the response
   * interceptor in api.js will clear the session and redirect to /login.
   */
  useEffect(() => {
    const token = localStorage.getItem('token');       // JWT access token
    const savedUser = localStorage.getItem('user');    // Serialized user object
    if (token && savedUser) {
      setUser(JSON.parse(savedUser));  // Restore user state from localStorage
    }
    setLoading(false);  // Session check complete — allow the UI to render
  }, []);

  /**
   * login — Authenticates a user with username and password.
   *
   * Sends credentials to the backend's /api/auth/login endpoint.
   * On success, stores the JWT token and user data in both localStorage
   * (for persistence) and React state (for reactivity).
   *
   * @param  {string} username — The user's username
   * @param  {string} password — The user's password
   * @return {Object}          — The authenticated user object from the server
   * @throws {Error}           — Axios error if login fails (wrong credentials, etc.)
   */
  const login = async (username, password) => {
    const res = await authAPI.login({ username, password });
    // Destructure the response: backend returns { access_token, user: {...} }
    const { access_token, user: userData } = res.data;
    localStorage.setItem('token', access_token);           // Persist JWT for future requests
    localStorage.setItem('user', JSON.stringify(userData)); // Persist user profile data
    setUser(userData);  // Update React state → triggers re-render across the app
    return userData;
  };

  /**
   * register — Creates a new user account and logs them in.
   *
   * Sends registration data (username, email, password, role, gender,
   * department, level) to the backend's /api/auth/register endpoint.
   * On success, automatically logs the user in (same token storage as login).
   *
   * @param  {Object} data — Registration form data object
   * @return {Object}      — The newly created user object from the server
   * @throws {Error}       — Axios error if registration fails (duplicate username, etc.)
   */
  const register = async (data) => {
    const res = await authAPI.register(data);
    const { access_token, user: userData } = res.data;
    localStorage.setItem('token', access_token);
    localStorage.setItem('user', JSON.stringify(userData));
    setUser(userData);
    return userData;
  };

  /**
   * logout — Ends the current user session.
   *
   * Clears the JWT token and user data from both localStorage and React state.
   * Setting user to null causes ProtectedRoute components to redirect to /login.
   *
   * Note: This is a client-side only logout. The JWT remains valid on the server
   * until it expires. For production use, consider adding a server-side token
   * blacklist or using refresh token rotation.
   */
  const logout = () => {
    localStorage.removeItem('token');   // Remove persisted JWT
    localStorage.removeItem('user');    // Remove persisted user profile
    setUser(null);                      // Clear React state → triggers redirect to /login
  };

  // Provide the auth state and functions to all child components.
  // Any component using useAuth() will re-render when `user` or `loading` changes.
  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

/**
 * useAuth — Custom Hook for Accessing Auth Context
 *
 * A convenience wrapper around useContext(AuthContext).
 * Components call `const { user, login, logout } = useAuth();` instead of
 * importing both AuthContext and useContext.
 *
 * @return {Object} — { user, loading, login, register, logout }
 *
 * Usage example:
 *   const { user, logout } = useAuth();
 *   if (user) { console.log(`Logged in as ${user.username}`); }
 */
export function useAuth() {
  return useContext(AuthContext);
}
