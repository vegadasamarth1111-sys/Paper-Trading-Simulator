import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';

// Positions = intraday + derivatives (everything that is not delivery).
export default function Positions() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    api('/api/v1/positions/')
      .then((p) => setRows(p.filter((x) => x.product_type !== 'CNC')))
      .catch((err) => setError(err.message));
  }, []);

  const upl = rows.reduce((s, p) => s + Number(p.unrealized_pnl), 0);

  return (
    <div>
      <h2>Positions</h2>
      {error && <p className="error">{error}</p>}
      <div className="panel">
        <div className="kv"><span className="muted">Open intraday / F&amp;O positions</span><b>{rows.length}</b></div>
        <div className="kv"><span className="muted">MTM unrealized</span>
          <span className={upl >= 0 ? 'up' : 'down'}>₹{upl.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span></div>
      </div>
      <div className="panel">
        <table>
          <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Avg</th><th>LTP</th><th>Unrealized</th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} onClick={() => navigate(`/symbol/${p.instrument.symbol}`)} style={{ cursor: 'pointer' }}>
                <td><b>{p.instrument.symbol}</b> <span className="muted">{p.product_type}</span></td>
                <td>{p.side}</td><td>{p.quantity}</td><td>{p.average_entry_price}</td><td>{p.last_price}</td>
                <td className={Number(p.unrealized_pnl) >= 0 ? 'up' : 'down'}>{p.unrealized_pnl}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <p className="muted">No open intraday positions. MIS auto-squares at 15:15 IST.</p>}
      </div>
    </div>
  );
}
