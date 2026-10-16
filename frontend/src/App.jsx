import { useState } from 'react';
import { HashRouter, Link, NavLink, Route, Routes, useParams, useSearchParams } from 'react-router-dom';
import './styles.css';
import { loggedIn, logout } from './api';
import TopBar from './components/TopBar';
import Tape from './components/Tape';
import Login from './pages/Login';
import Explore from './pages/Explore';
import Holdings from './pages/Holdings';
import Positions from './pages/Positions';
import Orders from './pages/Orders';
import Strategies from './pages/Strategies';
import Risk from './pages/Risk';
import DataHealth from './pages/DataHealth';
import Watchlist from './pages/Watchlist';
import Symbol from './pages/Symbol';
import Terminal from './pages/Terminal';
import Account from './pages/Account';
import FnO from './pages/FnO';

function SymbolRoute() {
  const { symbol } = useParams();
  const [params] = useSearchParams();
  return <Symbol key={symbol} initialSide={params.get('side') || 'BUY'} />;
}

function Shell() {
  const [tab, setTab] = useState('explore');
  const Link2 = ({ id, label, to }) => (
    <NavLink to={to} end={to === '/'} onClick={() => setTab(id)}
      className={({ isActive }) => (isActive || tab === id ? 'active' : '')}>
      {label}
    </NavLink>
  );
  return (
    <div>
      <TopBar />
      <div className="tabs">
        <Link2 id="explore" label="Explore" to="/" />
        <Link2 id="holdings" label="Holdings" to="/holdings" />
        <Link2 id="positions" label="Positions" to="/positions" />
        <Link2 id="orders" label="Orders" to="/orders" />
        <Link2 id="watchlist" label="Watchlist" to="/watchlist" />
        <span className="spacer" />
        <Link to="/terminal"><button className="btn-terminal">Terminal</button></Link>
      </div>
      <Tape />
      <div className="page">
        <Routes>
          <Route path="/" element={<Explore />} />
          <Route path="/holdings" element={<Holdings />} />
          <Route path="/positions" element={<Positions />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/strategies" element={<Strategies />} />
          <Route path="/risk" element={<Risk />} />
          <Route path="/data" element={<DataHealth />} />
          <Route path="/fno" element={<FnO />} />
          <Route path="/terminal" element={<Terminal />} />
          <Route path="/account" element={<Account />} />
          <Route path="/symbol/:symbol" element={<SymbolRoute />} />
          <Route path="*" element={<Explore />} />
        </Routes>
      </div>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(loggedIn());

  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  return (
    <HashRouter>
      <Shell />
    </HashRouter>
  );
}
