# Market data

The bundled snapshot contains five years of real daily observations for the two public market series used in the project:

| Column | Series | Unit | Provider |
|---|---|---|---|
| `brent` | Europe Brent Spot Price FOB (`RBRTE.D`, published as FRED `DCOILBRENTEU`) | USD/barrel | U.S. Energy Information Administration |
| `gasoil` | New York Harbor Ultra-Low Sulfur No. 2 Diesel Spot Price (`EER_EPD2DXL0_PF4_Y35NY_DPG.D`, published as FRED `DDFUELNYH`) | USD/gallon | U.S. Energy Information Administration |
| `bitume` (separate file) | Asphalt and other petroleum/coal products PPI (`WPU058`) | Index Dec 1984=100, monthly | U.S. Bureau of Labor Statistics, mirrored by FRED |

The raw CSV files are public EIA observations downloaded from FRED. The normalized file used by the application is `data/processed/market_prices.csv`

Bitume uses `data/processed/bitumen_index.csv`, with 60 actual monthly observations from September 2021 to August 2026. Its raw source is `data/raw/FRED_WPU058.csv`, downloaded on October 8, 2026. Run `scripts/prepare_market_data.py` to rebuild both files; `--refresh` fetches new official data. Monthly values are never interpolated into daily observations or converted to USD/tonne. The database imports this history into `market_benchmarks` idempotently, including on existing installations.


Raw files:

- `data/raw/FRED_DCOILBRENTEU.csv`
- `data/raw/FRED_DDFUELNYH.csv`
- `data/raw/FRED_WPU058.csv`

Primary source references:

- [FRED: Brent Europe, DCOILBRENTEU](https://fred.stlouisfed.org/series/DCOILBRENTEU)
- [FRED: New York Harbor diesel, DDFUELNYH](https://fred.stlouisfed.org/series/DDFUELNYH)
- [EIA Petroleum data](https://www.eia.gov/petroleum/data.php)
- [BLS/FRED: monthly asphalt index, WPU058](https://fred.stlouisfed.org/series/WPU058)
