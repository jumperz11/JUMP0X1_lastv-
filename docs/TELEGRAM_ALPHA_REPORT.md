# Telegram Alpha Report: The Real Edge

**Status:** ARCHIVE (Historical Research)
**Note:** Research that informed the pivot from arbitrage to directional trading.

---

**Date:** 2025-12-20
**Source:** Polymarket Lounge Telegram (5,254 messages)
**Focus:** Binance/Chainlink prediction edge, execution mechanics, who's profitable

---

## Executive Summary

The real alpha is in **LATENCY ARBITRAGE between Binance and Polymarket/Chainlink**. But there's a catch: the **500ms taker speed bump** neutered most directional strategies. Gabagool survives because he uses **limit orders only** (maker), avoiding the speed bump entirely.

---

## TIER 1: THE BINANCE/CHAINLINK PREDICTION EDGE

### The Core Alpha (11 Gold Messages Found)

**InsideTrader (most credible):**
> "There are latency opportunities between the chainlink and binance spot prices, but the strategy will fail at 2+ seconds executions."

> "You can run the comparison on the binance klines also, 98% times matches the PM outcome."

**Paul D (technical details):**
> "Polymarket uses chainlink data stream which is sub second and they only output per second updates, so sure you have delay if you use polym RTDS."

> "We can see binance is faster, do you have exact data on latency?"

> "You think that for the 15min markets he gets signal from binance?"

**Felix Poirier (HFT background):**
> "To get data from Binance to Polymarket that's already almost 150ms."

> "Is RTDS fast compared to binance? I only trade the 1h markets which resolve on Binance."

### How The Oracle Resolution Works

**koco (key insight):**
> "For btc 15m markets where chainlink oracle is the resolver, it's the price of chainlink oracle at the exact moment of the unix timestamp. For example: you have a 2-2:15PM ET btc 15m market, the final resolution price ticks at 2:15:00 which translates to 'price to beat' for the upcoming market that starts at 2:15:01."

> "For hourly markets they are resolved by binance BTC/USDT 1 minute candles. PM does not have united resolver for all crypto markets."

**LL's Mistake (learning moment):**
> "The problem was I did not know that chainlink is actually available. For one I am using the timestamp +1 (so the opening), first mistake. Secondly, I am using coinbase. That's why I asked where you are getting your prices from."

### The Directional Prediction Theory

**BRiaN aMoCCa:**
> "He might have a bot that detected price movement using Binance and/or chainlink API then to determine direction to trigger trade."

**niysso (deep understanding):**
> "If you have good vol model + little spot edge + understand chainlink is just aggregate of spot prices around exchanges. And UI is always late, degens are keep trading and filling spread. And you have 500ms delay for takers.. good combination for MM."

---

## TIER 2: THE 500MS SPEED BUMP (Game Changer)

### What Happened

**dhanush (was profitable, now struggling):**
> "I was making money on this till they introduced the 500ms taker speed bump. Then the PNL dipped."

> "My round trip time for FAK order went up from 100ms to 750ms after the new speed bump rule. And the worst part is for FAK order there is no event raised on the websocket."

**InsideTrader:**
> "500ms delay for taker orders."

> "In my opinion not worth chasing such, edge disappears within 2 seconds in that case."

**Kyle Miller:**
> "500ms taker delay nuked his old strat. Prob making now."

### How Gabagool Survives

**Felix Poirier:**
> "Limit orders and pray some dumb algo buys it."

> "I would measure the time it takes for you to receive the signal. If the sum of signal + Polymarket is sub 200ms, you have a lot of wiggle room. If they remove the speed bump you might have to work a bit harder on this."

**The implication:** Gabagool uses MAKER orders (limit orders), which are NOT subject to the 500ms taker delay. He doesn't take liquidity - he provides it.

---

## TIER 3: EXECUTION & FILL MECHANICS

### The Imbalance Problem

**Bbb447 (key insight):**
> "You don't only buy the side that is dropping. You need some sort of a limit for the share imbalance between up and down shares. For example amount of down shares can only be 10% more than up shares. If you surpass that number your bot has to buy the other side, even if price is too high."

> "He doesn't buy one side very low and waits for the other side to drop as well. He buys hundreds of times every second and never allows his balance of up and down shares to be highly imbalanced."

**LL:**
> "He doesn't get filled every time. He has directional risk almost all the time. He just has over time a 50/50 of being lucky with the market. He actually has a high imbalance inbetween."

> "More trades that move with the market. You cannot do a little amount you need to do many throughout the market time. That way you offset the imbalance."

### The Both Sides Problem

**Aflatoon (reality check):**
> "But it's not always possible to get both side filled at less than 1 dollar. In most cases it might be possible but always no. I have seen people on polymarket trying it compounding each time. 1000 times they won and once they lost means entire pf gone to zero."

**Sevi (common failure):**
> "Are you guys actively looking for pairs that cost less than 1$, and sending buy orders for both positions at the same time? Because I do that and I've never had success with both buy orders fulfilling."

**King:**
> "If one side filled and market not reversed means it wipes previous profit."

### The Hedge Dilemma

**GOOPY:**
> "So am I understanding this right? If I get filled on 1 leg, it's better to fill the second leg at an undesired price (within reason) to hedge my losses?"

> "Had a few go to 0 and it's def not good RR. Problem I need to figure out is the optimal SL to set without missing out on possible hedged fills."

---

## TIER 4: WHO'S ACTUALLY PROFITABLE?

### Confirmed Profitable (or Claims)

