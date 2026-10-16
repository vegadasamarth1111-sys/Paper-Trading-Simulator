import { useEffect, useState } from 'react';
import { api } from '../api';

export default function Risk() {
  const [configs, setConfigs] = useState([]);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState('');
  const [halted, setHalted] = useState(false);

  async function load() {
    try {
      const [c, e, a] = await Promise.all([
        api('/api/v1/risk/'),
        api('/api/v1/risk-events/'),
        api('/api/v1/account/'),
      ]);
      setConfigs(c);
      setEvents(e);
      setHalted(a.kill_switch_active);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function kill() {
    if (!window.confirm('HALT ALL TRADING? This cancels orders and squares off positions.')) return;
    try {
      await api('/api/v1/kill-switch/', { method: 'POST', body: { reason: 'dashboard button' } });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function release() {
    try {
      await api('/api/v1/kill-switch/release/', { method: 'POST', body: {} });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div>
      <h2>Risk</h2>
      {error && <p className="error">{error}</p>}
      <div className="panel">
        <h3>Kill switch {halted && <span className="neg">(HALTED)</span>}</h3>
        {!halted
          ? <button className="action danger kill" onClick={kill}>HALT ALL TRADING</button>
          : <button className="action kill" onClick={release}>Release halt</button>}
      </div>
      <div className="panel">
        <h3>Limits</h3>
        <table>
          <thead><tr><th>Rule</th><th>Enabled</th><th>Value</th><th>%</th></tr></thead>
          <tbody>
            {configs.map((c) => (
              <tr key={c.id}>
                <td>{c.rule_display}</td><td>{c.is_enabled ? 'yes' : 'no'}</td>
                <td>{c.limit_value}</td><td>{c.limit_percentage ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h3>Recent risk events ({events.length})</h3>
        <table>
          <thead><tr><th>Time</th><th>Rule</th><th>Action</th><th>Message</th></tr></thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td>{e.timestamp}</td><td>{e.rule_display}</td><td>{e.action}</td><td>{e.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
