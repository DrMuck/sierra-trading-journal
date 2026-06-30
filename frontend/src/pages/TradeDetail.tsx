import { useEffect, useState, useRef, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getTrade, getTradeChart, getDailyChart, getSetupLibrary } from '../lib/api';
import type { SetupRow } from '../lib/api';
import TradeCardForm from '../components/TradeCardForm';
import { formatPnl, formatPoints, formatDuration, formatTime, formatCurrency, pnlColor, cn } from '../lib/utils';
import { ArrowLeft } from 'lucide-react';
import { createChart, ColorType, CrosshairMode, CandlestickSeries, HistogramSeries, createSeriesMarkers } from 'lightweight-charts';
import type { IChartApi } from 'lightweight-charts';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts';
import {
  getExchangeTimezone,
  getExchangeTzLabel,
  formatClockInTz,
  makeChartLocalization,
  makeTickMarkFormatter,
} from '../lib/timezones';

const fmtMoney = (v: number) => `${v >= 0 ? '+' : ''}$${v.toFixed(0)}`;

export default function TradeDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [trade, setTrade] = useState<any>(null);
  const [fills, setFills] = useState<any[]>([]);
  const [chartData, setChartData] = useState<any>(null);
  // notes + rating are no longer mirrored to local state — TradeCardForm
  // owns them entirely and reads `trade.notes` / `trade.rating` from props.
  const [wideChartData, setWideChartData] = useState<any>(null);
  const [lookahead, setLookahead] = useState<number>(60);
  const [wideInterval, setWideInterval] = useState<number>(300);  // 60 = 1m, 300 = 5m
  const [dailyInterval, setDailyInterval] = useState<number>(300);
  // Trade card state lives inside the TradeCardForm component itself —
  // keeps keystrokes from re-rendering the whole TradeDetail tree (which
  // contains expensive Recharts canvases).
  const [setupLibrary, setSetupLibrary] = useState<SetupRow[]>([]);
  // Daily chart + business zones
  const [dailyData, setDailyData] = useState<any>(null);
  const [showZones, setShowZones] = useState(true);
  const [showPriorZones, setShowPriorZones] = useState(true);
  const [showSingles, setShowSingles] = useState(true);
  const chartRef = useRef<HTMLDivElement>(null);
  const wideChartRef = useRef<HTMLDivElement>(null);
  const dailyChartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<IChartApi | null>(null);
  const wideChartInstance = useRef<IChartApi | null>(null);
  const dailyChartInstance = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!id) return;
    getTrade(id).then(d => {
      // setup_name / trade_idea / what_good / what_bad / notes / rating all
      // live on `trade` and are passed straight into TradeCardForm as its
      // `initial` prop. No local state mirror needed any more.
      setTrade(d.trade);
      setFills(d.fills || []);
    });
  }, [id]);

  // Refetch wide context chart when interval changes
  useEffect(() => {
    if (!id) return;
    getTradeChart(id, wideInterval, 0).then(setWideChartData).catch(() => {});
  }, [id, wideInterval]);

  // Refetch day chart when interval changes
  useEffect(() => {
    if (!id) return;
    getDailyChart(id, dailyInterval, 5).then(setDailyData).catch(() => {});
  }, [id, dailyInterval]);

  // Load the user's setup-name library once per trade for the autocomplete
  // dropdown. Filtered to the trade's account so each prop firm has its own
  // setup vocabulary.
  useEffect(() => {
    if (!trade?.account) return;
    getSetupLibrary(trade.account)
      .then(r => setSetupLibrary(r.setups || []))
      .catch(() => {});
  }, [trade?.account]);

  // Reload P&L chart when lookahead changes
  useEffect(() => {
    if (!id) return;
    getTradeChart(id, 15, lookahead).then(setChartData).catch(() => {});
  }, [id, lookahead]);

  // Render price chart
  useEffect(() => {
    if (!chartRef.current || !chartData?.bars?.length) return;

    if (chartInstance.current) {
      chartInstance.current.remove();
      chartInstance.current = null;
    }

    const chart = createChart(chartRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#161922' },
        textColor: '#6b6f8a',
        fontFamily: 'Inter, sans-serif',
        panes: { separatorColor: '#2a2d3e', separatorHoverColor: '#363952' },
      },
      grid: {
        vertLines: { color: '#1c1f2e' },
        horzLines: { color: '#1c1f2e' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: makeTickMarkFormatter(trade?.root_symbol),
      },
      localization: makeChartLocalization(trade?.root_symbol),
      width: chartRef.current.clientWidth,
      height: 480,
    });

    chartInstance.current = chart;

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e80',
      wickDownColor: '#ef444480',
    }, 0);

    // Volume pane (paneIndex=1)
    const volSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      color: '#6b6f8a',
    }, 1);

    // Delta pane (paneIndex=2)
    const deltaSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      color: '#6366f1',
    }, 2);

    // Filter bars to trade window (+/- 5 min padding)
    const entryTime = trade?.entry_time_ms ? trade.entry_time_ms / 1000 : 0;
    const exitTime = trade?.exit_time_ms ? trade.exit_time_ms / 1000 : entryTime;
    const padding = 300; // 5 minutes
    const windowStart = entryTime - padding;
    const windowEnd = exitTime + padding;

    const windowBars = chartData.bars.filter(
      (b: any) => b.time >= windowStart && b.time <= windowEnd
    );
    const barsToShow = windowBars.length > 5 ? windowBars : chartData.bars;
    series.setData(barsToShow);

    // Volume bars (colored by candle direction)
    volSeries.setData(barsToShow.map((b: any) => ({
      time: b.time,
      value: b.volume || 0,
      color: b.close >= b.open ? '#22c55e80' : '#ef444480',
    })));

    // Delta bars (green positive, red negative)
    deltaSeries.setData(barsToShow.map((b: any) => ({
      time: b.time,
      value: b.delta || 0,
      color: (b.delta || 0) >= 0 ? '#22c55e' : '#ef4444',
    })));

    // Set pane sizes (price chart bigger, volume/delta smaller)
    try {
      chart.panes()[0]?.setHeight(280);
      chart.panes()[1]?.setHeight(100);
      chart.panes()[2]?.setHeight(100);
    } catch (e) { /* older versions may not support setHeight */ }

    // Add trade markers
    if (chartData.markers?.length && barsToShow.length > 0) {
      const markers = chartData.markers
        .map((m: any) => {
          // Find nearest bar time
          const nearest = barsToShow.reduce((best: any, b: any) =>
            Math.abs(b.time - m.time) < Math.abs(best.time - m.time) ? b : best
          , barsToShow[0]);
          return {
            time: nearest?.time || m.time,
            position: m.side === 'BUY' ? 'belowBar' as const : 'aboveBar' as const,
            color: m.side === 'BUY' ? '#22c55e' : '#ef4444',
            shape: m.side === 'BUY' ? 'arrowUp' as const : 'arrowDown' as const,
            text: `${m.side} ${m.quantity} @ ${m.price.toFixed(2)}`,
          };
        })
        .filter((m: any) => m.time != null);
      markers.sort((a: any, b: any) => a.time - b.time);
      if (markers.length > 0) createSeriesMarkers(series, markers);
    }

    // Entry/exit price lines
    if (trade) {
      if (trade.entry_price) {
        series.createPriceLine({
          price: trade.entry_price,
          color: '#3b82f6',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'Entry',
        });
      }
      if (trade.exit_price) {
        series.createPriceLine({
          price: trade.exit_price,
          color: trade.pnl_dollars >= 0 ? '#22c55e' : '#ef4444',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'Exit',
        });
      }
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartInstance.current = null;
    };
  }, [chartData, trade]);

  // Render wide 5min chart
  useEffect(() => {
    if (!wideChartRef.current || !wideChartData?.bars?.length || !trade) return;

    if (wideChartInstance.current) {
      wideChartInstance.current.remove();
      wideChartInstance.current = null;
    }

    const chart = createChart(wideChartRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#161922' },
        textColor: '#6b6f8a',
        fontFamily: 'Inter, sans-serif',
        panes: { separatorColor: '#2a2d3e', separatorHoverColor: '#363952' },
      },
      grid: {
        vertLines: { color: '#1c1f2e' },
        horzLines: { color: '#1c1f2e' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: makeTickMarkFormatter(trade?.root_symbol),
      },
      localization: makeChartLocalization(trade?.root_symbol),
      width: wideChartRef.current.clientWidth,
      height: 420,
    });

    wideChartInstance.current = chart;

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e80',
      wickDownColor: '#ef444480',
    }, 0);

    const volSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      color: '#6b6f8a',
    }, 1);

    const deltaSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      color: '#6366f1',
    }, 2);

    // Filter to +/- 3 hours around trade
    const entryTime = trade.entry_time_ms / 1000;
    const exitTime = trade.exit_time_ms ? trade.exit_time_ms / 1000 : entryTime;
    const padding = 3 * 3600; // 3 hours
    const windowBars = wideChartData.bars.filter(
      (b: any) => b.time >= entryTime - padding && b.time <= exitTime + padding
    );

    if (windowBars.length > 0) {
      series.setData(windowBars);

      volSeries.setData(windowBars.map((b: any) => ({
        time: b.time,
        value: b.volume || 0,
        color: b.close >= b.open ? '#22c55e80' : '#ef444480',
      })));
      deltaSeries.setData(windowBars.map((b: any) => ({
        time: b.time,
        value: b.delta || 0,
        color: (b.delta || 0) >= 0 ? '#22c55e' : '#ef4444',
      })));

      try {
        chart.panes()[0]?.setHeight(240);
        chart.panes()[1]?.setHeight(90);
        chart.panes()[2]?.setHeight(90);
      } catch (e) {}

      // Mark trade entry/exit
      if (wideChartData.markers?.length) {
        const markers = wideChartData.markers
          .map((m: any) => {
            const nearest = windowBars.reduce((best: any, b: any) =>
              Math.abs(b.time - m.time) < Math.abs(best.time - m.time) ? b : best
            , windowBars[0]);
            return {
              time: nearest?.time || m.time,
              position: m.side === 'BUY' ? 'belowBar' as const : 'aboveBar' as const,
              color: m.side === 'BUY' ? '#22c55e' : '#ef4444',
              shape: m.side === 'BUY' ? 'arrowUp' as const : 'arrowDown' as const,
              text: `${m.side} @ ${m.price.toFixed(2)}`,
            };
          })
          .filter((m: any) => m.time != null);
        markers.sort((a: any, b: any) => a.time - b.time);
        if (markers.length > 0) createSeriesMarkers(series, markers);
      }

      if (trade.entry_price) {
        series.createPriceLine({
          price: trade.entry_price, color: '#3b82f6', lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: 'Entry',
        });
      }
      if (trade.exit_price) {
        series.createPriceLine({
          price: trade.exit_price,
          color: trade.pnl_dollars >= 0 ? '#22c55e' : '#ef4444',
          lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'Exit',
        });
      }

      chart.timeScale().fitContent();
    }

    const handleResize = () => {
      if (wideChartRef.current) chart.applyOptions({ width: wideChartRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      wideChartInstance.current = null;
    };
  }, [wideChartData, trade]);


  // ── Daily 5-min chart with business zones ─────────────────────
  useEffect(() => {
    console.log('[TradeDetail] daily-chart effect: dailyData=%o trade=%o', dailyData, trade);
    if (!dailyChartRef.current || !dailyData?.bars?.length || !trade) return;
    try {
    if (dailyChartInstance.current) {
      dailyChartInstance.current.remove();
      dailyChartInstance.current = null;
    }
    const chart = createChart(dailyChartRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#161922' },
        textColor: '#6b6f8a',
        fontFamily: 'Inter, sans-serif',
      },
      grid: {
        vertLines: { color: '#1c1f2e' },
        horzLines: { color: '#1c1f2e' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: makeTickMarkFormatter(trade?.root_symbol),
      },
      localization: makeChartLocalization(trade?.root_symbol),
      width: dailyChartRef.current.clientWidth,
      height: 500,
    });
    dailyChartInstance.current = chart;
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e80',
      wickDownColor: '#ef444480',
    });
    candles.setData(dailyData.bars);

    // Trade markers (entries/exits for ALL trades that day on this symbol)
    // lightweight-charts v5 requires markers in strictly ascending time order
    // AND requires marker times to align to bar times (snap to bar interval).
    // Overlapping trades + intraday entries between bar starts would break it.
    const barStep = dailyInterval;
    const rawMarkers = (dailyData.markers || []).flatMap((m: any) => {
      const isCur = m.is_current;
      const winColor = (m.net_pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444';
      const entryColor = isCur ? '#facc15' : winColor;
      const snap = (t: number) => Math.floor(t / barStep) * barStep;
      return [
        {
          time: snap(m.entry_time),
          position: m.side === 'LONG' ? 'belowBar' : 'aboveBar',
          shape: m.side === 'LONG' ? 'arrowUp' : 'arrowDown',
          color: entryColor,
          text: isCur ? '★ entry' : '',
          size: isCur ? 2 : 1,
        },
        {
          time: snap(m.exit_time),
          position: m.side === 'LONG' ? 'aboveBar' : 'belowBar',
          shape: 'square',
          color: winColor,
          text: isCur ? `★ exit ${m.net_pnl != null ? `$${m.net_pnl.toFixed(0)}` : ''}` : '',
          size: isCur ? 2 : 1,
        },
      ];
    });
    // Sort ascending by time (REQUIRED by lightweight-charts v5)
    rawMarkers.sort((a: any, b: any) => a.time - b.time);
    // Deduplicate markers that landed on the exact same bar after snapping —
    // keep the "current trade" marker if present, else the first.
    const seen = new Set<number>();
    const markers = rawMarkers.filter((m: any) => {
      if (seen.has(m.time)) return false;
      seen.add(m.time);
      return true;
    });
    console.log('[TradeDetail] daily-chart markers: %d raw -> %d after sort/dedupe',
      rawMarkers.length, markers.length);
    if (markers.length) createSeriesMarkers(candles, markers as any);

    // Business zones — draw POC/VAH/VAL as horizontal price lines on the candles
    function drawZones(z: any, isToday: boolean) {
      if (!z) return;
      const pocColor = '#dc2626';      // deep red (matches BusinessZones.cpp)
      const vahColor = isToday ? '#f87171' : '#3b82f6';
      const valColor = isToday ? '#86efac' : '#3b82f6';
      const lineW = isToday ? 2 : 1;
      candles.createPriceLine({
        price: z.poc,
        color: pocColor,
        lineWidth: lineW as any,
        lineStyle: 0,
        axisLabelVisible: true,
        title: `${isToday ? '' : `${z.date} `}POC`,
      });
      candles.createPriceLine({
        price: z.vah,
        color: vahColor,
        lineWidth: lineW as any,
        lineStyle: isToday ? 2 : 1,
        axisLabelVisible: true,
        title: `${isToday ? '' : `${z.date} `}VAH`,
      });
      candles.createPriceLine({
        price: z.val,
        color: valColor,
        lineWidth: lineW as any,
        lineStyle: isToday ? 2 : 1,
        axisLabelVisible: true,
        title: `${isToday ? '' : `${z.date} `}VAL`,
      });
      // Single-print bands: render each as a line series with horizontal band
      if (showSingles && z.singles) {
        for (const s of z.singles) {
          candles.createPriceLine({
            price: s.high,
            color: isToday ? '#c084fc' : '#a855f780',
            lineWidth: 1 as any,
            lineStyle: 2,
            axisLabelVisible: false,
            title: '',
          });
          candles.createPriceLine({
            price: s.low,
            color: isToday ? '#c084fc' : '#a855f780',
            lineWidth: 1 as any,
            lineStyle: 2,
            axisLabelVisible: false,
            title: '',
          });
        }
      }
    }
    if (showZones) {
      drawZones(dailyData.today_zones, true);
      if (showPriorZones) {
        for (const z of (dailyData.prior_zones || [])) drawZones(z, false);
      }
    }

    chart.timeScale().fitContent();
    const resize = () => {
      if (dailyChartRef.current) chart.applyOptions({ width: dailyChartRef.current.clientWidth });
    };
    window.addEventListener('resize', resize);
    dailyChartInstance.current = chart;
    return () => {
      window.removeEventListener('resize', resize);
      chart.remove();
      dailyChartInstance.current = null;
    };
    } catch (err) {
      console.error('[TradeDetail] daily-chart effect threw:', err);
      // Re-throw so the ErrorBoundary catches it (and we see the stack).
      throw err;
    }
  }, [dailyData, trade, showZones, showPriorZones, showSingles, dailyInterval]);

  // ── Day summary — derived from dailyData.markers ──
  // Memoized so it ONLY recomputes when dailyData actually changes. MUST be
  // declared BEFORE the early-return below — Rules of Hooks require every
  // hook to run in the same order on every render. Trying to call useMemo
  // after the `if (!trade) return …` exits early on the first render and
  // throws "Rendered more hooks than during the previous render" on the
  // second render when `trade` finally loads.
  const daySummary = useMemo(() => {
    const m = dailyData?.markers ?? [];
    if (!m.length) return null;
    const sorted = [...m].sort((a: any, b: any) => a.exit_time - b.exit_time);
    let running = 0, peak = 0, peakAt = 0, trough = 0, troughAt = 0;
    let curStreak = 0, maxStreak = 0;
    const equity: { t: number; pnl: number; n: number }[] = [];
    const wins: any[] = [], losses: any[] = [], scratches: any[] = [];
    for (let i = 0; i < sorted.length; i++) {
      const t = sorted[i];
      const net = t.net_pnl ?? 0;
      running += net;
      if (running > peak) { peak = running; peakAt = i + 1; }
      if (running < trough) { trough = running; troughAt = i + 1; }
      if (net > 0) { wins.push(t); curStreak = 0; }
      else if (net < 0) { losses.push(t); curStreak += 1; if (curStreak > maxStreak) maxStreak = curStreak; }
      else { scratches.push(t); }
      equity.push({ t: t.exit_time, pnl: Math.round(running * 100) / 100, n: i + 1 });
    }
    const tradeNum = sorted.findIndex((t: any) => t.is_current) + 1;
    const giveBack = peak - running;
    const firstEntry = Math.min(...sorted.map((x: any) => x.entry_time));
    const lastExit = Math.max(...sorted.map((x: any) => x.exit_time));
    return {
      n: sorted.length, wins: wins.length, losses: losses.length, scratches: scratches.length,
      wr: sorted.length ? wins.length / sorted.length : 0,
      net: running, peak, peakAt, trough, troughAt,
      giveBack, maxStreak, tradeNum,
      firstEntry, lastExit, equity,
    };
  }, [dailyData]);

  if (!trade) {
    return <div className="p-8 text-text-2">Loading trade...</div>;
  }

  // Symbol-specific exchange timezone (e.g. ES → America/Chicago → "08:30" at
  // cash open instead of UTC "13:30"). Falls back to UTC for unknown symbols.
  const tz = getExchangeTimezone(trade.root_symbol);
  const tzLabel = getExchangeTzLabel(trade.root_symbol);

  // P&L curve data — split into actual and projected
  const pnlCurve = chartData?.pnl_curve || [];
  const actualPoints = pnlCurve.filter((p: any) => !p.projected);
  const projectedPoints = pnlCurve.filter((p: any) => p.projected);
  const maxPnl = pnlCurve.length > 0 ? Math.max(...pnlCurve.map((p: any) => p.pnl)) : 0;
  const minPnl = pnlCurve.length > 0 ? Math.min(...pnlCurve.map((p: any) => p.pnl)) : 0;
  const projMaxPnl = projectedPoints.length > 0 ? Math.max(...projectedPoints.map((p: any) => p.pnl)) : null;
  const projMinPnl = projectedPoints.length > 0 ? Math.min(...projectedPoints.map((p: any) => p.pnl)) : null;
  const finalPnl = trade.pnl_dollars || 0;

  // Merge actual + projected with separate keys for recharts
  const mergedPnlCurve = pnlCurve.map((p: any) => ({
    ...p,
    actual: p.projected ? null : p.pnl,
    projected_pnl: p.projected ? p.pnl : null,
  }));
  // Bridge: duplicate last actual point as first projected point for continuity
  if (actualPoints.length > 0 && projectedPoints.length > 0) {
    const bridgeIdx = mergedPnlCurve.findIndex((p: any) => p.projected);
    if (bridgeIdx > 0) {
      mergedPnlCurve[bridgeIdx] = {
        ...mergedPnlCurve[bridgeIdx],
        actual: mergedPnlCurve[bridgeIdx - 1].actual,
      };
    }
  }

  // Format time for P&L curve tooltip
  const formatPnlTime = (timeS: number) => {
    const d = new Date(timeS * 1000);
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  // (daySummary is computed above the early-return — Rules of Hooks compliance.)

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      {/* Back + header */}
      <div className="flex items-center gap-4">
        <button onClick={() => navigate(-1)}
          className="p-2 rounded-lg hover:bg-surface-3 transition-colors">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h2 className="text-xl font-semibold">{trade.symbol}</h2>
            <span className={cn(
              'px-2 py-0.5 rounded text-xs font-medium',
              trade.side === 'LONG' ? 'bg-green/15 text-green' : 'bg-red/15 text-red'
            )}>
              {trade.side}
            </span>
            {trade.is_open ? (
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow/15 text-yellow">OPEN</span>
            ) : null}
          </div>
          <p className="text-sm text-text-3 mt-0.5">
            {trade.trade_date} &middot; {trade.account}
          </p>
        </div>
        <div className="text-right">
          <div className={cn('text-2xl font-bold', pnlColor(trade.net_pnl ?? trade.pnl_dollars))}>
            {formatPnl(trade.net_pnl ?? trade.pnl_dollars)}
          </div>
          {trade.commissions > 0 && (
            <div className="text-xs text-text-3 mt-0.5">
              Gross {formatPnl(trade.pnl_dollars)} &middot; Fees -${trade.commissions?.toFixed(2)}
            </div>
          )}
        </div>
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
        {[
          { label: 'Entry', value: trade.entry_price?.toFixed(2) },
          { label: 'Exit', value: trade.exit_price?.toFixed(2) || 'Open' },
          { label: 'Quantity', value: trade.entry_qty },
          { label: 'Gross P&L', value: formatPnl(trade.pnl_dollars) },
          { label: 'Commissions', value: trade.commissions ? `-$${trade.commissions.toFixed(2)}` : '-' },
          { label: 'Duration', value: formatDuration(trade.duration_seconds) },
          { label: 'Exit Type', value: trade.exit_order_type || '-' },
          { label: 'MFE / MAE', value: pnlCurve.length > 0
            ? `${formatCurrency(maxPnl)} / ${formatCurrency(minPnl)}`
            : '-' },
        ].map((m, i) => (
          <div key={i} className="bg-surface-2 border border-border rounded-lg p-3">
            <p className="text-xs text-text-3">{m.label}</p>
            <p className="text-sm font-medium mt-0.5">{m.value}</p>
          </div>
        ))}
      </div>

      {/* Price Chart */}
      <div className="bg-surface-2 border border-border rounded-xl p-4">
        <h3 className="text-sm font-medium text-text-2 mb-3">
          Price Chart (15s bars)
          <span className="ml-2 text-xs text-text-3 font-normal">· times in {tzLabel}</span>
        </h3>
        <div ref={chartRef} className="w-full" />
        {(!chartData || !chartData.bars?.length) && (
          <p className="text-text-3 text-sm py-12 text-center">
            No tick data available for this date/symbol.
          </p>
        )}
      </div>

      {/* Wide Context Chart */}
      <div className="bg-surface-2 border border-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="text-sm font-medium text-text-2">
            Context Chart ({wideInterval === 60 ? '1min' : '5min'} bars, +/- 3h)
            <span className="ml-2 text-xs text-text-3 font-normal">· times in {tzLabel}</span>
          </h3>
          <div className="flex items-center gap-1 text-xs">
            <span className="text-text-3 mr-1">Interval:</span>
            {[
              { v: 60, label: '1m' },
              { v: 300, label: '5m' },
            ].map(opt => (
              <button key={opt.v} onClick={() => setWideInterval(opt.v)}
                className={cn(
                  'px-2 py-1 rounded font-medium transition-colors',
                  wideInterval === opt.v ? 'bg-accent text-white' : 'bg-surface-3 text-text-2 hover:bg-surface-4'
                )}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <div ref={wideChartRef} className="w-full" />
        {(!wideChartData || !wideChartData.bars?.length) && (
          <p className="text-text-3 text-sm py-8 text-center">No tick data available.</p>
        )}
      </div>

      {/* Day Summary Card */}
      {daySummary && (
        <div className="bg-surface-2 border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h3 className="text-sm font-medium text-text-2">
              Day Summary
              <span className="ml-2 text-xs text-text-3 font-normal">
                {trade.trade_date} · {trade.root_symbol}
                {daySummary.tradeNum > 0 && (
                  <span className="ml-2 text-accent">
                    · This is trade {daySummary.tradeNum} of {daySummary.n}
                  </span>
                )}
              </span>
            </h3>
            <div className="text-xs text-text-3">
              {formatClockInTz(daySummary.firstEntry, tz)} → {formatClockInTz(daySummary.lastExit, tz)} {tzLabel}
            </div>
          </div>

          {/* KPI grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 mb-4">
            <div className="bg-surface-3 border border-border rounded-lg p-2.5">
              <p className="text-xs text-text-3">Trades</p>
              <p className="text-base font-semibold mt-0.5">{daySummary.n}</p>
              <p className="text-xs text-text-3 mt-0.5">
                <span className="text-green">{daySummary.wins}W</span>
                {' / '}
                <span className="text-red">{daySummary.losses}L</span>
                {daySummary.scratches ? <span className="text-text-3">{' / '}{daySummary.scratches}S</span> : null}
              </p>
            </div>
            <div className="bg-surface-3 border border-border rounded-lg p-2.5">
              <p className="text-xs text-text-3">Win Rate</p>
              <p className="text-base font-semibold mt-0.5">{(daySummary.wr * 100).toFixed(0)}%</p>
            </div>
            <div className="bg-surface-3 border border-border rounded-lg p-2.5">
              <p className="text-xs text-text-3">Net P&L</p>
              <p className={cn(
                'text-base font-semibold mt-0.5',
                daySummary.net >= 0 ? 'text-green' : 'text-red'
              )}>
                {fmtMoney(daySummary.net)}
              </p>
            </div>
            <div className="bg-surface-3 border border-border rounded-lg p-2.5">
              <p className="text-xs text-text-3">Peak</p>
              <p className="text-base font-semibold mt-0.5 text-green">{fmtMoney(daySummary.peak)}</p>
              {daySummary.peakAt > 0 && (
                <p className="text-xs text-text-3 mt-0.5">at #{daySummary.peakAt}</p>
              )}
            </div>
            <div className="bg-surface-3 border border-border rounded-lg p-2.5">
              <p className="text-xs text-text-3">Trough</p>
              <p className="text-base font-semibold mt-0.5 text-red">{fmtMoney(daySummary.trough)}</p>
              {daySummary.troughAt > 0 && (
                <p className="text-xs text-text-3 mt-0.5">at #{daySummary.troughAt}</p>
              )}
            </div>
            <div className="bg-surface-3 border border-border rounded-lg p-2.5">
              <p className="text-xs text-text-3">Give-back</p>
              <p className={cn(
                'text-base font-semibold mt-0.5',
                daySummary.giveBack > 200 ? 'text-red' :
                daySummary.giveBack > 100 ? 'text-yellow' : 'text-text'
              )}>
                {fmtMoney(-daySummary.giveBack)}
              </p>
              <p className="text-xs text-text-3 mt-0.5">from peak</p>
            </div>
            <div className="bg-surface-3 border border-border rounded-lg p-2.5">
              <p className="text-xs text-text-3">Max Streak</p>
              <p className={cn(
                'text-base font-semibold mt-0.5',
                daySummary.maxStreak >= 5 ? 'text-red' :
                daySummary.maxStreak >= 3 ? 'text-yellow' : 'text-text'
              )}>
                {daySummary.maxStreak}L
              </p>
              <p className="text-xs text-text-3 mt-0.5">consec losses</p>
            </div>
          </div>

          {/* Intraday equity sparkline */}
          <div className="h-32">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={daySummary.equity} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="dayPosGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#22c55e" stopOpacity={0.4} />
                    <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="dayNegGrad" x1="0" y1="1" x2="0" y2="0">
                    <stop offset="0%" stopColor="#ef4444" stopOpacity={0.4} />
                    <stop offset="100%" stopColor="#ef4444" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                <XAxis
                  dataKey="t"
                  tick={{ fill: '#6b6f8a', fontSize: 10 }}
                  tickLine={false}
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  tickFormatter={(t: any) => formatClockInTz(t as number, tz)}
                />
                <YAxis
                  tick={{ fill: '#6b6f8a', fontSize: 10 }}
                  tickLine={false}
                  tickFormatter={v => `$${v}`}
                  width={50}
                />
                <Tooltip
                  contentStyle={{ background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 8, color: '#e4e6f0', fontSize: 12 }}
                  labelFormatter={(v: any) => formatClockInTz(v as number, tz)}
                  formatter={(v: any, _name: any, props: any) => [
                    formatCurrency(v as number),
                    `Running P&L (after trade #${props.payload.n})`,
                  ]}
                />
                <ReferenceLine y={0} stroke="#363952" strokeDasharray="3 3" />
                {daySummary.peak > 0 && (
                  <ReferenceLine y={daySummary.peak} stroke="#22c55e60" strokeDasharray="2 4" label={{ value: 'peak', fill: '#22c55e', fontSize: 9, position: 'insideTopRight' }} />
                )}
                {daySummary.trough < 0 && (
                  <ReferenceLine y={daySummary.trough} stroke="#ef444460" strokeDasharray="2 4" label={{ value: 'trough', fill: '#ef4444', fontSize: 9, position: 'insideBottomRight' }} />
                )}
                <Area
                  type="stepAfter"
                  dataKey="pnl"
                  stroke={daySummary.net >= 0 ? '#22c55e' : '#ef4444'}
                  strokeWidth={1.6}
                  fill={daySummary.net >= 0 ? 'url(#dayPosGrad)' : 'url(#dayNegGrad)'}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Whole-Day Chart with Business Zones */}
      <div className="bg-surface-2 border border-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h3 className="text-sm font-medium text-text-2">
            Day Chart ({dailyInterval === 60 ? '1min' : '5min'} bars, whole RTH, {tzLabel})
            {dailyData?.today_zones && (
              <span className="ml-3 text-xs text-text-3 font-normal">
                <span className="text-red-400 font-mono">POC {dailyData.today_zones.poc.toFixed(2)}</span>
                {'  '}
                <span className="text-red-300 font-mono">VAH {dailyData.today_zones.vah.toFixed(2)}</span>
                {'  '}
                <span className="text-green-300 font-mono">VAL {dailyData.today_zones.val.toFixed(2)}</span>
              </span>
            )}
          </h3>
          <div className="flex items-center gap-3 text-xs flex-wrap">
            <div className="flex items-center gap-1">
              <span className="text-text-3 mr-1">Interval:</span>
              {[
                { v: 60, label: '1m' },
                { v: 300, label: '5m' },
              ].map(opt => (
                <button key={opt.v} onClick={() => setDailyInterval(opt.v)}
                  className={cn(
                    'px-2 py-1 rounded font-medium transition-colors',
                    dailyInterval === opt.v ? 'bg-accent text-white' : 'bg-surface-3 text-text-2 hover:bg-surface-4'
                  )}>
                  {opt.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={showZones} onChange={e => setShowZones(e.target.checked)} />
                <span className="text-text-2">Zones</span>
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={showPriorZones} onChange={e => setShowPriorZones(e.target.checked)} disabled={!showZones} />
                <span className="text-text-2">Prior days</span>
              </label>
              <label className="flex items-center gap-1 cursor-pointer">
                <input type="checkbox" checked={showSingles} onChange={e => setShowSingles(e.target.checked)} disabled={!showZones} />
                <span className="text-text-2">Single prints</span>
              </label>
            </div>
          </div>
        </div>
        <div ref={dailyChartRef} className="w-full" />
        {(!dailyData || !dailyData.bars?.length) && (
          <p className="text-text-3 text-sm py-8 text-center">No tick data available.</p>
        )}
      </div>

      {/* Trade Card — isolated component so typing doesn't re-render the rest
          of the page (the day-summary sparkline + P&L Recharts are expensive). */}
      <TradeCardForm
        tradeId={trade.id}
        initial={{
          setup_name: trade.setup_name,
          trade_idea: trade.trade_idea,
          what_good: trade.what_good,
          what_bad: trade.what_bad,
          notes: trade.notes,
          rating: trade.rating,
        }}
        setupLibrary={setupLibrary}
      />

      {/* Unrealized P&L Curve */}
      {pnlCurve.length > 0 && (
        <div className="bg-surface-2 border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-text-2">
              Unrealized P&L During Trade
              <span className="text-text-3 font-normal ml-2">
                (MFE: <span className="text-green">{formatCurrency(maxPnl)}</span>
                {' '}MAE: <span className="text-red">{formatCurrency(minPnl)}</span>
                {projMaxPnl != null && (
                  <span className="ml-2">| Projected MFE: <span className="text-blue">{formatCurrency(projMaxPnl)}</span>
                  {' '}MAE: <span className="text-yellow">{formatCurrency(projMinPnl)}</span></span>
                )})
              </span>
            </h3>
            <div className="flex items-center gap-2">
              <span className="text-xs text-text-3">Look-forward:</span>
              {[0, 30, 60, 120, 300, 600].map(s => (
                <button key={s} onClick={() => setLookahead(s)}
                  className={cn(
                    'px-2 py-1 rounded text-xs font-medium transition-colors',
                    lookahead === s ? 'bg-accent text-white' : 'bg-surface-3 text-text-2 hover:bg-surface-4'
                  )}>
                  {s === 0 ? 'Off' : s < 60 ? `${s}s` : `${s / 60}m`}
                </button>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={mergedPnlCurve}>
              <defs>
                <linearGradient id="pnlActualGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={finalPnl >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={finalPnl >= 0 ? '#22c55e' : '#ef4444'} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="pnlProjGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#6366f1" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
              <XAxis
                dataKey="time"
                tick={{ fill: '#6b6f8a', fontSize: 10 }}
                tickLine={false}
                tickFormatter={formatPnlTime}
              />
              <YAxis
                tick={{ fill: '#6b6f8a', fontSize: 11 }}
                tickLine={false}
                tickFormatter={v => `$${v}`}
              />
              <Tooltip
                contentStyle={{ background: '#1c1f2e', border: '1px solid #2a2d3e', borderRadius: 8, color: '#e4e6f0' }}
                formatter={(v: number | null, name: string) => {
                  if (v == null) return ['-', ''];
                  const label = name === 'actual' ? 'Actual P&L' : 'Projected P&L';
                  return [formatCurrency(v), label];
                }}
                labelFormatter={formatPnlTime}
              />
              <ReferenceLine y={0} stroke="#363952" strokeDasharray="3 3" />
              <Area
                type="monotone"
                dataKey="actual"
                stroke={finalPnl >= 0 ? '#22c55e' : '#ef4444'}
                fill="url(#pnlActualGrad)"
                strokeWidth={1.5}
                dot={false}
                connectNulls={false}
              />
              {lookahead > 0 && (
                <Area
                  type="monotone"
                  dataKey="projected_pnl"
                  stroke="#6366f1"
                  fill="url(#pnlProjGrad)"
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                  dot={false}
                  connectNulls={false}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
          {lookahead > 0 && (
            <div className="flex items-center gap-4 mt-2 text-xs text-text-3">
              <span className="flex items-center gap-1">
                <span className="w-3 h-0.5 inline-block rounded" style={{ background: finalPnl >= 0 ? '#22c55e' : '#ef4444' }} />
                Actual P&L
              </span>
              <span className="flex items-center gap-1">
                <span className="w-3 h-0.5 inline-block rounded border-b border-dashed" style={{ borderColor: '#6366f1' }} />
                What-if (stayed {lookahead < 60 ? `${lookahead}s` : `${lookahead / 60}m`} longer)
              </span>
            </div>
          )}
        </div>
      )}

      {/* Fills table */}
      <div className="bg-surface-2 border border-border rounded-xl overflow-hidden">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-medium text-text-2">Fills ({fills.length})</h3>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-text-3 text-xs uppercase">
              <th className="text-left px-4 py-2">Time</th>
              <th className="text-left px-4 py-2">Side</th>
              <th className="text-right px-4 py-2">Price</th>
              <th className="text-right px-4 py-2">Qty</th>
              <th className="text-left px-4 py-2">Type</th>
              <th className="text-left px-4 py-2">Order ID</th>
            </tr>
          </thead>
          <tbody>
            {fills.map((f, i) => (
              <tr key={i} className="border-b border-border/50">
                <td className="px-4 py-2 font-mono text-xs">{formatTime(f.timestamp)}</td>
                <td className="px-4 py-2">
                  <span className={f.side === 'BUY' ? 'text-green' : 'text-red'}>{f.side}</span>
                </td>
                <td className="px-4 py-2 text-right font-mono">{f.price?.toFixed(2)}</td>
                <td className="px-4 py-2 text-right">{f.quantity}</td>
                <td className="px-4 py-2 text-text-2">{f.order_type}</td>
                <td className="px-4 py-2 text-text-3 font-mono text-xs">{f.order_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Notes & Rating moved into the Trade Card above — single source of truth. */}
    </div>
  );
}
