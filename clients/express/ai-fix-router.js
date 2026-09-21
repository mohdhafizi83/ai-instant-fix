/**
 * AI Instant Fix — Express/Node variant
 *
 * Same-origin proxy: your Node backend forwards widget traffic to the
 * AI Fix server, keeping the AI Fix URL + JWT token server-side only.
 *
 * Setup:
 *   1. Copy this file into your project (e.g. src/ai-fix-router.js)
 *   2. Env vars: AIF_SERVER=https://fix.example.com  AIF_TOKEN=***
 *   3. app.use('/ai-fix', aiFixRouter())
 *   4. Serve widget JS from your own static dir and point
 *      data-aif-api at "https://yoursite.com/ai-fix"
 *
 * The widget calls <api>/api/tasks — this router maps /api/tasks to the
 * upstream AI Fix server.
 */
'use strict';

const express = require('express');

function aiFixRouter() {
  const router = express.Router();
  const server = (process.env.AIF_SERVER || '').replace(/\/+$/, '');
  const token = process.env.AIF_TOKEN || '';

  if (!server) {
    throw new Error('AIF_SERVER env var is required for the AI Fix router');
  }

  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;

  async function forward(req, res, method, path, body) {
    try {
      const upstream = await fetch(server + path, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(15000),
      });
      const data = await upstream.json().catch(() => ({}));
      res.status(upstream.status).json(data);
    } catch (err) {
      res.status(502).json({ error: 'upstream unreachable' });
    }
  }

  // POST /api/tasks — create a fix task
  router.post('/api/tasks', express.json({ limit: '64kb' }), async (req, res) => {
    const { prompt, url, page_url, parent_id } = req.body || {};
    if (!prompt || !url) {
      return res.status(400).json({ error: 'prompt and url are required' });
    }
    if (String(prompt).length > 5000) {
      return res.status(400).json({ error: 'prompt too long' });
    }
    await forward(req, res, 'POST', '/api/tasks', {
      user_id: req.user ? String(req.user.id) : 'guest',
      prompt: String(prompt),
      url: String(url),
      page_url: page_url ? String(page_url) : String(url),
      ...(parent_id ? { parent_id: parseInt(parent_id, 10) } : {}),
    });
  });

  // GET /api/tasks — list tasks for a page
  router.get('/api/tasks', async (req, res) => {
    const qs = new URLSearchParams();
    ['page_url', 'user_id', 'status'].forEach((k) => {
      if (req.query[k]) qs.set(k, String(req.query[k]));
    });
    qs.set('limit', String(Math.min(parseInt(req.query.limit || '50', 10), 200)));
    await forward(req, res, 'GET', `/api/tasks?${qs.toString()}`);
  });

  // GET /api/tasks/:id — single task with replies
  router.get('/api/tasks/:id', async (req, res) => {
    const id = parseInt(req.params.id, 10);
    if (!Number.isInteger(id) || id < 1) {
      return res.status(400).json({ error: 'invalid id' });
    }
    await forward(req, res, 'GET', `/api/tasks/${id}`);
  });

  return router;
}

module.exports = { aiFixRouter };
