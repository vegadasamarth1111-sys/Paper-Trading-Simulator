import { useEffect, useState } from 'react';
import { api } from '../api';

// Scrolling index tape (NIFTY, BANKNIFTY, INDIAVIX + master IDX rows).
export default function Tape() {
  const [indices, setIndices] = useState([]);

  useEffect(() => {
    api('/api/v1/market/indices/')
      .then((d) => setIndices(d.indices || []))
      .catch(() => {});
  }, []);

  if (!indices.length) return null;
  return (
    <div className="tape">
      {indices.map((r) => (
        <span key={r.symbol}>
          <b>{r.symbol}</b> {r.ltp ?? '—'}{' '}
          <span className={Number(r.change) >= 0 ? 'up' : 'down'}>
            {r.change != null ? `${r.change} (${r.change_pct}%)` : ''}
          </span>
        </span>
      ))}
    </div>
  );
}
