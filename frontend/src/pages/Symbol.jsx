import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api';
import { CandleChart } from '../components/Charts';

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d'];

// Symbol page: candlestick chart + scalping ticket + option chain.
export default function Symbol({ initialSide = 'BUY' }) {
  const { symbol } = useParams();
  const [tf, setTf] = useState('15m');
  const [candles, setCandles] = useState([]);
  const [quote, setQuote] = useState(null);
  const [expiries, setExpiries] = useState([]);
  const [expiry, setExpiry] = useState('');
  const [chain, setChain] = useState([]);
  const [ticket, setTicket] = useState({ side: initialSide, qty: 1, order_type: 'MKT', price: '', stop: '' });
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');

  async function loadCandles(timeframe) {
    try {
      const data = await api(`/api/v1/market/candles/?symbol=${symbol}&timeframe=${timeframe}&limit=200`);
      // Endpoint returns closed-first; charts need strict time-ascending.
      const ordered = [...(data.candles || [])].sort((a, b) => new Date(a.open_ts) - new Date(b.open_ts));
      setCandles(ordered);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    api(`/api/v1/market/latest/?symbol=${symbol}`)
      .then(setQuote)
      .catch(() => setQuote(null));
    loadCandles(tf);
    api(`/api/v1/market/expiries/?underlying=${symbol}`)
      .then((d) => {
        setExpiries(d.expiries || []);
        if (d.expiries?.length) setExpiry(d.expiries[0]);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, tf]);

  useEffect(() => {
    if (!expiry) return;
    api(`/api/v1/market/option-chain/?underlying=${symbol}&expiry=${expiry}`)
      .then((d) => setChain(d.strikes || []))
      .catch(() => setChain([]));
  }, [symbol, expiry]);

  async function scalp(e) {
    e.preventDefault();
    setMsg('');
    setError('');
    try {
      const body = {
        symbol,
        side: ticket.side,
        order_type: ticket.order_type,
        quantity: Number(ticket.qty),
        stop_loss_price: ticket.stop || undefined,
      };
      if (ticket.order_type !== 'MKT' && ticket.price) body.price = ticket.price;
      const order = await api('/api/v1/orders/', { method: 'POST', body });
      setMsg(`Order ${order.status}${order.rejection_reason ? ` — ${order.rejection_reason}` : ''}`);
    } catch (err) {
      setError(err.message);
    }
  }

  const t = (k) => (e) => setTicket({ ...ticket, [k]: e.target.value });

  return (
    <div>
      <h2>{symbol} {quote && <span>₹{quote.ltp}</span>}</h2>
      {error && <p className="error">{error}</p>}
      {msg && <p className="ok">{msg}</p>}
      <div className="grid2">
        <div>
          <div className="tfbar">
            {TIMEFRAMES.map((x) => (
              <button key={x} className={tf === x ? 'active' : ''} onClick={() => setTf(x)}>{x}</button>
            ))}
          </div>
          <div className="panel"><CandleChart candles={candles} height={420} /></div>
          {chain.length > 0 && (
            <div className="panel">
              <h3>Option chain — {expiry}</h3>
              <select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
                {expiries.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
              <table className="chain" style={{ marginTop: 10 }}>
                <thead><tr><th className="num">Call LTP</th><th>Strike</th><th className="num">Put LTP</th></tr></thead>
                <tbody>
                  {chain.slice(0, 12).map((leg) => (
                    <tr key={leg.strike}>
                      <td className="num">{leg.call?.ltp ?? '—'}</td>
                      <td><b>{leg.strike}</b></td>
                      <td className="num">{leg.put?.ltp ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div>
          <div className="ticket">
            <h3 style={{ marginTop: 0 }}>Scalp ticket</h3>
            <div className="side">
              <button className={ticket.side === 'BUY' ? 'on-buy' : ''} onClick={() => setTicket({ ...ticket, side: 'BUY' })}>BUY</button>
              <button className={ticket.side === 'SELL' ? 'on-sell' : ''} onClick={() => setTicket({ ...ticket, side: 'SELL' })}>SELL</button>
            </div>
            <form onSubmit={scalp}>
              <div className="row">
                <input type="number" min="1" value={ticket.qty} onChange={t('qty')} placeholder="Qty" required />
                <select value={ticket.order_type} onChange={t('order_type')}>
                  <option value="MKT">Market</option>
                  <option value="LMT">Limit</option>
                </select>
              </div>
              <div className="row">
                <input value={ticket.price} onChange={t('price')} placeholder="Limit price" />
                <input value={ticket.stop} onChange={t('stop')} placeholder="Stop ₹ (required)" required />
              </div>
              <button className={`action ${ticket.side === 'SELL' ? 'sell' : ''}`} type="submit" style={{ width: '100%' }}>
                {ticket.side} {symbol}
              </button>
            </form>
            <p className="muted">Market fills at bid/ask + slippage. Every entry needs a stop — the risk engine rejects stop-less orders.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
