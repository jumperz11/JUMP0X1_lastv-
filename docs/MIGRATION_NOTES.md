# Migration Notes - Repository Restructure (2025-12-25)

## Overview
The repository was reorganized from a flat structure to a professional, modular layout.

## New Directory Structure
```
JUMP0X1/
├── .env                    # Configuration (gitignored)
├── .gitignore              # Comprehensive ignore rules
├── README.md               # Project documentation
├── VERSION                 # Version file
├── CLAUDE.md               # AI assistant context
│
├── run_paper.py            # Entry point: Paper trading
├── run_live.py             # Entry point: Live trading
├── RUN_PAPER.bat           # Windows: Paper trading
├── RUN_LIVE.bat            # Windows: Live trading
├── RUN_VERIFY.bat          # Windows: Pre-live checks
│
├── src/                    # Source code
│   ├── core/               # Core trading modules
│   │   ├── trade_executor.py
│   │   ├── polymarket_connector.py
│   │   └── real_trade_logger.py
│   └── ui/                 # User interface
│       └── ui_dashboard_live.py
│
├── scripts/                # Utility scripts
│   └── verify_pre_live.py
│
├── experiments/            # Backtest experiments
│   ├── backtest_adversarial.py
│   ├── backtest_alpha_test.py
│   ├── backtest_core_timing.py
│   ├── backtest_cross_market.py
│   ├── backtest_frequency_variants.py
│   ├── backtest_market_classification.py
│   ├── backtest_regime_stress.py
│   └── backtest_window_shift.py
│
├── docs/                   # Documentation
│   ├── GO_LIVE_CHECKLIST.md
│   ├── PHASE1_LOCKED.md
│   ├── MIGRATION_NOTES.md  # This file
│   └── (other docs from previous structure)
│
├── archive/                # Archived/deprecated files
│   ├── assets/             # Screenshots, images
│   ├── generate_validation_dashboard.py
│   ├── build_dashboard_pipeline.py
│   ├── build_real_dashboard.py
│   └── (old batch files)
│
├── logs/                   # Trading logs (gitignored)
│   ├── paper/
│   └── real/
│
└── PK8_PH/                 # Rust implementation (unchanged)
```

## What Moved Where

### Core Runtime → `src/core/`
| Old Location | New Location |
|--------------|--------------|
| `trade_executor.py` | `src/core/trade_executor.py` |
| `polymarket_connector.py` | `src/core/polymarket_connector.py` |
| `real_trade_logger.py` | `src/core/real_trade_logger.py` |

### Dashboard → `src/ui/`
| Old Location | New Location |
|--------------|--------------|
| `ui_dashboard_live.py` | `src/ui/ui_dashboard_live.py` |

### Backtest Files → `experiments/`
| Old Location | New Location |
|--------------|--------------|
| `backtest_*.py` | `experiments/backtest_*.py` |

### Scripts → `scripts/`
| Old Location | New Location |
|--------------|--------------|
| `verify_pre_live.py` | `scripts/verify_pre_live.py` |

### Documentation → `docs/`
| Old Location | New Location |
|--------------|--------------|
| `GO_LIVE_CHECKLIST.md` | `docs/GO_LIVE_CHECKLIST.md` |
| `PHASE1_LOCKED.md` | `docs/PHASE1_LOCKED.md` |

### Archived Files → `archive/`
| File | Reason |
|------|--------|
| `generate_validation_dashboard.py` | One-off utility |
| `build_dashboard_pipeline.py` | One-off utility |
| `build_real_dashboard.py` | One-off utility |
| `RUN_DASHBOARD.bat` | Replaced by RUN_PAPER.bat |
| `RUN_DEMO.bat` | Deprecated |
| `RUN_REAL_DEMO.bat` | Replaced by RUN_LIVE.bat |
| `screenshot.png`, `UI.jpg` | Assets |
| `markets_paper.7z` | Data archive |

## Import Changes

All imports were updated to use the new package structure:

```python
# Old
from trade_executor import TradeExecutor
from polymarket_connector import SessionManager
from real_trade_logger import init_real_logger

# New
from src.core.trade_executor import TradeExecutor
from src.core.polymarket_connector import SessionManager
from src.core.real_trade_logger import init_real_logger
```

## Path Changes

Log paths now use absolute paths relative to project root:
- Paper logs: `{project_root}/logs/paper/`
- Real logs: `{project_root}/logs/real/`

.env path now references project root from any script location.

## How to Run

### Paper Trading (Simulation)
```bash
python run_paper.py
# or
RUN_PAPER.bat  # Windows
```

### Live Trading (Real Orders)
```bash
python run_live.py
# or
RUN_LIVE.bat  # Windows
```

### Pre-Live Verification
```bash
python scripts/verify_pre_live.py
# or
RUN_VERIFY.bat  # Windows
```

## Breaking Changes

1. **Import paths changed** - Any external scripts importing from this project need updates
2. **Batch files renamed** - Old batch files archived, new ones created
3. **Log locations unchanged** - Logs still go to `logs/paper/` and `logs/real/`

## Notes

- Strategy logic unchanged - only structural refactoring
- All backtest results preserved in `backtest_full_logs/`
- PK8_PH Rust implementation unchanged