| Trader | Evidence | Strategy |
|--------|----------|----------|
| **Gabagool22** | Public PnL | Market making, limit orders |
| **Nash0** | "another great success!" (3x) | Similar to Gabagool |
| **dhanush** | "Was making money... until 500ms bump" | Taker (now broken) |
| **Serion** | "Yeah I'm winning consistently now" | Unknown |
| **Tuna** | "Still profitable" (with hedging) | Late snipe + hedge |
| **LL** | "I am actually making money" | Spread inefficiencies |

### Confirmed Not Profitable

| Trader | Reason |
|--------|--------|
| Most of the group | Can't get both sides filled |
| Anyone using taker orders | 500ms delay kills edge |
| Late snipers without hedge | One flip wipes 99 wins |
| Copy traders | Latency + fill constraints |

### The Honest Ones

**Jayden:**
> "There is no such thing as consistent without adapting the bots daily. I also lost money and still losing money everytime I try a new strategy. It's part of the game."

**Alexis:**
> "Some people will successfully implement the bot, some people will not. Some people will make money, some people will not."

**LL:**
> "I am actually happy to see me losing. Making my safety tighter, making sure nothing explodes. It's little money and it's education fee."

---

## TIER 5: TIMING & EDGE DECAY

### When Edge Exists

**Jayden:**
> "Gabagool becomes profitable usually after 3 minutes."

**Okyo:**
> "It requires early access because the edge exists only when markets first open and are illiquid; once prices normalize, the opportunity is gone."

**GO2049:**
> "In the first few minutes, should you buy YES or NO when the pair cost is always around 1?"

### Edge Decay Speed

**InsideTrader:**
> "Edge starts decaying already when the master gets in."

> "Edge disappears within 2 seconds in that case."

**ten:**
> "imo 15m timeframe is too volatile for size. all edges erode eventually so gotta adapt."

### The Long-Term View

**AlfaBlok (philosophical):**
> "What do you guys think the end game is here? Is this like a short race, where there's a few months' window before this goes fully public, and then edge fully evaporates? Like are we all trying to get in early while this lasts (knowing it won't)? Or do you see it as you can actually become a long term profitable market maker? Won't this become fully dominated by the citadels and jane streets?"

**🏎️💨:**
> "Most tradable edges behave like finite resources. The 'end date' isn't fixed, but determined by how quickly other participants discover and exploit the same structural inefficiencies. What matters is being early in the discovery phase."

---

## LATENCY BENCHMARKS (From Chat)

| Location/Setup | Latency | Notes |
|----------------|---------|-------|
| Frankfurt VPS | <1ms ping to PM | Madalin |
| AWS OSAKA | 5ms Binance, 200ms PM | dhanush |
| Home connection | 800ms market, 300ms limit | Tuna |
| Optimized setup | 32ms signal to OB | Paul D |
| HFT target | Sub-200ms total | Felix Poirier |

**Felix Poirier:**
> "Polymarket is on AWS, you can be speed competitive without paying 100k a month."

> "SIG and other OMMs are usually really good because they have deep enough pockets to pay for fibre & collocation at the large exchanges."

---

## TECHNICAL REQUIREMENTS (From Chat)

### Data Sources
1. **15m markets:** Chainlink RTDS (sub-second)
2. **1h markets:** Binance BTC/USDT 1-minute candles
3. **Spot price:** Binance websocket (fastest)

### Infrastructure
- AWS EC2 (same cloud as Polymarket)
- Frankfurt or US-East location
- Rust for speed (Python too slow)
- Proper websocket reconnection logic

### Order Types
- **Limit orders (MAKER):** No 500ms delay
- **FAK orders (TAKER):** 500ms delay, no websocket events
- **FOK orders:** All-or-nothing

---

## CONCLUSIONS

### The Real Alpha

1. **Binance leads Chainlink by ~150ms** - This is exploitable for directional bets
2. **500ms taker delay** killed most taker strategies - Maker-only now
3. **Gabagool uses limit orders** - Avoids speed bump entirely
4. **Fill precision is suspicious** - May have privileged access

### Why Your Backtest is Broken

1. **Assumes fills that don't happen** - Especially DOWN side
2. **No 500ms delay simulation** - Critical for taker orders
3. **No queue position** - First-in-queue gets filled
4. **No partial fills** - Reality is messier

### What Would Actually Work

1. **Pure market making** with limit orders (no taker delay)
2. **Directional bias** from Binance price feed
3. **Aggressive rebalancing** when imbalanced
4. **Volume-based sizing** (more trades when volatile)
5. **Sub-200ms infrastructure** on AWS

### The Uncomfortable Truth

**koco's Insight:**
> "I think it is some privileged shit going on here and we are just wasting our time to chase a ghost."

**The Arranger:**
> "I believe the edge is gone. Been watching for last 30 minutes, not a single time the sum went below 1.00."

---

## ACTIONABLE ITEMS

### For Your Bot

1. **Switch to MAKER orders only** (limit orders)
2. **Add Binance websocket** for directional signal
3. **Track Chainlink RTDS** for resolution price
4. **Reduce infrastructure latency** (AWS, Rust)
5. **Implement aggressive rebalancing** when imbalanced

### What To Test

1. Does Binance lead PM by 150ms+ consistently?
2. Can you get fills on limit orders at your prices?
3. What's your actual fill rate per side?
4. How often are you stuck one-sided?

---

*Report generated from 5,254 Telegram messages*
*Keywords searched: binance, chainlink, oracle, direction, predict, signal, fill, imbalance, latency, profitable*
