# Telegram Chat Analysis Report: Polymarket Binary Options Trading

**Status:** ARCHIVE (Historical Research)
**Note:** Research that informed the pivot from arbitrage to directional trading.

---

**Date:** 2025-12-20
**Source:** Polymarket Lounge Telegram Group (5,254 messages analyzed)
**Keywords Searched:** fill, queue, balanced, execution, maker/taker, liquidity, partial, arbitrage, edge, EV

---

## Executive Summary

The Telegram group is focused on replicating "Gabagool" - a legendary Polymarket trader who consistently profits from 15-minute binary options (BTC/ETH UP/DOWN markets). After analyzing 716 relevant messages, the **core finding is sobering: the strategy appears mathematically sound but practically impossible to execute without privileged access or insider edge.**

---

## Key Contributors & Their Credibility

### Tier 1: Most Insightful (Likely Profitable or Deep Understanding)

| Name | Messages | Key Insight | Credibility |
|------|----------|-------------|-------------|
| **InsideTrader** | 100 | "+EV requires win rate > avg entry price" | HIGH - States mathematical truth |
| **Kizo Azuki** | 76 | "Gabagool always ends with perfect variance - impossible without privileged fills" | HIGH - Did forensic analysis |
| **Felix Poirier** | 184 | "Making a robust backtester is probably not worth your time" | HIGH - Claims HFT background |
| **LL** | 235 | "He doesn't get filled every time. He has directional risk almost all the time" | MEDIUM - Active observer |
| **Bbb447** | 91 | "Never allow balance imbalance >10%" | MEDIUM - Understands the mechanic |

### Tier 2: Active Builders (Learning/Struggling)

| Name | Messages | Status |
|------|----------|--------|
| Nash0 | 235 | Running bot, claims some success |
| Jayden | 163 | Group admin, released tools/tutorials |
| GOOPY | 99 | Building, dealing with one-sided losses |
| Richard | ~50 | Analyzing Gabagool data |
| Tony Stark | ~30 | "Bot only works in sideways market" |

### Tier 3: Noise (Asking basic questions, no insights)
- Most of the 5,000+ messages

---

## Critical Insights by Topic

### 1. THE FILL PROBLEM (Your Core Question)

**Kizo Azuki's Forensic Analysis:**
> "In all markets, Gabagool manages to buy exactly the same +/-X shares variance for UP and DOWN. For example, bought 1490.05 shares total (745.03 each). UP variance was -39.88, DOWN variance was exactly +39.88. This happens for ALL his trades."

> "You can calculate the shares needed to correct imbalance, but market will fill your order 100%? Somehow in his case always yes."

> "Even a 0.5 share filled in a different direction would show as an anomaly and it is not happening."

**What this means:** Gabagool appears to have guaranteed fills - either through:
1. Privileged API access (insider)
2. Being the market maker himself
3. Extremely sophisticated fill prediction algorithm
4. Or... he's a Polymarket insider

**LL's Reality Check:**
> "He doesn't get filled every time. He has directional risk almost all the time. He just has over time a 50/50 of being lucky with the market. He actually has a high imbalance inbetween."

> "I don't think so actually. I think he uses exclusively limit orders. Many are not getting filled at the start, but later when the market comes back."

### 2. THE MATHEMATICAL DOOM (InsideTrader's Wisdom)

**The Rule:**
> "In binary markets it's simple, to make a profit (+EV), your win rate must be higher than the average price you paid."

**The Problem:**
> "I don't want to be the party pooper, but this strategy is mathematically doomed. If you're buying at 99.5 cents in the final seconds, you're risking 99.5 to make 0.5. You need to be right 200 times just to cover one loss—and markets actually do flip in the final seconds multiple times per week."

**The Edge Decay:**
> "Edge starts decaying already when the master gets in."

### 3. THE BALANCED POSITION REQUIREMENT

**Bbb447's Key Insight:**
> "You don't only buy the side that is dropping. You need some sort of a limit for the share imbalance between up and down shares. For example amount of down shares can only be 10% more than up shares. If you surpass that number your bot has to buy the other side, even if price is too high."

> "He doesn't buy one side very low and waits for the other side to drop as well. He buys hundreds of times every second and never allows his balance of up and down shares to be highly imbalanced."

**Aflatoon's Warning:**
> "But it's not always possible to get both side filled at less than 1 dollar. In most cases it might be possible but always no. I have seen people on polymarket trying it compounding each time. 1000 times they won and once they lost means entire pf gone to zero."

### 4. LATENCY & EXECUTION

**Felix Poirier (Claims HFT Background):**
> "If you guys want to seriously HFT on polymarket, I would highly recommend spending a good amount of time building a proper data pipeline, with accurate timestamps, reconnecting websockets, etc. We spent months doing this."

> "To get data from Binance to Polymarket that's already almost 150ms."

> "Polymarket is on AWS, you can be speed competitive without paying 100k a month."

