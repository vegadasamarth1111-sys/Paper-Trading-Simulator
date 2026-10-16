import { useEffect, useState } from 'react';
import { api } from '../api';
import { CandleChart } from '../components/Charts';

// Terminal: full-width chart + option chain side panel (NIFTY default).
export default function Terminal() {
  const [symbol, setSymbol] = useState('NIFTY');
  const [tf, setTf] = useState('15m');
  const [candles, setCandles] = useState([]);
  const [expiry, setExpiry] = useState('');
  const [chain, setChain] = useState([]);

  useEffect(() => {
    api(`/api/v1/market/candles/?symbol=${symbol}&timeframe=${tf}&limit=200`)
      .then((d) => setCandles(
        [...(d.candles || [])].sort((a, b) => new Date(a.open_ts) - new Date(b.open_ts)),
      ))
      .catch(() => setCandles([]));
  }, [symbol, tf]);

  useEffect(() => {
    api(`/api/v1/market/expiries/?underlying=${symbol}`)
      .then((d) => setExpiry(d.expiries?.[0] || ''))
      .catch(() => setExpiry(''));
  }, [symbol]);

  useEffect(() => {
    if (!expiry) {
      setChain([]);
      return;
    }
    api(`/api/v1/market/option-chain/?underlying=${symbol}&expiry=${expiry}`)
      .then((d) => setChain(d.strikes || []))
      .catch(() => setChain([]));
  }, [symbol, expiry]);

  return (
    <div>
      <h2>Terminal</h2>
      <form className="inline" onSubmit={(e) => e.preventDefault()}>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="Symbol" style={{ width: 160 }} />
        {['1m', '5m', '15m', '1h', '1d'].map((x) => (
          <button key={x} type="button" className={`pill ${tf === x ? 'active' : ''}`} onClick={() => setTf(x)}>{x}</button>
        ))}
      </form>
      <div className="grid2">
        <div className="panel"><CandleChart candles={candles} height={520} /></div>
        <div className="panel">
          <h3>Chain {expiry && <span className="muted">{expiry}</span>}</h3>
          <table className="chain">
            <thead><tr><th className="num">Call</th><th>Strike</th><th className="num">Put</th></tr></thead>
            <tbody>
              {chain.slice(0, 14).map((leg) => (
                <tr key={leg.strike}>
                  <td className="num">{leg.call?.ltp ?? '—'}</td>
                  <td><b>{leg.strike}</b></td>
                  <td className="num">{leg.put?.ltp ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!chain.length && <p className="muted">No chain for this symbol/expiry.</p>}
        </div>
      </div>
    </div>
  );
}
