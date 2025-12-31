use crate::engine::gamma::GammaClient;
use crate::engine::types::MarketEvent;
use anyhow::{Context, Result};

pub fn derive_current_timestamp_slug(
    prefix: &str,
    recurrence: &str,
    now: chrono::DateTime<chrono::Utc>,
) -> Option<String> {
    let step = match recurrence {
        "15m" => 15 * 60,
        "hourly" | "1h" | "1H" => 60 * 60,
        _ => return None,
    };
    let now_ts = now.timestamp();
    let aligned = now_ts - (now_ts.rem_euclid(step));
    Some(format!("{prefix}-{aligned}"))
}

pub async fn resolve_current_in_series(
    gamma: &GammaClient,
    series_id: &str,
    now: chrono::DateTime<chrono::Utc>,
) -> Result<Option<MarketEvent>> {
    // Prefer the event where start_time <= now < end_date. Fallback: earliest end_date > now.
    let page_limit = 500usize;
    let mut best: Option<MarketEvent> = None;
    let mut best_fallback: Option<MarketEvent> = None;
    for page in 0..200usize {
        let offset = page * page_limit;
        let events = gamma
            .list_events_by_series_id_page(series_id, page_limit, offset)
            .await
            .with_context(|| format!("list events for series_id={series_id} offset={offset}"))?;
        if events.is_empty() {
            break;
        }
        for ev in events {
            let Some(end) = ev.end_date else { continue };
            if end <= now {
                continue;
            }
            // Fallback candidate: earliest future end.
            match &best_fallback {
                None => best_fallback = Some(ev.clone()),
                Some(b) => {
                    let Some(be) = b.end_date else { continue };
                    if end < be {
                        best_fallback = Some(ev.clone());
                    }
                }
            }

            if let Some(start) = ev.start_time {
                if start <= now {
                    match &best {
                        None => best = Some(ev),
                        Some(b) => {
                            let Some(be) = b.end_date else { continue };
                            if end < be {
                                best = Some(ev);
                            }
                        }
                    }
                }
            }
        }
        // The Gamma events listing isn't reliably sorted, so we can't safely early-exit
        // after finding a candidate; keep scanning pages to avoid skipping the true "current" event.
    }
    Ok(best.or(best_fallback))
}

pub async fn resolve_next_in_series(
    gamma: &GammaClient,
    current: &MarketEvent,
    lookahead_events: usize,
) -> Result<Option<MarketEvent>> {
    let Some(current_end) = current.end_date else {
        return Ok(None);
    };

    if let Some(next) = try_timestamp_rollover(gamma, current).await? {
        return Ok(Some(next));
    }

    let Some(series_id) = current.series_id.as_deref() else {
        return Ok(None);
    };

    let page_limit = 500usize;
    let max_pages = (lookahead_events.max(page_limit) + page_limit - 1) / page_limit;

    let mut best: Option<MarketEvent> = None;
    for page in 0..max_pages {
        let offset = page * page_limit;
        let events = gamma
            .list_events_by_series_id_page(series_id, page_limit, offset)
            .await
            .with_context(|| format!("list events for series_id={series_id} offset={offset}"))?;
        if events.is_empty() {
            break;
        }
        for ev in events {
            let Some(end) = ev.end_date else { continue };
            if end <= current_end {
                continue;
            }
            match &best {
                None => best = Some(ev),
                Some(b) => {
                    let Some(best_end) = b.end_date.as_ref() else {
                        continue;
                    };
                    if end < *best_end {
                        best = Some(ev);
                    }
                }
            }
        }
        if best.is_some() && offset > lookahead_events {
            // Avoid paging forever; we already found a candidate.
            break;
        }
    }

    Ok(best)
}

async fn try_timestamp_rollover(
    gamma: &GammaClient,
    current: &MarketEvent,
) -> Result<Option<MarketEvent>> {
    let Some(recurrence) = current.series_recurrence.as_deref() else {
        return Ok(None);
    };
    let Some((prefix, ts)) = split_trailing_epoch(&current.slug) else {
        return Ok(None);
    };
    let step = match recurrence {
        "15m" => 15 * 60,
        "hourly" | "1h" | "1H" => 60 * 60,
        _ => return Ok(None),
    };
    let next_slug = format!("{prefix}{}", ts + step);
    match gamma.get_event_by_slug(&next_slug).await {
        Ok(ev) => Ok(Some(ev)),
        Err(_) => Ok(None),
    }
}

fn split_trailing_epoch(slug: &str) -> Option<(String, i64)> {
    let (prefix, tail) = slug.rsplit_once('-')?;
    if tail.len() != 10 || !tail.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let ts: i64 = tail.parse().ok()?;
    Some((format!("{prefix}-"), ts))
}
