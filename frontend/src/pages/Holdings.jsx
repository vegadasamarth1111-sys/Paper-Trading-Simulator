import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';

// Holdings = delivery (CNC) positions: invested vs current + returns.
export default function Holdings() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    api('/api/v1/positions/')
      .then((p) => setRows(p.filter((x) => x.product_type === 'CNC')))
      .catch((err) => setError(err.message));
  }, []);

  const invested = rows.reduce((s, p) => s + Number(p.average_entry_price) * Math.abs(p.quantity), 0);
  const current = rows.reduce((s, p) => s + Number(p.last_price) * Math.abs(p.quantity), 0);
  const pnl = current - invested;
  const pct = invested ? ((pnl / invested) * 100).toFixed(2) : '0.00';

  return (
    <div>
      <h2>Holdings ({rows.length})</h2>
      {error && <p className="error">{error}</p>}
      <div className="panel">
        <div className="invest">₹{current.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</div>
        <div className="kv"><span className="muted">Invested value</span><span>₹{invested.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span></div>
        <div className="kv"><span className="muted">Total returns</span>
          <span className={pnl >= 0 ? 'up' : 'down'}>₹{pnl.toLocaleString('en-IN', { maximumFractionDigits: 2 })} ({pct}%)</span></div>
      </div>
      <div className="panel">
        <table>
          <thead><tr><th>Company</th><th>Market price</th><th>Returns</th><th>Current (Invested)</th></tr></thead>
          <tbody>
            {rows.map((p) => {
              const inv = Number(p.average_entry_price) * Math.abs(p.quantity);
              const cur = Number(p.last_price) * Math.abs(p.quantity);
              return (
                <tr key={p.id} onClick={() => navigate(`/symbol/${p.instrument.symbol}`)} style={{ cursor: 'pointer' }}>
                  <td><b>{p.instrument.symbol}</b><br /><span className="muted">{Math.abs(p.quantity)} shares • Avg. ₹{p.average_entry_price}</span></td>
                  <td>₹{p.last_price}</td>
                  <td className={Number(p.unrealized_pnl) >= 0 ? 'up' : 'down'}>{p.unrealized_pnl}</td>
                  <td>₹{cur.toLocaleString('en-IN', { maximumFractionDigits: 2 })}<br /><span className="muted">₹{inv.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <p className="muted">No delivery holdings yet — buy CNC to start one.</p>}
      </div>
    </div>
  );
}
