/**
 * AI Instant Fix — React variant
 *
 * Wraps the universal widget. The widget itself is framework-agnostic;
 * this component just manages its lifecycle with React props.
 *
 * Usage:
 *   import { AiInstantFix } from './AiInstantFix';
 *
 *   <AiInstantFix
 *     api="https://fix.example.com"
 *     userId="admin"
 *     token={jwtToken}          // optional, from POST /api/auth/login
 *     theme="#247b70"
 *   />
 *
 * No npm package needed — copy this file into your project.
 */
import React, { useEffect, useRef } from 'react';

export function AiInstantFix({ api, userId = 'anonymous', token = '', theme = '#247b70' }) {
  const loadedRef = useRef(false);

  useEffect(() => {
    if (loadedRef.current || !api) return;
    loadedRef.current = true;

    const s = document.createElement('script');
    s.src = `${api.replace(/\/+$/, '')}/widget/ai-instant-fix.js`;
    s.dataset.aifApi = api;
    s.dataset.aifUserId = userId;
    if (token) s.dataset.aifToken = token;
    if (theme) s.dataset.aifTheme = theme;
    document.body.appendChild(s);

    // Widget is a singleton (window.__AIF_LOADED__); unmounting the
    // component does not remove the floating widget by design.
    return () => {
      // Optional hard cleanup:
      // const root = document.getElementById('aif-root');
      // const styles = document.getElementById('aif-styles');
      // if (root) root.remove();
      // if (styles) styles.remove();
      // delete window.__AIF_LOADED__;
    };
  }, [api, userId, token, theme]);

  return null; // widget renders itself into document.body
}

/**
 * Headless helper: use the AI Fix REST API directly from React state
 * without the floating widget (build your own UI on top).
 */
export function useAiInstantFix({ api, userId = 'anonymous', token = '' }) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const base = (api || '').replace(/\/+$/, '');

  return {
    createTask: (prompt, url, parentId = null) =>
      fetch(`${base}/api/tasks`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          user_id: userId,
          prompt,
          url,
          page_url: url,
          ...(parentId ? { parent_id: parentId } : {}),
        }),
      }).then((r) => r.json()),

    listTasks: (pageUrl, limit = 50) =>
      fetch(`${base}/api/tasks?page_url=${encodeURIComponent(pageUrl)}&limit=${limit}`, { headers })
        .then((r) => r.json()),

    getTask: (id) => fetch(`${base}/api/tasks/${id}`, { headers }).then((r) => r.json()),
  };
}
