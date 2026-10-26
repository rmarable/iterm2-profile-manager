# Roadmap

Planned work, newest plans last. Done items move to the release notes.

| When | What | Why then |
|---|---|---|
| Week of 2026-11-23 (reminder set for Tue 2026-11-24, 9:00 AM ET) | **Launch history view.** Chart the `launches` table: sessions per day and mode, favorite vs new, colored vs plain, how long each look stays open, which favorites get used. Compare with the odds from `.tools/srcp_odds.py`. Counts and durations only, no times of day. Treat `end_reason = 'changed'` rows (re-rolled away from, since 0.7.0) as rejections, not short sessions. | On 2026-10-24 there were 61 sessions over 6 days, too few to compare with the odds. A month gives a few hundred. |
