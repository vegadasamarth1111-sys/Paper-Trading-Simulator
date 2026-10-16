import { useEffect, useState } from 'react';
import { api } from '../api';

// F&O landing: indices plus the most active index options.
export default function FnO() {
  const [indices, setIndices] = useState([]);
  const [chain, setChain] = useState([]);
  const [expiry, setExpiry] = useState('');
  const [underlying, setUnderlying] = useState('NIFTY');
  const [error, setError] = useState('');

  useEffect(() => {
    api('/api/v1/market/indices/')
      .then((d) => setIndices(d.indices || []))
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    api(`/api/v1/market/expiries/?underlying=${underlying}`)
      .then((d) => setExpiry(d.expiries?.[0] || ''))
      .catch(() => setExpiry(''));
  }, [underlying]);

  useEffect(() => {
    if (!expiry) {
      setChain([]);
      return;
    }
    api(`/api/v1/market/option-chain/?underlying=${underlying}&expiry=${expiry}`)
      .then((d) => setChain(d.strikes || []))
      .catch(() => setChain([]));
  }, [underlying, expiry]);

  return (
    <div>
      <h2>F&amp;O</h2>
      {error && <p className="error">{error}</p>}
      <div className="cards4">
        {indices.map((r) => (
          <div className="stockcard" key={r.symbol}>
            <div className="nm">{r.name || r.symbol}</div>
            <div className="px">{r.ltp ?? '—'}</div>
            <div className={Number(r.change) >= 0 ? 'up' : 'down'}>
              {r.change} ({r.change_pct}%)
            </div>
          </div>
        ))}
      </div>
      <div className="panel">
        <h3>
          Top options —{' '}
          <select value={underlying} onChange={(e) => setUnderlying(e.target.value)}>
            <option>NIFTY</option>
            <option>BANKNIFTY</option>
          </select>{' '}
          <span className="muted">{expiry}</span>
        </h3>
        <table className="chain">
          <thead><tr><th>Contract</th><th className="num">LTP</th></tr></thead>
          <tbody>
            {chain.slice(0, 8).flatMap((leg) => [
              { k: `${leg.strike}-CE`, label: `${underlying} ${leg.strike} Call`, ltp: leg.call?.ltp },
              { k: `${leg.strike}-PE`, label: `${underlying} ${leg.strike} Put`, ltp: leg.put?.ltp },
            ]).map((c) => (
              <tr key={c.k}>
                <td>{c.label}</td>
                <td className="num">{c.ltp ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!chain.length && <p className="muted">No listed contracts for this expiry yet.</p>}
      </div>
    </div>
  );
}
