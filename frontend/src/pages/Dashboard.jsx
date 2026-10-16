import { useEffect, useState } from 'react';
import { api } from '../api';
import EquityChart from '../components/EquityChart';

function Cards({ account, pnl }) {
  const items = [
    ['Equity', pnl ? `₹${Number(pnl.total_equity).toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : '—'],
    ['Day PnL', pnl?.day_pnl ?? '—', Number(pnl?.day_pnl ?? 0) >= 0 ? 'pos' : 'neg'],
    ['Realized', pnl?.realized_pnl ?? '—', Number(pnl?.realized_pnl ?? 0) >= 0 ? 'pos' : 'neg'],
    ['Unrealized', pnl?.unrealized_pnl ?? '—', Number(pnl?.unrealized_pnl ?? 0) >= 0 ? 'pos' : 'neg'],
    ['Margin used', account ? `₹${Number(account.margin_used).toLocaleString('en-IN')}` : '—'],
    ['Drawdown', pnl ? `${pnl.drawdown_pct}%` : '—', Number(pnl?.drawdown_pct ?? 0) > 5 ? 'warn' : ''],
  ];
  return (
    <div className="cards">
      {items.map(([label, value, cls]) => (
        <div className="card" key={label}>
          <div className="label">{label}</div>
          <div className={`value ${cls || ''}`}>{value}</div>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [account, setAccount] = useState(null);
  const [pnl, setPnl] = useState(null);
  const [positions, setPositions] = useState([]);
  const [error, setError] = useState('');

  async function load() {
    try {
      const [a, p, pos] = await Promise.all([
        api('/api/v1/account/'),
        api('/api/v1/pnl/'),
        api('/api/v1/positions/'),
      ]);
      setAccount(a);
      setPnl(p);
      setPositions(pos);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <h2>Dashboard</h2>
      {error && <p className="error">{error}</p>}
      <Cards account={account} pnl={pnl} />
      <div className="panel">
        <h3>Equity curve</h3>
        <EquityChart curve={pnl?.equity_curve} />
      </div>
      <div className="panel">
        <h3>Open positions ({positions.length})</h3>
        <table>
          <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Avg</th><th>LTP</th><th>Unrealized</th></tr></thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.id}>
                <td>{p.instrument.symbol}</td><td>{p.side}</td><td>{p.quantity}</td>
                <td>{p.average_entry_price}</td><td>{p.last_price}</td>
                <td className={Number(p.unrealized_pnl) >= 0 ? 'pos' : 'neg'}>{p.unrealized_pnl}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
