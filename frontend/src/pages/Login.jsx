import { useEffect, useState } from 'react';
import { api, loggedIn, logout } from '../api';

export default function Login({ onDone }) {
  const [mode, setMode] = useState('login');
  const [form, setForm] = useState({ username: '', password: '', confirm: '', email: '' });
  const [error, setError] = useState('');

  useEffect(() => {
    if (loggedIn()) onDone();
  }, [onDone]);

  async function submit(e) {
    e.preventDefault();
    setError('');
    try {
      if (mode === 'login') {
        const data = await api('/api/v1/auth/login/', {
          method: 'POST',
          auth: false,
          body: { username: form.username, password: form.password },
        });
        localStorage.setItem('pts_access', data.access);
        localStorage.setItem('pts_refresh', data.refresh);
      } else {
        if (form.password !== form.confirm) {
          setError('Passwords do not match.');
          return;
        }
        await api('/api/v1/auth/register/', {
          method: 'POST',
          auth: false,
          body: {
            username: form.username,
            email: form.email,
            password: form.password,
            password_confirm: form.confirm,
          },
        });
        setMode('login');
        setError('Registered — now log in.');
        return;
      }
      onDone();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="login">
      <h2>Paper Trading Simulator</h2>
      <p style={{ color: '#8b98b3' }}>{mode === 'login' ? 'Log in' : 'Create account'} — virtual capital, zero risk.</p>
      <form onSubmit={submit}>
        <input placeholder="Username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        {mode === 'register' && (
          <input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        )}
        <input type="password" placeholder="Password (min 12 chars)" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
        {mode === 'register' && (
          <input type="password" placeholder="Confirm password" value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} required />
        )}
        {error && <p className="error">{error}</p>}
        <button className="action" type="submit">{mode === 'login' ? 'Log in' : 'Register'}</button>
      </form>
      <p>
        <button className="action" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? 'Need an account? Register' : 'Have an account? Log in'}
        </button>{' '}
        <button className="action" onClick={logout}>Clear session</button>
      </p>
    </div>
  );
}
