import { useEffect, useState } from 'react';
import { api } from '../api';

// My account: balance, virtual funding (add/withdraw paper money).
export default function Account() {
  const [account, setAccount] = useState(null);
  const [amount, setAmount] = useState('100000');
  const [error, setError] = useState('');
  const [ok, setOk] = useState('');

  async function load() {
    try {
      setAccount(await api('/api/v1/account/'));
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function move(action) {
    setError('');
    setOk('');
    try {
      const updated = await api(`/api/v1/account/${action}/`, {
        method: 'POST',
        body: { amount },
      });
      setAccount(updated);
      setOk(`${action === 'deposit' ? 'Added' : 'Withdrew'} ₹${Number(amount).toLocaleString('en-IN')}`);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div>
      <h2>My account</h2>
      {error && <p className="error">{error}</p>}
      {ok && <p className="ok">{ok}</p>}
      <div className="panel">
        <div className="muted">Balance</div>
        <div className="invest">
          {account ? `₹${Number(account.current_balance).toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : '—'}
        </div>
        <div className="kv"><span className="muted">Free margin</span><span>{account?.available_margin}</span></div>
        <div className="kv"><span className="muted">Withdrawable (free margin)</span><span>{account?.available_margin}</span></div>
        <div className="kv"><span className="muted">Margin used</span><span>{account?.margin_used}</span></div>
      </div>
      <div className="panel">
        <h3>Add / withdraw paper money</h3>
        <p className="muted">Virtual funds only — always free, instantly settled, fully ledgered.</p>
        <form className="inline" onSubmit={(e) => e.preventDefault()}>
          <input type="number" min="1" value={amount} onChange={(e) => { setAmount(e.target.value); setError(''); }} placeholder="Amount ₹" required />
          <button className="action" type="button" onClick={() => move('deposit')}>Add money</button>
          <button className="action ghost" type="button" onClick={() => move('withdraw')}>Withdraw</button>
        </form>
      </div>
    </div>
  );
}
