import { useEffect, useState } from 'react';
import { api } from '../api';

const EMPTY = { symbol: 'RELIANCE', side: 'BUY', order_type: 'MKT', quantity: 10, price: '', trigger_price: '', stop_loss_price: '' };
const today = () => new Date().toISOString().slice(0, 10);

export default function Orders() {
  const [orders, setOrders] = useState([]);
  const [trades, setTrades] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [csvDate, setCsvDate] = useState(today());

  async function load() {
    try {
      const [o, t] = await Promise.all([api('/api/v1/orders/'), api('/api/v1/trades/')]);
      setOrders(o);
      setTrades(t);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function place(e) {
    e.preventDefault();
    setError('');
    setStatus('');
    try {
      const body = { ...form, quantity: Number(form.quantity) };
      for (const k of ['price', 'trigger_price', 'stop_loss_price']) if (!body[k]) delete body[k];
      const order = await api('/api/v1/orders/', { method: 'POST', body });
      setStatus(`Order ${order.status}${order.rejection_reason ? ` — ${order.rejection_reason}` : ''}`);
      setForm(EMPTY);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function cancel(id) {
    try {
      await api(`/api/v1/orders/${id}/`, { method: 'DELETE' });
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function downloadCsv(e) {
    e.preventDefault();
    setError('');
    try {
      const token = localStorage.getItem('pts_access');
      const res = await fetch(`/api/v1/reports/daily.csv?date=${csvDate}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `trades-${csvDate}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div>
      <h2>Orders &amp; trades</h2>
      {error && <p className="error">{error}</p>}
      {status && <p>{status}</p>}
      <form className="inline" onSubmit={place}>
        <input value={form.symbol} onChange={set('symbol')} placeholder="Symbol" required />
        <select value={form.side} onChange={set('side')}><option>BUY</option><option>SELL</option></select>
        <select value={form.order_type} onChange={set('order_type')}>
          <option value="MKT">Market</option><option value="LMT">Limit</option>
          <option value="SL">Stop-loss</option><option value="SLM">Stop market</option>
        </select>
        <input type="number" min="1" value={form.quantity} onChange={set('quantity')} placeholder="Qty" required />
        <input value={form.price} onChange={set('price')} placeholder="Limit (LMT/SL)" />
        <input value={form.trigger_price} onChange={set('trigger_price')} placeholder="Trigger (SL/SLM)" />
        <input value={form.stop_loss_price} onChange={set('stop_loss_price')} placeholder="Stop ₹ (required)" />
        <button className="action" type="submit">Place order</button>
      </form>
      <div className="panel">
        <h3>Orders ({orders.length})</h3>
        <table>
          <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Filled</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id}>
                <td>{o.instrument.symbol}</td><td>{o.side}</td><td>{o.quantity}</td>
                <td>{o.filled_quantity} @ {o.average_fill_price ?? '—'}</td>
                <td>{o.status}{o.rejection_reason ? ` — ${o.rejection_reason}` : ''}</td>
                <td>{(o.status === 'OPN' || o.status === 'PFL') && (
                  <button className="action" onClick={() => cancel(o.id)}>Cancel</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h3>Daily report (CSV)</h3>
        <form className="inline" onSubmit={downloadCsv}>
          <input type="date" value={csvDate} onChange={(e) => setCsvDate(e.target.value)} required />
          <button className="action" type="submit">Download trades CSV</button>
        </form>
      </div>
      <div className="panel">
        <h3>Trades ({trades.length})</h3>
        <table>
          <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Charges</th></tr></thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id}>
                <td>{t.executed_at}</td><td>{t.instrument.symbol}</td><td>{t.side}</td>
                <td>{t.quantity}</td><td>{t.price}</td><td>{t.total_charges}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
