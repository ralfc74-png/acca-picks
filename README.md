# Acca Picks (iPhone web app)

A small web app that shows football accumulator **pick lists by market** (match result, double chance,
draw no bet, BTTS, over/under goals, corners). It works out its own probability estimates from free
football-data.co.uk results and odds, gives each pick a tier (S/A/B/C), and lets you build a slip with
combined odds and hit chance. Ronald-style: UK time, GBP, Bet365 prices where the free data has them.

**It never places bets and never logs in anywhere.** Estimates are not guarantees. See the disclaimer in the app footer.

## What is in the box

| Path | What it does |
|---|---|
| `build_data.py` | Downloads the data, runs the model, writes `docs/data.json` |
| `docs/` | The app itself (this is the folder GitHub Pages serves) |
| `.github/workflows/daily.yml` | Runs `build_data.py` every day at 06:00 UTC and commits the new `data.json` |
| `requirements.txt` | Just `numpy` |

## Try it on a computer first

```bash
pip install -r requirements.txt
python build_data.py            # takes about a minute, writes docs/data.json
cd docs && python -m http.server 8000
# open http://localhost:8000
```

## Put it online (GitHub Pages, free)

You need a free GitHub account. Do these once:

1. Go to github.com, click **New repository**. Name it e.g. `acca-picks`. Choose **Public** (free Pages needs a public repo on a free account) and create it.
   The app only holds public football data. Nothing personal is stored in it.
2. Upload everything from this folder (including the hidden `.github` folder) to the repository. Easiest: on the new repo page click
   **uploading an existing file**, drag the unzipped contents in and commit. Or with Git:
   `git init && git add . && git commit -m first && git branch -M main && git remote add origin https://github.com/YOURNAME/acca-picks.git && git push -u origin main`.
3. Turn on Pages: **Settings > Pages > Build and deployment > Source: Deploy from a branch > Branch: `main`, folder: `/docs` > Save**.
4. Allow the daily job to commit: **Settings > Actions > General > Workflow permissions > Read and write permissions > Save**.
5. Run the first update now: **Actions** tab > **Daily picks** > **Run workflow**. After about two minutes `docs/data.json` is refreshed.
6. Your app is at `https://YOURNAME.github.io/acca-picks/` (shown at the top of the Pages settings screen after a minute or so).

From then on it refreshes itself at 06:00 UTC daily. Note: GitHub pauses scheduled jobs on repos with no activity for 60 days.
If picks stop updating, open the Actions tab and click **Enable workflow**, or run it by hand.

Want fresher prices later in the day? Add another line under `schedule:` in `daily.yml`, e.g. `- cron: "30 11 * * *"`.

## Add it to your iPhone home screen

1. Open the app address in **Safari** (it must be Safari, not Chrome).
2. Tap the **Share** button (square with an arrow).
3. Scroll down and tap **Add to Home Screen**, then **Add**.
4. Open it from the new "Acca Picks" icon. It opens full screen like a normal app and keeps your slip between visits.

## How to use it

* **Market chips**: pick the market, you get a list sorted by chance of landing.
* **When / League / Min tier / Sides**: filter the list. "Best side per match" shows the likelier side only (e.g. Over *or* Under).
* Tap a pick to add it to your **slip** (one pick per match). Tap the green bar at the bottom to see combined odds, hit chance and a tiny stake idea.
* **Auto-build** makes a 4/6/8/12/20-fold from the top picks in the chosen market with your current filters.
* **Decimal / Fractional** button at the top switches odds format.

## What the numbers mean (and the limits)

* **Chance** = estimated probability. It blends a Poisson (Dixon-Coles-lite) model from team scoring/conceding rates and home advantage with the de-vigged bookmaker odds (60% market, 40% model) where odds exist. Corners are pure model.
* **Fair odds** = 1 / chance. Always an estimate, never a price you can bet at.
* **Bookie odds** come from football-data's `fixtures.csv` Bet365 columns. They exist only for **match result (1X2)** and **over/under 2.5 goals**.
  For double chance, draw no bet, BTTS, over/under 1.5 and corners there are **no bookmaker odds in the free data**: odds show as "none in data".
  Double chance / DNB edge is measured against a probability derived from the 1X2 prices, not a real Bet365 price.
* **Edge** = your estimate minus the de-vigged bookmaker chance, in percentage points. **EV** = expected profit per £1 at the Bet365 odds shown.
* **Tiers**: S = 80%+ with positive edge vs a real Bet365 price (so only 1X2 and over/under 2.5 can be S); A = 72%+; B = 63-72%; C = under 63%.
  Early in the season samples are small and tiers are capped at B/C for teams with few games.
* Prices in the data can be hours old. Check the live price (and Bet365 acca boost terms) before placing anything.
* `fixtures.csv` only lists the coming round or so of matches, so "Next 7 days" is often 3-4 days of games. Cup games and leagues outside the football-data list are not covered.
* Not modelled: line-ups, injuries, referees, weather, motivation.
* Combined acca chance multiplies the legs as if independent, which is optimistic for legs in the same competition/weather.

## Responsible gambling

Long accas almost always lose. Only stake what you can afford to lose. 18+.
[BeGambleAware](https://www.begambleaware.org) · [GAMSTOP](https://www.gamstop.co.uk) · National Gambling Helpline 0808 8020 133 (free, 24/7).

Data: [football-data.co.uk](https://www.football-data.co.uk). Free for personal use. Please be kind to their server (the job runs once a day).