> "Making a backtester that is robust enough is probably not worth your time."

**InsideTrader on Latency:**
> "Edge disappears within 2 seconds. From posting the order and then resting on chain takes around 2-3 seconds for me."

### 5. THE ONE-SIDED DEATH SPIRAL

**GOOPY's Experience:**
> "Had a few go to 0 and it's def not good RR. Problem I need to figure out is the optimal SL to set without missing out on possible hedged fills."

**King's Warning:**
> "If one side filled and market not reversed means it wipes previous profit."

**Tony Stark's Limitation:**
> "My bot only works in sideways market. When market gets directional, doesn't fill."

### 6. COPY TRADING GABAGOOL - WHY IT FAILS

**Multiple Confirmations:**
- "By the time you copy, the opportunity is gone. You cannot get the same shares at the same price at a later time."
- "You can't really copy trade gabagool, but you can trade against him and assert dominance." (Felix Poirier - sarcasm)
- "Gabagool aint a bot he's trading manually" (Ukob - likely wrong but shows skepticism)

---

## WHO IS "GETTING IT"?

### Understanding the Problem (But Not Profitable):
1. **Kizo Azuki** - Best forensic analysis, proved Gabagool has impossible fill precision
2. **InsideTrader** - Knows the math, probably not running a bot
3. **Felix Poirier** - HFT background, understands infrastructure requirements
4. **LL** - Active testing, understands the risks

### Possibly Profitable:
1. **Nash0** - Claims success, shares minimal details
2. **Gabagool22** - The mystery whale (possibly insider/market maker)

### Definitely Not Profitable:
- Everyone asking "how do I copy Gabagool"
- Everyone surprised by one-sided losses
- Everyone without sub-100ms latency

---

## KEY QUOTES ABOUT FILLS & BALANCE

### On Getting Both Sides Filled:

> **Sevi:** "Are you guys actively looking for pairs that cost less than 1$, and sending buy orders for both positions at the same time? Because I do that and I've never had success with both buy orders fulfilling."

> **Le Chômeur:** "And finish to one side where you don't want to be filled."

> **Eze:** "With some luck you will fill some orders on both sides, but on most situations your orders won't fill on one side and you will lose money."

> **Paul D:** "I assume that gabagool trades with limit orders based on my analysis but I'm very skeptical on that actually working, how does he get filled every time?"

### On the Imbalance Problem:

> **Kizo Azuki:** "If it's late in the market or you don't get the fills, you will end up with an imbalance."

> **King:** "If one side filled and market not reversed means it wipes previous profit."

> **GOOPY:** "So am I understanding this right? If I get filled on 1 leg, it's better to fill the second leg at an undesired price (within reason) to hedge my losses?"

---

## Technical Observations

### Infrastructure Requirements:
1. **Location:** AWS us-east (Hetzner blocked by Cloudflare)
2. **Latency:** Sub-200ms from signal to order
3. **Data Source:** Binance websocket for BTC/ETH spot
4. **Fill Monitoring:** Cannot rely on balance API (has lag)

### The Speed Bump:
> **Felix Poirier:** "I won't give out my alpha, even though it's certainly not as valuable since the speed bump."

(Polymarket may have implemented anti-HFT measures)

---

## Conclusions

### What The Chat Confirms:

1. **Your backtest is unrealistic** - Everyone struggles with fills
2. **DOWN side harder to fill** - Confirmed by multiple traders
3. **One-sided exposure is fatal** - Confirmed repeatedly
4. **Speed matters enormously** - But even fast traders struggle
5. **Gabagool has an edge no one can replicate** - Possibly insider/privileged

### The Uncomfortable Truth:

**Kizo Azuki's Damning Observation:**
> "I think it is some privileged shit going on here and we are just wasting our time to chase a ghost. Probably some of you know the cry.eth2 account, explain to me who in the world would sell you [winning trades] for 1c??? Looks like some poly insider advantage is going here."

### Recommendation:

The Telegram group, despite 5,000+ messages, has produced **zero confirmed profitable replicators of Gabagool's strategy.** The closest insights suggest:

1. The strategy requires perfect fill execution that may only be possible with insider access
2. Backtesting is misleading because it assumes fills that don't happen
3. The edge, if it exists for retail, is measured in milliseconds
4. One-sided exposure will eventually blow up any account

**Your 98.5% backtest win rate is fiction.** The real world produces 54% on one-sided positions (per your earlier analysis), which at 47.7% average entry price is barely break-even before fees.

---

## Appendix: Message Statistics

| Category | Count |
|----------|-------|
| Total Messages | 5,254 |
| Keyword Matches | 716 |
| Gabagool Mentions | 100+ |
| Fill/Queue/Execution | 97 |
| Both Sides/Balanced | 119 |
| Edge/EV Discussion | 241 |
| Arbitrage Discussion | 52 |

---

*Report generated from ChatExport_2025-12-20*
