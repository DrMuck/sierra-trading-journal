"""
Tilt / revenge / give-back detector for a chosen account (ES only).

For each trading day, builds an intraday P&L curve from the trade sequence and
identifies four failure patterns:

  1) GIVE-BACK days   — peak intraday P&L was > $X but EOD was < $Y
                        (you were up money and gave it back)
  2) MAX-LOSS days    — EOD loss exceeded the daily soft-stop (-$400)
  3) LOSS STREAKS     — N consecutive losing trades within a day
  4) REVENGE clusters — re-entered within < T seconds after a stop-out

For each pattern: print the day, the trades involved (timestamps + PnL), and the
running intraday P&L at each step. Designed to make the patterns visible so they
can become live alerts.
"""
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from config import DB_PATH


def get_trades(account: str, symbols: list[str]):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sym_clause = f" AND root_symbol IN ({','.join('?' for _ in symbols)})"
    rows = conn.execute(f"""
        SELECT id, root_symbol, side, entry_price, exit_price, entry_qty,
               entry_time_ms, exit_time_ms, trade_date, pnl_points, net_pnl
          FROM trades
         WHERE is_open=0 AND exit_time_ms IS NOT NULL
           AND account = ?{sym_clause}
         ORDER BY entry_time_ms
    """, (account, *symbols)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fmt_ny(ms):
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc) - timedelta(hours=4)
    return dt.strftime("%H:%M:%S")


def per_day(trades):
    """Group trades by trade_date in entry-time order."""
    by_date = defaultdict(list)
    for t in trades:
        by_date[t["trade_date"]].append(t)
    return by_date


def detect(by_date, *,
           give_back_peak_min: float = 200,
           give_back_eod_max: float = 0,
           max_loss_threshold: float = -400,
           streak_min: int = 3,
           revenge_window_sec: int = 120):

    give_back_days = []
    max_loss_days = []
    streaks_all = []
    revenge_all = []

    for date in sorted(by_date):
        day_trades = by_date[date]
        # Running PnL with peak tracking
        running = 0.0
        peak = 0.0
        peak_at = None
        eod_loss_streak = 0
        max_streak_for_day = 0
        cur_streak = 0
        streak_start_idx = -1
        streak_records = []
        revenge_records = []

        for i, t in enumerate(day_trades):
            net = t["net_pnl"] or 0
            running += net
            if running > peak:
                peak = running
                peak_at = i

            # Loss streak tracking
            if net < 0:
                if cur_streak == 0:
                    streak_start_idx = i
                cur_streak += 1
                if cur_streak > max_streak_for_day:
                    max_streak_for_day = cur_streak
                # Revenge: gap < threshold to previous trade's exit?
                if i > 0:
                    prev = day_trades[i - 1]
                    if prev["net_pnl"] is not None and prev["net_pnl"] < 0 and prev["exit_time_ms"]:
                        gap_sec = (t["entry_time_ms"] - prev["exit_time_ms"]) / 1000
                        if gap_sec < revenge_window_sec:
                            revenge_records.append({
                                "date": date,
                                "i": i,
                                "gap_sec": gap_sec,
                                "prev": prev, "this": t,
                            })
            else:
                if cur_streak >= streak_min:
                    streak_records.append({
                        "date": date,
                        "start_i": streak_start_idx,
                        "len": cur_streak,
                        "trades": day_trades[streak_start_idx:i],
                    })
                cur_streak = 0
                streak_start_idx = -1

        # Close trailing streak
        if cur_streak >= streak_min:
            streak_records.append({
                "date": date,
                "start_i": streak_start_idx,
                "len": cur_streak,
                "trades": day_trades[streak_start_idx:],
            })

        eod = running
        give_back = peak - eod

        # Classify the day
        is_giveback = (peak >= give_back_peak_min and eod <= give_back_eod_max)
        is_maxloss = (eod <= max_loss_threshold)
        if is_giveback:
            give_back_days.append({
                "date": date, "peak": peak, "eod": eod,
                "give_back": give_back, "peak_at_trade": peak_at,
                "n_trades": len(day_trades), "max_streak": max_streak_for_day,
                "trades": day_trades,
            })
        if is_maxloss:
            max_loss_days.append({
                "date": date, "peak": peak, "eod": eod,
                "give_back": give_back, "n_trades": len(day_trades),
                "max_streak": max_streak_for_day, "trades": day_trades,
            })

        streaks_all.extend(streak_records)
        revenge_all.extend(revenge_records)

    return give_back_days, max_loss_days, streaks_all, revenge_all


def print_give_back_days(days):
    print(f"\n========== GIVE-BACK DAYS ==========")
    print(f"(peak intraday $+200+ but EOD <= $0)\n")
    if not days:
        print("  (none)")
        return
    print(f"{'date':<11} {'#trades':>7} {'peak':>9} {'peak@':>7} {'EOD':>9} {'gave_back':>11} {'streak':>7}")
    total_giveback = 0
    for d in sorted(days, key=lambda x: -x["give_back"]):
        print(f"{d['date']:<11} {d['n_trades']:>7} ${d['peak']:>+7.0f} "
              f"#{d['peak_at_trade'] + 1 if d['peak_at_trade'] is not None else '-':>5} "
              f"${d['eod']:>+7.0f} ${d['give_back']:>+9.0f} {d['max_streak']:>7}")
        total_giveback += d["give_back"]
    print(f"\n  Total $ given back across these days: ${total_giveback:.0f}")


