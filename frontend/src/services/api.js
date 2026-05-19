/**
 * api.js — Centralized HTTP Client & API Service Layer
 *
 * This module configures a shared Axios instance and exports API service
 * objects that encapsulate all HTTP calls to the IMAMU Course Scheduler backend.
 *
 * Architecture:
 * ─────────────
 *   Axios Instance (singleton)
 *     ├── Base URL Configuration   — from environment variable or same-origin
 *     ├── Request Interceptor      — attaches JWT token to every outgoing request
 *     └── Response Interceptor     — handles 401 errors globally (token expiry)
 *
 *   API Service Modules:
 *     ├── authAPI          — login, register, get current user
 *     ├── courseAPI         — CRUD operations for courses
 *     ├── sectionAPI        — list and create course sections
 *     ├── registrationAPI   — student course registration/unregistration
 *     ├── scheduleAPI       — schedule generation, save, retrieve, delete
 *     └── dataAPI           — reference data (departments, instructors, rooms, etc.)
 *
 * Why a Centralized API Layer?
 *   - All HTTP configuration lives in one file (base URL, headers, auth, error handling)
 *   - Components never deal with raw axios calls or token management
 *   - Changing the backend URL or adding headers only requires editing this file
 *   - Consistent error handling: 401 responses always redirect to login
 */

import axios from 'axios';

/**
 * API Base URL Configuration
 *
 * Uses the REACT_APP_API_URL environment variable if set (e.g., for production
 * or staging environments where the API lives on a different domain/port).
 * Falls back to an empty string, which means requests go to the same origin
 * as the frontend (useful in development with a proxy or in production when
 * both frontend and backend are served from the same domain).
 */
const API_BASE = process.env.REACT_APP_API_URL || '';

/**
 * Create the Axios Instance
 *
 * This instance is shared across all API modules below. It carries the
 * base URL and both interceptors. Using a custom instance (rather than
 * the global axios default) prevents conflicts if other libraries also
 * use Axios with different configurations.
 */
const api = axios.create({
  baseURL: API_BASE,
});

/**
 * Request Interceptor — JWT Token Injection
 *
 * Runs before every outgoing HTTP request. Retrieves the JWT token from
 * localStorage and attaches it to the Authorization header in the
 * standard "Bearer <token>" format.
 *
 * This ensures that all API calls to protected endpoints include the
 * authentication token without the caller needing to manage headers manually.
 *
 * Flow: Component calls courseAPI.list() → Axios prepares request →
 *       This interceptor adds the token → Request is sent to server
 */
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');  // Retrieve stored JWT
  if (token) {
    // Attach the token using the Bearer authentication scheme (RFC 6750)
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;  // Return the modified config to proceed with the request
});

/**
 * Response Interceptor — Global 401 Error Handler
 *
 * Runs after every HTTP response. Successful responses pass through unchanged.
 * If the server returns a 401 Unauthorized status (meaning the JWT is expired,
 * invalid, or missing), this interceptor:
 *   1. Clears the stored token and user from localStorage
 *   2. Redirects the browser to the /login page using a hard navigation
 *
 * This provides a single, consistent place to handle session expiry across
 * the entire application rather than adding try/catch logic in every component.
 *
 * Note: The hard redirect (window.location.href) is used instead of React
 * Router's navigate() because the interceptor runs outside the React
 * component tree and doesn't have access to the router context.
 */
api.interceptors.response.use(
  (response) => response,  // Success: pass through unchanged
  (error) => {
    if (error.response?.status === 401) {
      // Session expired or token invalid — clear stored credentials
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      // Hard redirect to login page (bypasses React Router)
      window.location.href = '/login';
    }
    // Re-throw the error so individual catch blocks can still handle it
    // (e.g., to show "wrong password" messages on the login form)
    return Promise.reject(error);
  }
);

// ============================================================================
// API Service Modules
//
// Each module groups related endpoints into an object with descriptive method
// names. All methods return Axios promises that resolve to { data, status, ... }.
// Components destructure the response: `const res = await courseAPI.list();`
// and then access `res.data` for the payload.
// ============================================================================

