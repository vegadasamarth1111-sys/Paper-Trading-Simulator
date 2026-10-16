import { useState } from 'react';
import './styles.css';
import { loggedIn, logout } from './api';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Orders from './pages/Orders';
import Strategies from './pages/Strategies';
import Risk from './pages/Risk';
import DataHealth from './pages/DataHealth';

const TABS = [
  ['dashboard', 'Dashboard', Dashboard],
  ['orders', 'Orders', Orders],
  ['strategies', 'Strategies', Strategies],
  ['risk', 'Risk', Risk],
  ['data', 'Data health', DataHealth],
];

export default function App() {
  const [authed, setAuthed] = useState(loggedIn());
  const [tab, setTab] = useState('dashboard');

  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  const Active = (TABS.find(([id]) => id === tab) || TABS[0])[2];

  function signOut() {
    logout();
    setAuthed(false);
  }

  return (
    <div className="app">
      <nav>
        <h1>Paper Trading</h1>
        {TABS.map(([id, label]) => (
          <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
        <button className="logout" onClick={signOut}>Log out</button>
      </nav>
      <main>
        <Active />
      </main>
    </div>
  );
}
