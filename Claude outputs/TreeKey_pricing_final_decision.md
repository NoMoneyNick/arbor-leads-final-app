# TreeKey pricing — final plan

Plain language, no jargon. This replaces the earlier draft. Once you've seen this, I'll build it into the code straight away.

## 1. The 5 subscription plans (replacing the current 8)

| Plan | Price/month | How far it reaches | Leads included/month | Who it's for |
|---|---|---|---|---|
| Starter | £39 | 15 miles | 6 | 1-2 van operators, all job types |
| Growth | £79 | 20 miles | 10 | Established 2-3 man crews |
| Consultant | £99 | 20 miles | 8 | Qualified arborists — planning/survey work |
| Commercial & Forestry | £159 | 30 miles | 14 | Heavy machinery, big clearances, tenders |
| Elite | £249 | 45 miles | 20 | Full access, first look at the best leads, top priority |

This is what you already agreed to. I'm building this now — no more input needed from you on this part.

## 2. Pay-per-lead pricing (buying one lead at a time, no subscription)

I found the real system doesn't actually price by job size like the old draft said — it prices by how fresh the council notice is. So this replaces the old flat £19/£29/£49 with a version that also accounts for how valuable/urgent a lead is:

| | Fresh (just found) | Older (still active) |
|---|---|---|
| Ordinary lead | £29 | £19 |
| Notable lead (bigger scale/commercial) | £39 | £29 |
| Top-tier lead (urgent or legally significant) | £49 | £39 |

Only one genuinely new price here (£39) — everything else already exists and is tested.

## 3. The "London has loads of work, rural areas don't" problem

This is real, and you were right to raise it. Here's the honest answer: you don't need to rebuild anything before launch to handle it. The pay-per-lead option above already solves it — someone in a quiet area just buys leads one at a time instead of a subscription that promises more than the area can deliver. The fix is in how the pricing page explains this, not new code.

Two upgrades worth doing, but AFTER launch, not before (they cost more engineering time and aren't needed to open the doors honestly):
- Let unused leads roll over one extra month, so a quiet month doesn't feel like wasted money.
- Eventually price subscriptions by area density, the way Zillow does with property ads. Not needed yet — you don't have enough real customers yet to know the real numbers per area.

## 4. Exclusive leads — this is your actual advantage

I checked what your closest real competitor (Planr) promises, and it does NOT guarantee a lead is exclusive to one contractor. Neither does anyone else I checked. If TreeKey genuinely can promise "only you get this lead," and it's actually true, that's a real difference, not just marketing.

The problem: the code to enforce that already exists in your project but isn't switched on anywhere. My recommendation: treat wiring this up as a real launch requirement, not a someday task — it's the one thing that actually justifies your prices being higher than a competitor's. I'd tackle this as the next piece of work after today's pricing changes are live.

## 5. The two false claims on your £179 plan

Already fixed — I checked and this was already done. Nothing more needed here.

## What happens next

I'm implementing #1 and #2 in the code right now. #3 needs no work before launch. #4 (real exclusivity) is the next thing I'd build after that, if you agree.
