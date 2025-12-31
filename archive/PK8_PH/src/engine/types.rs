use chrono::{DateTime, Utc};
use std::collections::BTreeMap;

#[derive(Debug, Clone)]
pub struct MarketEvent {
    pub slug: String,
    pub series_id: Option<String>,
    pub series_recurrence: Option<String>,
    pub condition_id: Option<String>,
    pub end_date: Option<DateTime<Utc>>,
    pub start_time: Option<DateTime<Utc>>,
    pub liquidity_clob: Option<f64>,
    pub order_min_size: Option<f64>,
    pub min_tick_size: Option<f64>,
    pub token_ids_by_outcome: BTreeMap<String, String>,
}

impl MarketEvent {
    pub fn token_ids(&self) -> Vec<String> {
        self.token_ids_by_outcome.values().cloned().collect()
    }
}
