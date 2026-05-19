/**
 * index.js — Application Entry Point
 *
 * This is the bootstrapping file for the IMAMU Course Scheduler React application.
 * It is the first JavaScript file that executes when the browser loads the bundled
 * application. Its sole responsibility is to mount the root React component (<App />)
 * into the DOM element with the id "root" (defined in public/index.html).
 *
 * React 18 Concurrent Rendering:
 *   React 18 introduced `createRoot` (from "react-dom/client") to replace the
 *   legacy `ReactDOM.render()`. `createRoot` enables React's concurrent features
 *   such as automatic batching of state updates and transitions, which improve
 *   perceived performance for the end user.
 *
 * Architecture note:
 *   The <App /> component (imported from ./App) contains all routing, context
 *   providers, and page-level components. Keeping this entry file minimal
 *   follows the single-responsibility principle — index.js handles DOM mounting
 *   only, while App.js handles application structure.
 */

import React from 'react';                       // Core React library — required for JSX transformation
import ReactDOM from 'react-dom/client';          // React 18 DOM rendering API (concurrent mode)
import App from './App';                          // Root application component (routing + providers)

// Locate the <div id="root"> element in public/index.html and create a React root.
// This is the single mounting point for the entire Single-Page Application (SPA).
const root = ReactDOM.createRoot(document.getElementById('root'));

// Render the top-level <App /> component into the DOM.
// All child components, routes, and providers are nested inside <App />.
root.render(<App />);
