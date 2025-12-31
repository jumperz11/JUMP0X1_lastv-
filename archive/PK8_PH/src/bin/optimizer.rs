use anyhow::{Context, Result};
use rand::{rngs::StdRng, Rng, SeedableRng};
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
    process::Command,
};

#[derive(Debug, Clone, Copy)]
struct Range {
    lo: f64,
    hi: f64,
}

impl Range {
    fn sample(&self, rng: &mut StdRng) -> f64 {
        if !(self.lo.is_finite() && self.hi.is_finite()) || self.hi <= self.lo {
            return self.lo;
        }
        self.lo + (self.hi - self.lo) * rng.gen::<f64>()
    }
}

#[derive(Debug, Deserialize, Clone)]
struct BacktestSummary {
    markets: i64,
    pnl_total: f64,
    win_rate: f64,
    trades_total: i64,
}

#[derive(Debug, Clone)]
struct Trial {
    env: BTreeMap<String, String>,
    summary: BacktestSummary,
    score: f64,
}

fn exe_name(stem: &str) -> String {
    if cfg!(windows) {
        format!("{stem}.exe")
    } else {
        stem.to_string()
    }
}

fn build_backtest_if_missing(backtest_exe: &Path) -> Result<()> {
    if backtest_exe.exists() {
        return Ok(());
    }
    let prof = backtest_exe
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|s| s.to_str())
        .unwrap_or("");
    let release = prof.eq_ignore_ascii_case("release");
    let mut cmd = Command::new("cargo");
    cmd.arg("build");
    if release {
        cmd.arg("--release");
    }
    cmd.args(["--bin", "backtest"]);
    let st = cmd.status().context("building backtest binary")?;
    if !st.success() {
        anyhow::bail!("failed to build backtest (exit={})", st);
    }
    if !backtest_exe.exists() {
        anyhow::bail!(
            "backtest binary still missing at {}",
            backtest_exe.display()
        );
    }
    Ok(())
}

fn backtest_exe_path() -> Result<PathBuf> {
    let me = std::env::current_exe().context("current_exe")?;
    let dir = me
        .parent()
        .context("current_exe has no parent directory")?;
    Ok(dir.join(exe_name("backtest")))
}

fn parse_range_arg(s: &str) -> Result<(String, Range)> {
    // NAME=lo:hi
    let (name, rhs) = s
        .split_once('=')
        .with_context(|| format!("invalid --range {s:?} (expected NAME=lo:hi)"))?;
    let (lo, hi) = rhs
        .split_once(':')
        .with_context(|| format!("invalid --range {s:?} (expected NAME=lo:hi)"))?;
    let lo: f64 = lo
        .trim()
        .parse()
        .with_context(|| format!("invalid lo in --range {s:?}"))?;
    let hi: f64 = hi
        .trim()
        .parse()
        .with_context(|| format!("invalid hi in --range {s:?}"))?;
    Ok((
        name.trim().to_string(),
        Range {
            lo: lo.min(hi),
            hi: lo.max(hi),
        },
    ))
}

fn default_ranges() -> BTreeMap<String, Range> {
    // These are deliberately wide; tighten via `--range`.
    BTreeMap::from([
        ("PM_EPS0".to_string(), Range { lo: 0.001, hi: 0.030 }),
        ("PM_EPS1".to_string(), Range { lo: 0.005, hi: 0.080 }),
        ("PM_DELTA0".to_string(), Range { lo: 0.0, hi: 0.050 }),
        ("PM_DELTA1".to_string(), Range { lo: 0.0, hi: 0.080 }),
        (
            "PM_TAU0_SECONDS".to_string(),
            Range {
                lo: 120.0,
                hi: 3600.0,
            },
        ),
        ("PM_U0".to_string(), Range { lo: 10.0, hi: 400.0 }),
        ("PM_U_MIN".to_string(), Range { lo: 0.0, hi: 100.0 }),
        ("PM_BETA_SKEW".to_string(), Range { lo: 0.2, hi: 1.0 }),
        ("PM_SKEW_DEADZONE".to_string(), Range { lo: 0.0, hi: 0.05 }),
        (
            "PM_SKEW_TARGET_EXTRA_DELTA".to_string(),
            Range { lo: 0.0, hi: 0.05 },
        ),
        (
            "PM_STOP_NEW_SECONDS".to_string(),
            Range {
                lo: 5.0,
                hi: 180.0,
            },
        ),
        ("PM_PROB_ALPHA".to_string(), Range { lo: 0.05, hi: 0.50 }),
        ("PM_SIGMA_ALPHA".to_string(), Range { lo: 0.05, hi: 0.50 }),
        ("PM_TOX_RHO".to_string(), Range { lo: 0.00, hi: 0.50 }),
        (
            "PM_TOX_LOOKAHEAD_SECONDS".to_string(),
            Range {
                lo: 1.0,
                hi: 30.0,
            },
        ),
    ])
}