def print_max_loss_days(days, threshold):
    print(f"\n========== MAX-LOSS DAYS (EOD <= ${threshold:.0f}) ==========")
    if not days:
        print("  (none)")
        return
    print(f"{'date':<11} {'#trades':>7} {'peak':>9} {'EOD':>9} {'streak':>7}")
    total_loss = 0
    for d in sorted(days, key=lambda x: x["eod"]):
        print(f"{d['date']:<11} {d['n_trades']:>7} ${d['peak']:>+7.0f} "
              f"${d['eod']:>+7.0f} {d['max_streak']:>7}")
        total_loss += d["eod"]
    print(f"\n  Total EOD loss on these days: ${total_loss:.0f}")


def print_streak_detail(streaks, top_n=10):
    print(f"\n========== TOP {top_n} LOSS STREAKS (>=3 consecutive losers) ==========")
    if not streaks:
        print("  (none)")
        return
    by_len = sorted(streaks, key=lambda s: -s["len"])
    for s in by_len[:top_n]:
        total = sum((t["net_pnl"] or 0) for t in s["trades"])
        print(f"\n  {s['date']}  streak length={s['len']}  total=${total:+.0f}")
        running = 0
        for i, t in enumerate(s["trades"]):
            running += t["net_pnl"] or 0
            entry = fmt_ny(t["entry_time_ms"])
            exit_t = fmt_ny(t["exit_time_ms"]) if t["exit_time_ms"] else "-"
            dur = (t["exit_time_ms"] - t["entry_time_ms"]) / 1000 if t["exit_time_ms"] else 0
            print(f"    #{s['start_i'] + i + 1:>3} {entry} -> {exit_t} ({dur:>5.1f}s) "
                  f"{t['root_symbol']} {t['side']:<5} "
                  f"qty={int(t['entry_qty']):>2} net=${(t['net_pnl'] or 0):>+7.1f} "
                  f"  running=${running:+.0f}")


def print_revenge(revenge_records, top_n=15):
    print(f"\n========== REVENGE CLUSTERS (re-entry within 2 min of prior loss) ==========")
    if not revenge_records:
        print("  (none)")
        return
    print(f"  {len(revenge_records)} revenge re-entries total\n")
    # Group by date
    by_date = defaultdict(list)
    for r in revenge_records:
        by_date[r["date"]].append(r)
    # Sort dates by total revenge damage
    date_damage = sorted(by_date.items(),
                         key=lambda x: sum(((r["this"]["net_pnl"] or 0) for r in x[1])))[:top_n]
    for date, recs in date_damage:
        total_damage = sum((r["this"]["net_pnl"] or 0) for r in recs)
        print(f"  {date}  ({len(recs)} re-entries, total ${total_damage:+.0f})")
        for r in recs:
            prev = r["prev"]; this = r["this"]
            print(f"    #{r['i']+1}  prev exit {fmt_ny(prev['exit_time_ms'])} (${(prev['net_pnl'] or 0):+.0f})  "
                  f"-> next entry {fmt_ny(this['entry_time_ms'])}  gap={r['gap_sec']:>5.0f}s  "
                  f"new trade: ${(this['net_pnl'] or 0):+.0f}")


def print_summary(by_date):
    print(f"\n========== OVERALL DAILY STATS ==========")
    days = list(by_date.keys())
    eod_pnls = [sum((t["net_pnl"] or 0) for t in by_date[d]) for d in days]
    n_trades = [len(by_date[d]) for d in days]
    winners = [p for p in eod_pnls if p > 0]
    losers = [p for p in eod_pnls if p < 0]
    print(f"  Trading days: {len(days)}")
    print(f"  Total net: ${sum(eod_pnls):+.0f}")
    print(f"  Winning days: {len(winners)} (avg ${sum(winners) / len(winners) if winners else 0:.0f})")
    print(f"  Losing days:  {len(losers)} (avg ${sum(losers) / len(losers) if losers else 0:.0f})")
    print(f"  Avg trades/day: {sum(n_trades) / len(n_trades):.1f}")
    # Best and worst
    best = max(zip(days, eod_pnls), key=lambda x: x[1])
    worst = min(zip(days, eod_pnls), key=lambda x: x[1])
    print(f"  Best day:  {best[0]}  ${best[1]:+.0f}")
    print(f"  Worst day: {worst[0]}  ${worst[1]:+.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    ap.add_argument("--symbols", default="ES")
    ap.add_argument("--give_back_peak", type=float, default=200,
                    help="Peak intraday $ required to flag give-back (default 200)")
    ap.add_argument("--give_back_eod", type=float, default=0,
                    help="EOD <= this to confirm give-back (default 0)")
    ap.add_argument("--max_loss", type=float, default=-400,
                    help="Daily soft-stop threshold (default -400)")
    ap.add_argument("--streak", type=int, default=3,
                    help="Min consecutive losers to flag (default 3)")
    ap.add_argument("--revenge_sec", type=int, default=120,
                    help="Max seconds between losing trades to flag as revenge (default 120)")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    trades = get_trades(args.account, symbols)
    print(f"Loaded {len(trades)} closed trades for {args.account} "
          f"(symbols={symbols})")

    by_date = per_day(trades)
    print_summary(by_date)
    give_back_days, max_loss_days, streaks, revenge = detect(
        by_date,
        give_back_peak_min=args.give_back_peak,
        give_back_eod_max=args.give_back_eod,
        max_loss_threshold=args.max_loss,
        streak_min=args.streak,
        revenge_window_sec=args.revenge_sec,
    )
    print_give_back_days(give_back_days)
    print_max_loss_days(max_loss_days, args.max_loss)
    print_streak_detail(streaks, top_n=10)
    print_revenge(revenge, top_n=15)


if __name__ == "__main__":
    main()
