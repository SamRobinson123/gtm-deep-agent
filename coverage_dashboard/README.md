# Coverage Curve Analysis

A weekly-cadence analytical tool that tracks **pipe coverage vs. quarterly targets** across regions, products, and deal-types — and charts how coverage evolves week by week, comparing the current quarter against prior quarters at the same point in their lifecycle.

Sibling project to `gtm-weekly-reporting`. That tool reports *this week's* coverage; this tool's distinguishing job is the **historic dimension**.

## What it does

Every Monday, the analyst runs `run_weekly_coverage.py`. The tool:

1. Pulls the latest opportunity snapshot from Azure Synapse.
2. Computes pipe coverage (`pipeline $ / target $`) for every slice — region, product, deal-type, and their drill-downs.
3. Appends the result to a local coverage time-series (`data/coverage_history.parquet`).
4. Renders a self-contained HTML dashboard with the current week's table plus historic coverage curves.

The dashboard is a single HTML file. It opens in any browser. No server, no Docker, no build step.

## The question it answers

> For each slice of the business, how has coverage of the quarterly target evolved week by week — within the current quarter and compared to prior quarters at the same lifecycle point?

## Quick Start

```powershell
# Install dependencies (one time)
uv sync

# Configure Synapse credentials
Copy-Item .env.example .env
# Edit .env with your Synapse connection details

# Run the weekly report
uv run python -m src.run_weekly_coverage

# Open the rendered dashboard
output\coverage_dashboard.html
```

## Methodology

[`planning/PLAN.md`](planning/PLAN.md) is the single reference for the project — spec, design/decision history, AND how every number is produced: the two Synapse queries with line-by-line include/exclude rules (§3.3/§3.3a/§3.5a), how open pipe and bookings are derived (§6), the coverage formula (§7), and the needed-coverage / median-recommended-coverage math (§11a). The raw SQL lives in `backend/sql/`. (The separate `METHODOLOGY.md`/`.docx` and `planning/NEEDED_PIPELINE_METHODOLOGY.md` were consolidated into PLAN.md on 2026-06-07.)

## Project Status

Live. See [`planning/PLAN.md`](planning/PLAN.md) for the full spec, including open questions.

## Project Structure

```
coverage-curve-analysis/
├── src/         # Pipeline: pull → compute → store → render
├── templates/   # HTML dashboard template
├── data/        # Coverage history parquet (gitignored)
├── output/      # Rendered dashboards (gitignored)
├── planning/    # Spec and documentation
└── tests/       # pytest
```

## License

See [LICENSE](LICENSE).