fn fmt_env_value(name: &str, v: f64) -> String {
    // Keep readable formatting for `.env`.
    let decimals = match name {
        "PM_TAU0_SECONDS" | "PM_STOP_NEW_SECONDS" | "PM_TOX_LOOKAHEAD_SECONDS" => 1,
        _ => 6,
    };
    format!("{v:.decimals$}")
}

fn run_backtest_json(
    backtest_exe: &Path,
    base_dir: Option<&str>,
    extra_env: &BTreeMap<String, String>,
    limit_markets: Option<usize>,
) -> Result<BacktestSummary> {
    let mut cmd = Command::new(backtest_exe);
    cmd.arg("--summary-json");
    cmd.arg("--no-plots");
    if let Some(n) = limit_markets {
        cmd.arg("--limit-markets");
        cmd.arg(n.to_string());
    }

    if let Some(base) = base_dir {
        cmd.env("PK8GA_MARKETS_DIR", base);
    }

    for (k, v) in extra_env {
        cmd.env(k, v);
    }

    // Be defensive: suppress any tracing noise in case something logs.
    cmd.env("RUST_LOG", "error");

    let out = cmd.output().with_context(|| format!("running {}", backtest_exe.display()))?;
    if !out.status.success() {
        anyhow::bail!(
            "backtest failed (exit={}):\nstdout:\n{}\nstderr:\n{}",
            out.status,
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
    }
    let s = String::from_utf8(out.stdout).context("backtest stdout not utf8")?;
    let line = s
        .lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("")
        .trim();
    if line.is_empty() {
        anyhow::bail!("backtest produced no JSON output");
    }
    let summary: BacktestSummary =
        serde_json::from_str(line).context("parsing backtest JSON summary")?;
    Ok(summary)
}

fn main() -> Result<()> {
    let _ = dotenvy::dotenv();

    let mut trials: usize = 50;
    let mut seed: u64 = 1;
    let mut base_dir: Option<String> = None;
    let mut out_path: Option<PathBuf> = None;
    let mut limit_markets: Option<usize> = None;

    let mut w_pnl: f64 = 1.0;
    let mut w_win: f64 = 10.0;
    let mut w_trades: f64 = 0.10;
    let mut min_trades: Option<i64> = None;
    let mut min_win_rate: Option<f64> = None;
    let mut force_paper: bool = true;

    let mut ranges = default_ranges();

    let args: Vec<String> = std::env::args().collect();
    let mut i = 1usize;
    while i < args.len() {
        let a = args[i].as_str();
        match a {
            "--help" | "-h" => {
                println!("Usage:");
                println!("  optimizer [FLAGS]");
                println!();
                println!("Flags:");
                println!("  --trials N");
                println!("  --seed N");
                println!("  --base DIR                 (sets PK8GA_MARKETS_DIR for backtest runs)");
                println!("  --limit-markets N          (run only first N markets)");
                println!("  --out FILE.jsonl           (append per-trial JSON lines)");
                println!("  --paper 0|1                (default 1)");
                println!();
                println!("Objective:");
                println!("  --w-pnl X                  (default 1.0)");
                println!("  --w-win X                  (default 10.0, win_rate in [0,1])");
                println!("  --w-trades X               (default 0.10)");
                println!("  --min-trades N             (optional constraint)");
                println!("  --min-win-rate X           (optional constraint, percent 0..100)");
                println!();
                println!("Search space:");
                println!("  --range NAME=lo:hi         (repeatable; overrides default ranges)");
                return Ok(());
            }
            "--trials" => {
                i += 1;
                trials = args
                    .get(i)
                    .context("--trials requires a value")?
                    .parse()
                    .context("invalid --trials")?;
            }
            "--seed" => {
                i += 1;
                seed = args
                    .get(i)
                    .context("--seed requires a value")?
                    .parse()
                    .context("invalid --seed")?;
            }
            "--base" => {
                i += 1;
                base_dir = Some(
                    args.get(i)
                        .context("--base requires a value")?
                        .to_string(),
                );
            }
            "--out" => {
                i += 1;
                out_path = Some(PathBuf::from(
                    args.get(i).context("--out requires a value")?,
                ));
            }
            "--limit-markets" | "--max-markets" => {
                i += 1;
                limit_markets = Some(
                    args.get(i)
                        .context("--limit-markets requires a value")?
                        .parse()
                        .context("invalid --limit-markets")?,
                );
            }
            "--paper" => {
                i += 1;
                let v = args.get(i).context("--paper requires 0|1")?;
                force_paper = v != "0" && !v.eq_ignore_ascii_case("false");
            }
            "--w-pnl" => {
                i += 1;
                w_pnl = args
                    .get(i)
                    .context("--w-pnl requires a value")?
                    .parse()
                    .context("invalid --w-pnl")?;
            }
            "--w-win" => {
                i += 1;
                w_win = args
                    .get(i)
                    .context("--w-win requires a value")?
                    .parse()
                    .context("invalid --w-win")?;
            }
            "--w-trades" => {
                i += 1;
                w_trades = args
                    .get(i)
                    .context("--w-trades requires a value")?
                    .parse()
                    .context("invalid --w-trades")?;
            }
            "--min-trades" => {
                i += 1;
                min_trades = Some(
                    args.get(i)
                        .context("--min-trades requires a value")?
                        .parse()
                        .context("invalid --min-trades")?,
                );
            }
            "--min-win-rate" => {
                i += 1;
                min_win_rate = Some(
                    args.get(i)
                        .context("--min-win-rate requires a value")?
                        .parse()
                        .context("invalid --min-win-rate")?,
                );
            }
            "--range" => {
                i += 1;
                let raw = args.get(i).context("--range requires NAME=lo:hi")?;
                let (name, r) = parse_range_arg(raw)?;
                ranges.insert(name, r);
            }
            _ if a.starts_with("--range=") => {
                let raw = a.splitn(2, '=').nth(1).unwrap_or("");
                let (name, r) = parse_range_arg(raw)?;
                ranges.insert(name, r);
            }
            _ => anyhow::bail!("unknown flag: {a}"),
        }
        i += 1;
    }

    let backtest_exe = backtest_exe_path()?;
    build_backtest_if_missing(&backtest_exe)?;

    let mut rng = StdRng::seed_from_u64(seed);
    let mut best: Option<Trial> = None;
    let mut all: Vec<Trial> = Vec::with_capacity(trials);

    let mut out_file = if let Some(p) = &out_path {
        let f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(p)
            .with_context(|| format!("open {}", p.display()))?;
        Some(f)
    } else {
        None
    };

    for t in 0..trials {
        let mut env = BTreeMap::<String, String>::new();
        if force_paper {
            env.insert("PM_PAPER_TRADING".to_string(), "1".to_string());
        }

        for (k, r) in &ranges {
            env.insert(k.clone(), fmt_env_value(k, r.sample(&mut rng)));
        }

        let summary = run_backtest_json(
            &backtest_exe,
            base_dir.as_deref(),
            &env,
            limit_markets,
        )?;

        if let Some(min) = min_trades {
            if summary.trades_total < min {
                continue;
            }
        }
        if let Some(min) = min_win_rate {
            if summary.win_rate + 1e-9 < min {
                continue;
            }
        }

        let score = w_pnl * summary.pnl_total
    + w_win * (summary.win_rate / 100.0)
    + w_trades * (summary.trades_total as f64).ln();
    
        let trial = Trial {
            env,
            summary,
            score,
        };

        if let Some(ref mut f) = out_file {
            let obj = serde_json::json!({
                "trial": t,
                "score": trial.score,
                "summary": {
                    "markets": trial.summary.markets,
                    "pnl_total": trial.summary.pnl_total,
                    "win_rate": trial.summary.win_rate,
                    "trades_total": trial.summary.trades_total,
                },
                "env": trial.env,
            });
            writeln!(f, "{}", obj.to_string()).context("write --out")?;
        }

        if best.as_ref().is_none_or(|b| trial.score > b.score) {
            best = Some(trial.clone());
        }
        all.push(trial);

        if (t + 1) % 10 == 0 || t + 1 == trials {
            let b = best.as_ref().unwrap();
            eprintln!(
                "[{}/{}] best score={:.4} pnl={:.4} win={:.2}% trades={} (markets={})",
                t + 1,
                trials,
                b.score,
                b.summary.pnl_total,
                b.summary.win_rate,
                b.summary.trades_total,
                b.summary.markets
            );
        }
    }

    all.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
    let top_n = all.len().min(10);

    println!("Top {top_n} trials:");
    for (idx, tr) in all.iter().take(top_n).enumerate() {
        println!(
            "{:>2}. score={:.4} pnl={:.4} win={:.2}% trades={}",
            idx + 1,
            tr.score,
            tr.summary.pnl_total,
            tr.summary.win_rate,
            tr.summary.trades_total
        );
    }

    let Some(best) = all.first() else {
        anyhow::bail!("no feasible trials (all filtered out?)");
    };

    println!();
    println!("Best params (.env overrides):");
    for (k, v) in &best.env {
        if k == "PM_PAPER_TRADING" {
            continue;
        }
        println!("{k}={v}");
    }

    Ok(())
}
