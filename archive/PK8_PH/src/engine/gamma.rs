use crate::engine::types::MarketEvent;
use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Utc};
use reqwest::Url;
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Clone)]
pub struct GammaClient {
    http: reqwest::Client,
    base_url: Url,
}

impl GammaClient {
    pub fn new(base_url: &str) -> Result<Self> {
        let base_url = Url::parse(base_url).context("invalid gamma base url")?;
        Ok(Self {
            http: reqwest::Client::new(),
            base_url,
        })
    }

    pub async fn get_event_by_slug(&self, slug: &str) -> Result<MarketEvent> {
        let url = self
            .base_url
            .join(&format!("events/slug/{slug}"))
            .context("build gamma slug url")?;

        let raw: GammaEvent = self
            .http
            .get(url)
            .send()
            .await
            .context("gamma request failed")?
            .error_for_status()
            .context("gamma non-200")?
            .json()
            .await
            .context("gamma json decode failed")?;

        Ok(raw.into_market_event()?)
    }

    pub async fn list_events_by_series_id(
        &self,
        series_id: &str,
        limit: usize,
    ) -> Result<Vec<MarketEvent>> {
        self.list_events_by_series_id_page(series_id, limit, 0)
            .await
    }

    pub async fn list_events_by_series_id_page(
        &self,
        series_id: &str,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<MarketEvent>> {
        let mut url = self
            .base_url
            .join("events")
            .context("build gamma events url")?;
        {
            let mut qp = url.query_pairs_mut();
            qp.append_pair("series_id", series_id);
            qp.append_pair("limit", &limit.to_string());
            qp.append_pair("offset", &offset.to_string());
        }

        let raws: Vec<GammaEvent> = self
            .http
            .get(url)
            .send()
            .await
            .context("gamma events request failed")?
            .error_for_status()
            .context("gamma events non-200")?
            .json()
            .await
            .context("gamma events json decode failed")?;

        let mut out = Vec::with_capacity(raws.len());
        for raw in raws {
            if let Ok(ev) = raw.into_market_event() {
                out.push(ev);
            }
        }
        Ok(out)
    }
}

#[derive(Debug, Deserialize, Clone)]
#[serde(untagged)]
enum JsonStringOrVec<T> {
    JsonString(String),
    Vec(Vec<T>),
}

impl<T> JsonStringOrVec<T>
where
    T: for<'de> Deserialize<'de>,
{
    fn into_vec(self, label: &'static str) -> Result<Vec<T>> {
        match self {
            JsonStringOrVec::Vec(v) => Ok(v),
            JsonStringOrVec::JsonString(s) => serde_json::from_str(&s)
                .with_context(|| format!("failed to parse {label} json-string field: {s}")),
        }
    }
}

#[derive(Debug, Deserialize)]
struct GammaEvent {
    slug: String,
    #[serde(default)]
    #[serde(rename = "endDate")]
    end_date: Option<String>,
    #[serde(default)]
    #[serde(rename = "seriesSlug")]
    #[allow(dead_code)]
    series_slug: Option<String>,
    #[serde(default)]
    series: Vec<GammaSeries>,
    #[serde(default)]
    markets: Vec<GammaMarket>,
}

#[derive(Debug, Deserialize)]
struct GammaSeries {
    #[serde(default)]
    id: Option<String>,
    #[serde(default)]
    recurrence: Option<String>,
}

#[derive(Debug, Deserialize)]
struct GammaMarket {
    #[serde(default)]
    #[serde(rename = "conditionId")]
    condition_id: Option<String>,
    #[serde(default)]
    outcomes: Option<JsonStringOrVec<String>>,
    #[serde(default)]
    #[serde(rename = "clobTokenIds")]
    clob_token_ids: Option<JsonStringOrVec<String>>,
    #[serde(default)]
    #[serde(rename = "liquidityClob")]
    liquidity_clob: Option<serde_json::Value>,
    #[serde(default)]
    #[serde(rename = "eventStartTime")]
    event_start_time: Option<String>,
    #[serde(default)]
    #[serde(rename = "orderMinSize")]
    order_min_size: Option<serde_json::Value>,
    #[serde(default)]
    #[serde(rename = "orderPriceMinTickSize")]
    order_price_min_tick_size: Option<serde_json::Value>,
}

impl GammaEvent {
    fn into_market_event(self) -> Result<MarketEvent> {
        let end_date = match self.end_date {
            Some(s) => Some(
                DateTime::parse_from_rfc3339(&s)
                    .with_context(|| format!("invalid endDate: {s}"))?
                    .with_timezone(&Utc),
            ),
            None => None,
        };

        let series_id = self.series.get(0).and_then(|s| s.id.clone());
        let series_recurrence = self.series.get(0).and_then(|s| s.recurrence.clone());

        let market = self.markets.get(0);
        let condition_id = market.and_then(|m| m.condition_id.clone());
        let liquidity_clob = market.and_then(|m| match m.liquidity_clob.as_ref() {
            None => None,
            Some(serde_json::Value::Number(n)) => n.as_f64(),
            Some(serde_json::Value::String(s)) => s.parse::<f64>().ok(),
            _ => None,
        });
        let order_min_size = market.and_then(|m| match m.order_min_size.as_ref() {
            None => None,
            Some(serde_json::Value::Number(n)) => n.as_f64(),
            Some(serde_json::Value::String(s)) => s.parse::<f64>().ok(),
            _ => None,
        });
        let min_tick_size = market.and_then(|m| match m.order_price_min_tick_size.as_ref() {
            None => None,
            Some(serde_json::Value::Number(n)) => n.as_f64(),
            Some(serde_json::Value::String(s)) => s.parse::<f64>().ok(),
            _ => None,
        });
        let start_time = market
            .and_then(|m| m.event_start_time.as_deref())
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
            .map(|d| d.with_timezone(&Utc));

        let mut token_ids_by_outcome = BTreeMap::<String, String>::new();
        if let Some(m) = market {
            if let (Some(outcomes), Some(token_ids)) = (&m.outcomes, &m.clob_token_ids) {
                let outcomes = outcomes.clone().into_vec("outcomes")?;
                let token_ids = token_ids.clone().into_vec("clobTokenIds")?;
                if outcomes.len() != token_ids.len() {
                    return Err(anyhow!(
                        "outcomes/token_ids length mismatch: {} vs {}",
                        outcomes.len(),
                        token_ids.len()
                    ));
                }
                for (outcome, token_id) in outcomes.into_iter().zip(token_ids.into_iter()) {
                    token_ids_by_outcome.insert(outcome, token_id);
                }
            }
        }

        Ok(MarketEvent {
            slug: self.slug,
            series_id,
            series_recurrence,
            condition_id,
            end_date,
            start_time,
            liquidity_clob,
            order_min_size,
            min_tick_size,
            token_ids_by_outcome,
        })
    }
}
