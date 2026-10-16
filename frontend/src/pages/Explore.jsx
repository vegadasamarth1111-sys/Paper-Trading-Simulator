import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { EquityChart } from '../components/Charts';

// Explore: index cards, top movers from the seed universe, investments summary.
export default function Explore() {
  const [indices, setIndices] = useState([]);
  const [movers, setMovers] = useState([]);
  const [pnl, setPnl] = useState(null);
  const [account, setAccount] = useState(null);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    (async () => {
      try {
        const [idx, over, p, a] = await Promise.all([
          api('/api/v1/market/indices/'),
          api('/api/v1/market/overview/'),
          api('/api/v1/pnl/'),
          api('/api/v1/account/'),
        ]);
        setIndices(idx.indices || []);
        const rows = (over.symbols || []).filter((r) => r.ltp != null);
        rows.sort((x, y) => Math.abs(Number(y.change_pct || 0)) - Math.abs(Number(x.change_pct || 0)));
        setMovers(rows.slice(0, 4));
        setPnl(p);
        setAccount(a);
      } catch (err) {
        setError(err.message);
      }
    })();
  }, []);

  return (
    <div>
      {error && <p className="error">{error}</p>}
      <div className="grid2">
        <div>
          <h2>Explore</h2>
          <div className="cards4">
            {movers.map((m) => (
              <div className="stockcard" key={m.symbol} onClick={() => navigate(`/symbol/${m.symbol}`)}>
                <div className="nm">{m.name || m.symbol}</div>
                <div className="px">₹{m.ltp}</div>
                {m.change != null ? (
                  <div className={Number(m.change) >= 0 ? 'up' : 'down'}>
                    {m.change} ({m.change_pct}%)
                  </div>
                ) : (
                  <div className="muted">—</div>
                )}
              </div>
            ))}
          </div>
          <div className="panel">
            <h3>Top movers today</h3>
            <table>
              <thead><tr><th>Company</th><th>Market price</th><th>1D change</th></tr></thead>
              <tbody>
                {movers.map((m) => (
                  <tr key={m.symbol} onClick={() => navigate(`/symbol/${m.symbol}`)} style={{ cursor: 'pointer' }}>
                    <td><b>{m.symbol}</b><br /><span className="muted">{m.name}</span></td>
                    <td>₹{m.ltp}</td>
                    <td className={Number(m.change) >= 0 ? 'up' : 'down'}>
                      {m.change != null ? `${m.change} (${m.change_pct}%)` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h2>Your investments</h2>
          <div className="panel">
            <div className="muted">Current</div>
            <div className="invest">₹{pnl ? Number(pnl.total_equity).toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—'}</div>
            <div className="kv"><span className="muted">Day returns</span>
              <span className={Number(pnl?.day_pnl ?? 0) >= 0 ? 'up' : 'down'}>{pnl?.day_pnl}</span></div>
            <div className="kv"><span className="muted">Total returns</span>
              <span className={Number(pnl?.realized_pnl ?? 0) >= 0 ? 'up' : 'down'}>{pnl?.realized_pnl}</span></div>
            <div className="kv"><span className="muted">Invested</span>
              <span>{account ? `₹${Number(account.initial_capital).toLocaleString('en-IN', { maximumFractionDigits: 0 })}` : '—'}</span></div>
          </div>
          <div className="panel">
            <h3>Indices</h3>
            {indices.map((r) => (
              <div className="kv" key={r.symbol}>
                <span><b>{r.symbol}</b> {r.ltp}</span>
                <span className={Number(r.change) >= 0 ? 'up' : 'down'}>{r.change_pct}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
