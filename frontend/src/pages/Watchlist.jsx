import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { Sparkline } from '../components/Charts';

// Watchlist: user lists with sparklines, one-tap buy/sell into the ticket.
export default function Watchlist() {
  const [lists, setLists] = useState([]);
  const [active, setActive] = useState(null);
  const [quotes, setQuotes] = useState({});
  const [sparks, setSparks] = useState({});
  const [name, setName] = useState('');
  const [add, setAdd] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();

  async function loadLists() {
    try {
      const rows = await api('/api/v1/watchlists/');
      setLists(rows);
      if (rows.length && !active) setActive(rows[0]);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { loadLists(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!active?.symbols?.length) return;
    api(`/api/v1/market/overview/?symbols=${active.symbols.join(',')}`)
      .then(async (over) => {
        const q = {};
        over.symbols.forEach((r) => { q[r.symbol] = r; });
        setQuotes(q);
        const sp = {};
        await Promise.all(
          active.symbols.map(async (s) => {
            try {
              const d = await api(`/api/v1/market/candles/?symbol=${s}&timeframe=1d&limit=30`);
              sp[s] = [...(d.candles || [])]
                .sort((a, b) => new Date(a.open_ts) - new Date(b.open_ts))
                .map((c) => ({ time: c.open_ts.slice(0, 10), value: Number(c.close_price) }));
            } catch {
              sp[s] = [];
            }
          }),
        );
        setSparks(sp);
      })
      .catch((err) => setError(err.message));
  }, [active]);

  async function createList(e) {
    e.preventDefault();
    try {
      await api('/api/v1/watchlists/', { method: 'POST', body: { name: name || 'My Watchlist', symbols: ['RELIANCE'] } });
      setName('');
      await loadLists();
    } catch (err) {
      setError(err.message);
    }
  }

  async function addSymbol(e) {
    e.preventDefault();
    if (!active || !add.trim()) return;
    try {
      const updated = await api(`/api/v1/watchlists/${active.id}/`, {
        method: 'PATCH',
        body: { symbols: [...active.symbols, add.trim().toUpperCase()] },
      });
      setActive(updated);
      setAdd('');
      await loadLists();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div>
      <h2>Watchlist</h2>
      {error && <p className="error">{error}</p>}
      <form className="inline" onSubmit={createList}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New watchlist name" />
        <button className="action ghost" type="submit">+ Watchlist</button>
      </form>
      <div className="pills">
        {lists.map((w) => (
          <button key={w.id} className={`pill ${active?.id === w.id ? 'active' : ''}`} onClick={() => setActive(w)}>
            {w.name}
          </button>
        ))}
      </div>
      {active && (
        <div className="panel">
          <form className="inline" onSubmit={addSymbol}>
            <input value={add} onChange={(e) => setAdd(e.target.value)} placeholder="Add symbol (e.g. TCS)" />
            <button className="action ghost" type="submit">+ Add</button>
          </form>
          <table>
            <thead><tr><th>Company</th><th>Trend</th><th>Mkt price</th><th>1D change</th><th></th></tr></thead>
            <tbody>
              {active.symbols.map((s) => {
                const q = quotes[s] || {};
                return (
                  <tr key={s} onClick={() => navigate(`/symbol/${s}`)} style={{ cursor: 'pointer' }}>
                    <td><b>{s}</b></td>
                    <td><Sparkline data={sparks[s]} /></td>
                    <td>{q.ltp ? `₹${q.ltp}` : '—'}</td>
                    <td className={Number(q.change) >= 0 ? 'up' : 'down'}>
                      {q.change != null ? `${q.change} (${q.change_pct}%)` : '—'}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button className="chip-buy" onClick={() => navigate(`/symbol/${s}?side=BUY`)}>B</button>{' '}
                      <button className="chip-sell" onClick={() => navigate(`/symbol/${s}?side=SELL`)}>S</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!lists.length && <p className="muted">Create a watchlist to start tracking symbols.</p>}
    </div>
  );
}
