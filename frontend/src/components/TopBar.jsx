import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, logout, searchInstruments } from '../api';

// Top bar: brand, segments, search (Ctrl+K), alerts bell, avatar menu.
export default function TopBar() {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState([]);
  const [menu, setMenu] = useState(false);
  const [user, setUser] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    api('/api/v1/auth/me/').then((me) => setUser(me.username || '')).catch(() => {});
  }, []);

  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    const id = setTimeout(() => {
      searchInstruments(q.trim()).then(setHits).catch(() => setHits([]));
    }, 250);
    return () => clearTimeout(id);
  }, [q]);

  useEffect(() => {
    function hotkey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        document.getElementById('global-search')?.focus();
      }
    }
    window.addEventListener('keydown', hotkey);
    return () => window.removeEventListener('keydown', hotkey);
  }, []);

  return (
    <div className="topbar">
      <Link to="/" className="logo" style={{ textDecoration: 'none' }}>
        <span className="mark" />
        PaperTrade
      </Link>
      <div className="segments">
        <button className="active">Stocks</button>
        <button onClick={() => navigate('/fno')}>F&amp;O</button>
      </div>
      <div className="search">
        <input
          id="global-search"
          placeholder="Search stocks, F&O…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onBlur={() => setTimeout(() => setHits([]), 150)}
        />
        <span className="kbd">Ctrl+K</span>
        {hits.length > 0 && (
          <div className="search-results">
            {hits.slice(0, 8).map((h) => (
              <button key={h.id} onClick={() => { setQ(''); navigate(`/symbol/${h.symbol}`); }}>
                <b>{h.symbol}</b> <span className="muted">{h.name}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <button className="iconbtn" title="Alerts come via Telegram when configured">🔔</button>
      <button className="avatar" onClick={() => setMenu(!menu)} title={user || 'Account'}>
        {(user || 'D').slice(0, 1).toUpperCase()}
      </button>
      {menu && (
        <div className="menu">
          <button onClick={() => { setMenu(false); navigate('/account'); }}>My account</button>
          <button onClick={() => { setMenu(false); navigate('/risk'); }}>Risk limits</button>
          <button onClick={() => { logout(); window.location.reload(); }}>Log out</button>
        </div>
      )}
    </div>
  );
}
