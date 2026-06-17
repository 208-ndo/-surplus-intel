# Surplus GA

Automated Georgia surplus funds lead scraper and dashboard for GitHub Actions + GitHub Pages.

## Counties

- Clayton County, Georgia
- DeKalb County, Georgia

## What It Does

- Downloads public surplus/excess funds PDFs directly.
- Parses owner, parcel, amount, sale date, and property address where available.
- Scores each lead from 0 to 100.
- Writes `data/surplus_leads.json`.
- Displays a dark GitHub Pages dashboard in `index.html`.
- Optionally pushes eligible leads to GoHighLevel when `GHL_API_KEY` is set.

## Local Run

```powershell
python -m pip install -r requirements.txt
python -m scraper.main
start .\index.html
```

## GitHub Actions

The scraper runs Monday, Wednesday, and Friday at 7am ET:

```yaml
0 11 * * 1,3,5
```

It can also be triggered manually from the Actions tab.

## GoHighLevel

Set this repository secret:

```text
GHL_API_KEY
```

Only leads with `score >= 60` and `surplus_amount >= 20000` are pushed.
