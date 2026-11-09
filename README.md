# Taiwan ETF Holdings Tracker

A Streamlit app that scrapes daily constituent holdings for Taiwan-listed ETFs, diffs them against the last stored snapshot, and surfaces what the fund actually bought and sold — position weight changes, share count changes, and each holding's price move that day.

Covers ten ETFs, including the newer actively-managed products (00980A, 00981A, 00982A, 00991A, 00992A) whose holdings turn over far more than a passive index tracker's.

# What it does
Scrapes the current holdings table for a selected ETF from MoneyDJ
Reads the previously stored snapshot for that ETF from Google Sheets
Outer-joins the two so that newly added and fully exited positions both survive the merge
Fetches each holding's price change for the day from the FinMind API
Renders a sorted, colour-coded diff table

Snapshots are written back on demand, so today's holdings become tomorrow's comparison baseline.

# Design notes

Google Sheets as the datastore to avoid data vanish from Streamlit's setting. Sheets gives free, persistent, inspectable storage, with one worksheet per ETF created automatically on first save.

Red is up, green is down. This follows Taiwan and wider East Asian market convention, which is inverted relative to US platforms.

FinMind's free tier accepts one symbol per request, so prices are fetched in a loop with a 100 ms delay between calls rather than batched. The query window reaches back seven days so that a holiday or weekend still returns the most recent trading day rather than an empty result.

Outer join, then fill only the numeric columns. Filling the whole merged frame would replace missing security names with 0 and produce phantom rows; only the four numeric columns are filled, so a position that exists on one side and not the other is still identifiable by name.

Write access is password-gated in the sidebar, because the bulk-refresh button writes to the shared spreadsheet.

# Setup
bash
pip install -r requirements.txt
streamlit run etf2.py

Two secrets are required in .streamlit/secrets.toml (or, when deployed, in the Streamlit Cloud Settings → Secrets panel):

toml
gcp_json = '''{ ... Google service account JSON ... }'''
finmind_token = "optional; raises FinMind rate limits"
admin_password = "gates the bulk-refresh button"

The Google service account needs the Sheets and Drive scopes, and the target spreadsheet — named ETF_Holdings_Data — must be shared with the service account's email address.

# Limitations
Only one snapshot is retained per ETF. Saving clears the worksheet and overwrites it, so the comparison is always "now versus the last save," not a true daily time series. Building a real history would mean appending dated rows instead of overwriting.
The scraper depends on MoneyDJ's HTML structure. Any markup change on their side breaks extraction silently.
TLS verification is disabled on the scrape request, with urllib3 warnings suppressed. This works around a certificate issue on the source site but weakens transport security and should be revisited.
Price lookups scale linearly with the number of holdings because of the free-tier single-symbol limit; a large ETF takes a noticeable amount of time to refresh.
Holdings data is only as fresh as MoneyDJ's own update cycle, which lags the fund's actual trading.
