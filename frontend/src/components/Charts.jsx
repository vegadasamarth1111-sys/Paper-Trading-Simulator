import { useEffect, useRef } from 'react';
import { AreaSeries, CandlestickSeries, createChart } from 'lightweight-charts';

// Candlestick chart for symbol pages + terminal (free MIT library, v5 API).
export function CandleChart({ candles, height = 420 }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current || !candles?.length) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth || 720,
      height,
      layout: { background: { color: '#ffffff' }, textColor: '#6b7689' },
      grid: { vertLines: { color: '#eef1f5' }, horzLines: { color: '#eef1f5' } },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#00b386', downColor: '#eb5b3c',
      wickUpColor: '#00b386', wickDownColor: '#eb5b3c',
    });
    // Intraday bars must be UTCTimestamp seconds (backend sends ISO with
    // offset; Date parses it directly — never append 'Z' to an offset).
    const toTime = (iso) => (iso.length > 10 ? Math.floor(new Date(iso).getTime() / 1000) : iso.slice(0, 10));
    series.setData(
      candles.map((c) => ({
        time: toTime(c.open_ts),
        open: Number(c.open_price),
        high: Number(c.high_price),
        low: Number(c.low_price),
        close: Number(c.close_price),
      })),
    );
    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: ref.current.clientWidth });
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chart.remove();
    };
  }, [candles, height]);

  if (!candles?.length) return <p className="muted">No candles yet for this range.</p>;
  return <div ref={ref} />;
}

// Tiny inline sparkline for watchlist rows (area, no axes).
export function Sparkline({ data, width = 120, height = 36 }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current || !data?.length) return;
    const chart = createChart(ref.current, {
      width,
      height,
      layout: { background: { color: 'transparent' }, textColor: 'transparent' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
    });
    const up = data[data.length - 1].value >= data[0].value;
    const color = up ? '#00b386' : '#eb5b3c';
    const series = chart.addSeries(AreaSeries, {
      lineColor: color, topColor: color + '44', bottomColor: color + '05', lineWidth: 1,
    });
    series.setData(data);
    return () => chart.remove();
  }, [data, width, height]);

  if (!data?.length) return <span className="muted">—</span>;
  return <div ref={ref} />;
}

// Equity curve (area) for the dashboard.
export function EquityChart({ curve }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current || !curve?.length) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth || 640,
      height: 260,
      layout: { background: { color: '#ffffff' }, textColor: '#6b7689' },
      grid: { vertLines: { color: '#eef1f5' }, horzLines: { color: '#eef1f5' } },
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: '#00b386', topColor: '#00b38644', bottomColor: '#00b38608',
    });
    series.setData(curve.map((p) => ({ time: p.date, value: Number(p.equity) })));
    const resize = () => chart.applyOptions({ width: ref.current.clientWidth });
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chart.remove();
    };
  }, [curve]);

  if (!curve?.length) return <p className="muted">No snapshots yet — settle a day first.</p>;
  return <div ref={ref} />;
}
