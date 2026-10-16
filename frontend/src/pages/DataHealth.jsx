import { useEffect, useState } from 'react';
import { api, priceStream } from '../api';

const WATCH = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'NIFTY'];

export default function DataHealth() {
  const [rows, setRows] = useState([]);
  const [quality, setQuality] = useState(null);
  const [live, setLive] = useState({});
  const [error, setError] = useState('');

  useEffect(() => {
    let stop = false;
    (async () => {
      try {
        const health = await api(`/api/v1/data-health/?symbols=${WATCH.join(',')}`);
        if (!stop) {
          setRows(health.symbols);
          setQuality(health.quality);
        }
      } catch (err) {
        if (!stop) setError(err.message);
      }
    })();
    const ctrl = new AbortController();
    (async () => {
      try {
        for await (const frame of priceStream(WATCH, { signal: ctrl.signal })) {
          if (stop) break;
          setLive(frame.prices || {});
        }
      } catch (err) {
        if (!stop && err.name !== 'AbortError') setError(String(err.message || err));
      }
    })();
    return () => {
      stop = true;
      ctrl.abort();
    };
  }, []);

  return (
    <div>
      <h2>Data health</h2>
      {error && <p className="error">{error}</p>}
      {quality && (
        <div className="panel">
          <h3>
            Latest quality verdict: {quality.date}{' '}
            <span className={quality.is_suspect ? 'neg' : 'pos'}>
              {quality.is_suspect ? 'SUSPECT' : 'OK'}
            </span>
          </h3>
          <p style={{ color: '#8b98b3' }}>{quality.notes}</p>
        </div>
      )}
      <div className="panel">
        <h3>Feed (live stream + last snapshot)</h3>
        <table>
          <thead><tr><th>Symbol</th><th>Status</th><th>LTP</th><th>Age (s)</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol}>
                <td>{r.symbol}</td>
                <td className={r.status === 'live' ? 'pos' : 'warn'}>{r.status}</td>
                <td>{live[r.symbol]?.ltp ?? r.ltp ?? '—'}</td>
                <td>{r.age_seconds ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
