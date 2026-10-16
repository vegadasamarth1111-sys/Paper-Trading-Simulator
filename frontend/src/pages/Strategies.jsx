import { useEffect, useState } from 'react';
import { api } from '../api';

export default function Strategies() {
  const [strategies, setStrategies] = useState([]);
  const [signals, setSignals] = useState([]);
  const [error, setError] = useState('');

  async function load() {
    try {
      const [s, g] = await Promise.all([api('/api/v1/strategies/'), api('/api/v1/signals/')]);
      setStrategies(s);
      setSignals(g);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function control(id, action) {
    try {
      await api(`/api/v1/strategies/${id}/${action}/`, { method: 'POST' });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div>
      <h2>Strategies</h2>
      {error && <p className="error">{error}</p>}
      <div className="panel">
        <h3>Definitions</h3>
        <table>
          <thead><tr><th>Name</th><th>Capital</th><th>Active</th><th>Running</th><th></th></tr></thead>
          <tbody>
            {strategies.map((s) => (
              <tr key={s.id}>
                <td>{s.name}</td><td>{s.allocated_capital}</td>
                <td>{s.is_active ? 'yes' : 'no'}</td><td>{s.is_running ? 'yes' : 'no'}</td>
                <td>
                  <button className="action" onClick={() => control(s.id, 'start')}>Start</button>{' '}
                  <button className="action" onClick={() => control(s.id, 'stop')}>Stop</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h3>Recent signals ({signals.length})</h3>
        <table>
          <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Conf</th><th>Outcome</th></tr></thead>
          <tbody>
            {signals.map((g) => (
              <tr key={g.id}>
                <td>{g.generated_at}</td><td>{g.instrument.symbol}</td><td>{g.direction}</td>
                <td>{g.entry_price}</td><td>{g.confidence}</td>
                <td>{g.was_executed ? 'executed' : g.skip_reason || 'skipped'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