/**
 * authAPI — Authentication Endpoints
 *
 * Handles user registration, login, and session verification.
 * These endpoints are public (register/login) or require a valid JWT (getMe).
 */
export const authAPI = {
  register: (data) => api.post('/api/auth/register', data),  // POST: create new user account
  login: (data) => api.post('/api/auth/login', data),        // POST: authenticate and receive JWT
  getMe: () => api.get('/api/auth/me'),                      // GET: fetch current user profile (JWT required)
};

/**
 * courseAPI — Course Management Endpoints
 *
 * Full CRUD for academic courses. Used by students to browse courses
 * and by admins to manage the course catalog.
 *
 * @param {Object} params — Query parameters for filtering (e.g., department, level)
 */
export const courseAPI = {
  list: (params) => api.get('/api/courses', { params }),      // GET: list courses (with optional filters)
  get: (id) => api.get(`/api/courses/${id}`),                 // GET: single course by ID
  create: (data) => api.post('/api/courses', data),           // POST: create a new course
  update: (id, data) => api.put(`/api/courses/${id}`, data),  // PUT: update an existing course
  delete: (id) => api.delete(`/api/courses/${id}`),           // DELETE: remove a course
};

/**
 * sectionAPI — Course Section Endpoints
 *
 * Sections represent specific class offerings of a course (e.g., Section A
 * for Male students, Section B for Female students). Each section has its
 * own capacity, instructor, and time slots.
 */
export const sectionAPI = {
  list: (params) => api.get('/api/sections', { params }),  // GET: list sections (filterable)
  create: (data) => api.post('/api/sections', data),       // POST: create a new section
};

/**
 * registrationAPI — Student Course Registration Endpoints
 *
 * Manages the relationship between students and course sections.
 * Students register for specific sections (not courses directly) to
 * ensure correct gender filtering and capacity management.
 */
export const registrationAPI = {
  register: (sectionId) => api.post(`/api/registration/register/${sectionId}`),    // POST: enroll in a section
  unregister: (sectionId) => api.delete(`/api/registration/unregister/${sectionId}`), // DELETE: drop a section
  myRegistrations: () => api.get('/api/registration/my-registrations'),             // GET: list student's current registrations
};

/**
 * scheduleAPI — Schedule Generation & Management Endpoints
 *
 * The core scheduling functionality. The generate endpoint sends selected
 * courses and algorithm preferences to the backend, which runs optimization
 * algorithms (Genetic Algorithm or Particle Swarm Optimization) to produce
 * conflict-minimized timetables.
 *
 * @param {Object} data — { selected_course_ids, algorithm, objective, preferred_gender }
 */
export const scheduleAPI = {
  generate: (data) => api.post('/api/schedule/generate', data),  // POST: run scheduling algorithm
  save: (data) => api.post('/api/schedule/save', data),          // POST: persist a generated schedule
  getSaved: () => api.get('/api/schedule/saved'),                // GET: list saved schedules for current user
  deleteSaved: (id) => api.delete(`/api/schedule/saved/${id}`),  // DELETE: remove a saved schedule
};

/**
 * dataAPI — Reference Data Endpoints
 *
 * Fetches lookup/reference data used for dropdowns, filters, and the
 * schedule calendar. These are typically read-only lists that don't
 * change frequently.
 */
export const dataAPI = {
  departments: () => api.get('/api/departments'),   // GET: list academic departments
  instructors: () => api.get('/api/instructors'),   // GET: list all instructors
  rooms: () => api.get('/api/rooms'),               // GET: list available classrooms
  days: () => api.get('/api/days'),                 // GET: list academic days (SUN-THU)
  timeslots: () => api.get('/api/timeslots'),       // GET: list time slot definitions
};

// Export the configured Axios instance as the default export.
// This allows other modules to make custom API calls if needed,
// while still benefiting from the interceptors and base URL.
export default api;
