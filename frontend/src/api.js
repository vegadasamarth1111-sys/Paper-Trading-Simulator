// API client: JWT access token on every call, refresh on 401, logout on failure.
// Tokens live in localStorage (paper-trading dashboard threat model — no
// real money moves here; production hardens this to httpOnly cookies).

const API = '';

function tokens() {
  return {
    access: localStorage.getItem('pts_access'),
    refresh: localStorage.getItem('pts_refresh'),
  };
}

export function loggedIn() {
  return !!tokens().access;
}

export function logout() {
  localStorage.removeItem('pts_access');
  localStorage.removeItem('pts_refresh');
  window.location.hash = '#/login';
}

async function refreshAccess() {
  const { refresh } = tokens();
  if (!refresh) return false;
  const res = await fetch(`${API}/api/v1/auth/refresh/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh }),
  });
  if (!res.ok) return false;
  const data = await res.json();
  localStorage.setItem('pts_access', data.access);
  if (data.refresh) localStorage.setItem('pts_refresh', data.refresh);
  return true;
}

export async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth) headers.Authorization = `Bearer ${tokens().access}`;
  let res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && auth && (await refreshAccess())) {
    headers.Authorization = `Bearer ${tokens().access}`;
    res = await fetch(`${API}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  }
  if (res.status === 401) {
    logout();
    throw new Error('Session expired — please log in again.');
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const msg = (data && (data.errors || data.detail)) || `Request failed (${res.status})`;
    throw new Error(Array.isArray(msg) ? msg.join('; ') : msg);
  }
  return data;
}

// Instrument search (envelope: {success, data:[...]}).
export async function searchInstruments(q) {
  const res = await api(`/api/v1/instruments/?search=${encodeURIComponent(q)}`);
  return res.data || res;
}

// Live prices via fetch-driven SSE (EventSource cannot send Authorization
// headers). Reconnects automatically; the server closes each chunk (~60s)
// so no connection lives forever.
export async function* priceStream(symbols, { signal } = {}) {
  const headers = { Authorization: `Bearer ${tokens().access}`, Accept: 'text/event-stream' };
  const params = new URLSearchParams({ symbols: symbols.join(','), single: '0' });
  while (!signal?.aborted) {
    const res = await fetch(`${API}/api/v1/stream/prices/?${params}`, { headers, signal });
    if (!res.ok || !res.body) throw new Error(`Stream failed (${res.status})`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const frames = buf.split('\n\n');
      buf = frames.pop();
      for (const frame of frames) {
        const line = frame.split('\n').find((l) => l.startsWith('data: '));
        if (line) yield JSON.parse(line.slice(6));
      }
    }
    // Chunk ended (server closes every ~60s) — loop reconnects.
  }
}
