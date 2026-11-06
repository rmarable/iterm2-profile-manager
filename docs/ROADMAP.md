# Roadmap

Planned work, newest plans last. Done items move to the release notes.

| When | What | Why then |
|---|---|---|
| Week of 2026-11-23 (reminder set for Tue 2026-11-24, 9:00 AM ET) | **Launch history view.** Chart the `launches` table: sessions per day and mode, favorite vs new, colored vs plain, how long each look stays open, which favorites get used. Compare with the odds from `.tools/srcp_odds.py`. Counts and durations only, no times of day. Treat `end_reason = 'changed'` rows (re-rolled away from, since 0.7.0) as rejections, not short sessions. | On 2026-10-24 there were 61 sessions over 6 days, too few to compare with the odds. A month gives a few hundred. |
| Not scheduled | **Parallel color calls in iTerm2.** Propose a change to iTerm2 (open source, upstream, not this repo) so it handles color-setting API calls for different sessions in parallel. | Measured 2026-11-06, 32 tabs: with one API call per color (about 30 per tab), about 11 s in total at every cap from 1 to 32, so coloring more tabs at once on our side didn't finish sooner. Sending each tab's colors in one call took it to 2.5 s (cap 1) and 1.5 s (cap 2), which takes most of the gain on our side; the rest would need iTerm2 itself. Where iTerm2 serializes the calls is not yet checked in its source. |
