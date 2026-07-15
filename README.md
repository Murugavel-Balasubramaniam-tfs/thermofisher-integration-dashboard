# Integration Test Data Dashboard Backend

PostgreSQL-backed API for the Integration Test Data dashboard.

## Setup

```bash
cd /Users/murugavel.b/Library/CloudStorage/OneDrive-ThermoFisherScientific/Documents/Codex/integration-dashboard-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Update `.env` with the dashboard's private PostgreSQL connection string. As in
the `fscom-cart` Spring backend, `SPRING_PROFILES_ACTIVE` selects the runtime
profile. The dashboard loads `.env` first and then profile overrides from
`.env.<SPRING_PROFILES_ACTIVE>`; environment variables injected by the
shell, container, or deployment take highest priority.

For example, a QA deployment can use an untracked `.env.qa` file:

```env
SPRING_PROFILES_ACTIVE=qa
FSCOM_CART_DATABASE_PASSWORD=provided-by-secret-manager
```

The Cart connection follows the backend's profile mappings for `local-dev`,
`local-qa`, `dev`, `qa`, `uat`, `prod`, `prod_cloud`, `dev_eu`, `qa_eu`,
`uat_eu`, `prod_eu`, and `prod_cloud_eu`. Supply its password through
`FSCOM_CART_DATABASE_PASSWORD`, `SPRING_DATASOURCE_PASSWORD`, or a mounted secret
file. `FSCOM_CART_DATABASE_URL` / `SPRING_DATASOURCE_URL` can override the profile
mapping. Do not commit `.env` or profile files.

The non-secret datasource URLs, usernames, schemas, pool settings, and config-tree
locations are defined in `application.yml`, using the same multi-document profile
structure as `fscom-cart`. `server.py` reads those profile documents at startup;
password values remain runtime-only.

## Container deployment

`pipeline.yaml` mirrors the `fscom-cart` deployment pattern in the `fscart`
namespace. It mounts the existing `fscart-secret` read-only at
`/etc/secrets/fscart-secret` and the Cart backend ConfigMap at
`/etc/configmap/fishersci-cart-backend-config`. QA and Dev select their matching
Spring profiles and read the datasource password from the mounted Config Tree
file. The Docker image does not contain `.env` or any secret value.

The Titan v2 workflow builds with Thermo Fisher's internal Python 3.10 image and
deploys QA from `release/*` branches or Dev from `feature/*` branches. The app is
served behind `/integration-dashboard`; health checks use
`/integration-dashboard/api/health`.

If `.env` is not configured yet, the app still runs in local development mode using
`data/scripts.json`. Add `DATABASE_URL` and install `requirements.txt` when you are
ready to persist into PostgreSQL.

## PostgreSQL

Create a database and user if needed:

```sql
CREATE DATABASE integration_dashboard;
CREATE USER dashboard_user WITH PASSWORD 'dashboard_password';
GRANT ALL PRIVILEGES ON DATABASE integration_dashboard TO dashboard_user;
```

The app creates this table automatically:

```sql
CREATE TABLE IF NOT EXISTS integration_dashboard_scripts (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Run

```bash
bash start.sh
```

Open:

```text
http://127.0.0.1:8000/
```

If the browser says the site cannot be reached, check whether the server is
running:

```bash
bash check_server.sh
```

## API

- `GET /api/health`
- `GET /api/scripts`
- `GET /api/database/test` (read-only connection check and 10-row table preview)
- `GET /api/cart-database/test` (read-only fscom-cart `ORDERITEMS` preview)
- `PUT /api/scripts`

When the dashboard uploads an edited CSV, it refreshes the browser view and saves the updated rows back to PostgreSQL.

Use the **Test connection** button on the dashboard to connect with `DATABASE_URL`,
or to the selected `fscom-cart` profile, and display up to 10 recent rows. The
Cart query starts a read-only transaction and returns only part number, quantity,
UOM, status, and update time—no customer, account, order, or user identifiers.
API responses never include a connection string or database password.
