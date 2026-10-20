# Krein Property Management

Web application for managing Dutch residential rentals: homes, tenants and co-tenants, digitally signed tenancy agreements, monthly invoices, payment matching from bank statements, arrears, repairs and messages, with separate portals for staff, tenants, owners and contractors. Dutch is the main language; English is available in every screen.

## Features

- **Tenants and households**: tenants with co-tenants (each with their own login and share of the rent), portal invitations.
- **Contracts**: Dutch tenancy agreements from templates, edited in the browser, signed online by every tenant and approved by staff (PDF with signatures).
- **Invoicing**: monthly invoices (pro rata for partial months, per-person shares), optional review before sending, PDF and email delivery.
- **Payments**: "I have paid" notices and receipts, payment verification, MT940 / CAMT.053 bank-statement import with automatic matching, optional automatic bank feed through Ponto.
- **Arrears**: overdue overview, reminders on day 1 and day 8, recorded follow-up steps and payment plans.
- **Repairs and tickets**: tenant reports with photos and video, priorities and deadlines, contractor work orders, completion with photo evidence.
- **Messages**: inbox for conversations with tenants.
- **Also**: deposits, service-charge settlements, rent indexation, inspections, owners and owner statements, documents, tasks, automation rules, templates, audit log, CSV exports.
- **Platform**: a developer console (`/developer`) to manage administrators and switch features on or off per role.

## Portals

| Portal | Path | For |
|---|---|---|
| Staff | `/` | administrators, managers, finance, property staff, inspectors |
| Tenant | `/tenant` | tenants and co-tenants |
| Owner | `/owner` | property owners (read only) |
| Landlord | `/landlord` | owners who manage their own units |
| Contractor | `/contractor` | contractors with work orders |
| Developer | `/developer` | platform administration |

## Tech stack

Python 3.12 · FastAPI · SQLite (WAL, append-only financial records) · plain HTML, CSS and JavaScript (no build step) · ReportLab for PDFs · Docker.

## Project structure

```
app/            FastAPI application: API routes, database, migrations, PDFs, email, bank import, security
static/         The web interface (HTML, CSS, JavaScript), Dutch translations, icons and fonts
tests/          Automated tests (unittest), each on its own temporary database
scripts/        Start-up for hosted deployments, sample-data builder, demo tools
sample-data/    Sample database for demos (made by scripts/make_sample_db.py, no real personal data)
docs/           User guide (Dutch PDF), feature notes, privacy notes, scope
manage.py       Command line: demo data, create a developer, backup
Dockerfile      Container image
render.yaml     Render blueprint for a demo deployment
```

Runtime data (database, uploaded files, backups) lives in `data/` and is never committed.

## Getting started (local)

```powershell
python -m pip install -r requirements.txt
copy .env.example .env                     # then fill in the settings you need
python manage.py create-developer          # your own login, signs in at /developer
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/developer, create an administrator, and sign in with it at http://127.0.0.1:8000.

**Demo data instead:** `python manage.py demo` fills an empty database with fictional data (logins `admin@haven.local`, `tenant@haven.local`, … with the password `HavenDemo!2026`). To use the richer sample database, copy `sample-data/haven.db` and `sample-data/uploads` into `data/`.

## Configuration

Settings are read from environment variables (or `.env` for local use). See [.env.example](.env.example) for the full list.

| Setting | Purpose |
|---|---|
| `HAVEN_PUBLIC_URL` | The public address, used in emails and links |
| `HAVEN_DATA_DIR` | Where the database and uploaded files are stored (default `data/`) |
| `HAVEN_SMTP_*` | Outgoing email (invoices, reminders, invitations) |
| `HAVEN_TIMEZONE` | For example `Europe/Amsterdam` |
| `HAVEN_SECURE_COOKIES` | `1` when served over HTTPS |
| `HAVEN_PONTO_*` | Optional automatic bank feed |
| `HAVEN_BOOTSTRAP_DEVELOPER_EMAIL` / `_PASSWORD` | First developer login on a new hosted deployment |
| `HAVEN_SAMPLE_DATA` / `HAVEN_DEMO_PASSWORD` | Demo deployments only: load the sample data, with your own password |

Never commit `.env`: it holds passwords and keys.

## Tests

```powershell
python -m unittest discover -s tests
```

## Backup

```powershell
python manage.py backup
```

Creates a ZIP with a consistent database snapshot and the uploaded files. Backups contain personal data: store them privately.

## Deployment

- **Production**: Docker on a server with a persistent disk (planned: Google Cloud, region europe-west4), HTTPS through a reverse proxy, nightly backups. Start with an empty database: set `HAVEN_BOOTSTRAP_DEVELOPER_EMAIL` and `HAVEN_BOOTSTRAP_DEVELOPER_PASSWORD`; never set `HAVEN_SAMPLE_DATA` or `HAVEN_DEMO_SEED`.
- **Demo**: [render.yaml](render.yaml) deploys the sample data to Render. The free plan resets the data whenever the service restarts; see the notes in the file.

## Documentation

- [Gebruikershandleiding (PDF, Dutch)](docs/Krein-Gebruikershandleiding.pdf): user guide for staff and tenants
- [Feature notes](docs/FEATURES.md): how each part works, settings and limits
- [Privacy notes](docs/PRIVACY.md) · [Scope](docs/SCOPE.md) · [Implementation checklist](docs/IMPLEMENTATION.md)

## Legal note

Krein supports Dutch rental administration but does not certify legal compliance. Contract templates, rent rules, deposit handling and service-charge allocation should be reviewed by a legal professional before use.
