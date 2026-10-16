import { useEffect, useRef } from 'react';
import { createChart } from 'lightweight-charts';

// Equity curve from daily snapshots (free MIT-licensed chart library).
export default function EquityChart({ curve }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current || !curve?.length) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth || 640,
      height: 260,
      layout: { background: { color: '#1a2233' }, textColor: '#8b98b3' },
      grid: { vertLines: { color: '#2a3550' }, horzLines: { color: '#2a3550' } },
    });
    const series = chart.addAreaSeries({ lineColor: '#35c48d', topColor: '#35c48d55', bottomColor: '#35c48d05' });
    series.setData(curve.map((p) => ({ time: p.date, value: Number(p.equity) })));
    const resize = () => chart.applyOptions({ width: ref.current.clientWidth });
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chart.remove();
    };
  }, [curve]);

  if (!curve?.length) return <p style={{ color: '#8b98b3' }}>No snapshots yet — settle a day first.</p>;
  return <div ref={ref} />;
}
