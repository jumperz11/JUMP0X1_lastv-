use super::*;
use super::trade::derive_api_creds_from_env;
use polymarket_rs::AuthenticatedClient as PmAuthenticatedClient;
use polymarket_rs::types::{BalanceAllowanceParams, AssetType};

fn balance_of_call_data(address: PmAddress) -> String {
    // balanceOf(address) selector: 0x70a08231
    // abi-encoded address is 32 bytes, right-padded; address is 20 bytes.
    let addr_hex = address.to_string().trim_start_matches("0x").to_string();
    let addr_padded = format!("{:0>64}", addr_hex.to_lowercase());
    format!("0x70a08231{addr_padded}")
}

fn parse_hex_u128(s: &str) -> Option<u128> {
    let hex = s.trim().trim_start_matches("0x");
    if hex.is_empty() {
        return None;
    }
    u128::from_str_radix(hex, 16).ok()
}

/// Try to get Polymarket deposited balance via authenticated API
async fn get_polymarket_balance(logs: &SharedLogs) -> Option<f64> {
    let (signer, api_creds) = derive_api_creds_from_env().await.ok()??;

    let chain_id: u64 = std::env::var("PM_CHAIN_ID")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(137);
    let host = std::env::var("PM_CLOB_HOST")
        .unwrap_or_else(|_| "https://clob.polymarket.com".to_string());
    let funder = funder_address_from_env();

    let mut auth = PmAuthenticatedClient::new(host, signer.clone(), chain_id, Some(api_creds), funder);

    let params = BalanceAllowanceParams::new().asset_type(AssetType::Collateral);
    match auth.get_balance_allowance(params).await {
        Ok(json) => {
            // Response format: {"balance": "1234567", ...} where balance is in USDC micro-units
            if let Some(bal_str) = json.get("balance").and_then(|v| v.as_str()) {
                if let Ok(bal_raw) = bal_str.parse::<u128>() {
                    let bal = bal_raw as f64 / 1_000_000.0;
                    return Some(bal);
                }
            }
            push_log(logs, format!("[usdc] polymarket response: {json}")).await;
            None
        }
        Err(e) => {
            push_log(logs, format!("[usdc] polymarket API error: {e}")).await;
            None
        }
    }
}

pub(crate) async fn usdc_balance_loop(cash_usdc: SharedCash, logs: SharedLogs) -> Result<()> {
    let rpc_url = std::env::var("PM_POLYGON_RPC_URL")
        .unwrap_or_else(|_| "https://polygon-rpc.com".to_string());
    let usdc_contract = std::env::var("PM_USDC_CONTRACT")
        .unwrap_or_else(|_| "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359".to_string());

    let wallet = funder_address_from_env().or_else(|| {
        std::env::var("PM_PRIVATE_KEY")
            .ok()
            .and_then(|pk| PrivateKeySigner::from_str(&pk).ok())
            .map(|s| s.address())
    });
    let Some(wallet) = wallet else {
        push_log(
            &logs,
            "[usdc] disabled (missing PM_LIVE_WALLET_ADDRESS/PM_FUNDER_ADDRESS and PM_PRIVATE_KEY)"
                .to_string(),
        )
        .await;
        return Ok(());
    };

    push_log(&logs, format!("[usdc] polling wallet={wallet:?}")).await;

    let http = reqwest::Client::new();
    let mut tick = tokio::time::interval(Duration::from_secs(15));

    // Try to get Polymarket balance first
    let mut use_polymarket_balance = false;
    if let Some(pm_bal) = get_polymarket_balance(&logs).await {
        push_log(&logs, format!("[usdc] Polymarket deposited: ${pm_bal:.2}")).await;
        *cash_usdc.write().await = Some(pm_bal);
        use_polymarket_balance = true;
    }

    loop {
        tick.tick().await;

        // Try Polymarket API first (deposited balance)
        if use_polymarket_balance {
            if let Some(pm_bal) = get_polymarket_balance(&logs).await {
                *cash_usdc.write().await = Some(pm_bal);
                continue;
            }
        }

        // Fallback to on-chain wallet balance
        let payload = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [
                { "to": usdc_contract, "data": balance_of_call_data(wallet) },
                "latest"
            ]
        });

        let resp = http.post(&rpc_url).json(&payload).send().await;
        let resp = match resp {
            Ok(r) => r,
            Err(e) => {
                push_log(&logs, format!("[usdc] rpc error: {e}")).await;
                continue;
            }
        };
        let json = match resp.json::<serde_json::Value>().await {
            Ok(v) => v,
            Err(e) => {
                push_log(&logs, format!("[usdc] json error: {e}")).await;
                continue;
            }
        };

        let Some(result_hex) = json.get("result").and_then(|v| v.as_str()) else {
            let err = json
                .get("error")
                .cloned()
                .unwrap_or(serde_json::Value::Null);
            push_log(&logs, format!("[usdc] rpc missing result: {err}")).await;
            continue;
        };
        let Some(raw) = parse_hex_u128(result_hex) else {
            push_log(&logs, format!("[usdc] parse hex failed: {result_hex}")).await;
            continue;
        };
        let bal = raw as f64 / 1_000_000.0;
        {
            let mut g = cash_usdc.write().await;
            *g = Some(bal);
        }
    }
}
