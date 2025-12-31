use super::super::Timeframe;

#[derive(Debug, Clone)]
pub(crate) struct Desired {
    pub(crate) px: Option<f64>,
    pub(crate) q: f64,
    pub(crate) why: &'static str,
}

#[derive(Debug, Clone)]
pub(crate) struct StrategyQuote {
    pub(crate) up: Desired,
    pub(crate) down: Desired,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub(crate) struct StrategyCtx<'a> {
    pub(crate) symbol: &'a str,
    pub(crate) timeframe: Timeframe,
    pub(crate) tau_seconds: f64,

    pub(crate) market: &'a str,
    pub(crate) tok_up: &'a str,
    pub(crate) tok_dn: &'a str,

    pub(crate) by: Option<f64>,
    pub(crate) bn: Option<f64>,
    pub(crate) up_mid: Option<f64>,
    pub(crate) down_mid: Option<f64>,

    pub(crate) min_order_size: f64,
    pub(crate) tick_size: f64,

    pub(crate) qy: f64,
    pub(crate) qn: f64,
    pub(crate) current_exposure: f64,
}

pub(crate) trait Strategy {
    fn name(&self) -> &'static str;
    fn quote(&mut self, ctx: &StrategyCtx<'_>) -> StrategyQuote;
}
