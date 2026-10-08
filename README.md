# SokoPay Platform

A licensed payments platform for Ghana, built to grow across Africa.

This repository holds the **new** SokoPay build (started October 2026). It replaces the
2020–2024 gateway in `../repo-master`, which has been reviewed and retired for security
reasons (see `../docs/11-Codebase-Review-2020-2024.md`). Do not copy code from the old
repository into this one.

---

## What SokoPay is (for everyone)

SokoPay lets people and businesses in Ghana move money simply and safely:

- **Customers** pay bills, buy airtime and data, and pay shops from their phone.
- **Merchants** collect payments by any mobile money network or card, and receive the
  money in their account the next morning.
- **Agents** (later phase) help people put cash into, and take cash out of, the system.
- **Admins** (SokoPay staff) run onboarding, approvals, settlements and compliance.

## The one big idea: build everything, switch on per licence

Ghana's central bank (Bank of Ghana, "BoG") issues payment licences in tiers. Each tier
permits more activities than the one below it:

| Licence | What it adds |
|---|---|
| **PSP Standard** | A payment app that connects to a bigger licensed partner. Pay bills and shops; the partner carries the payment risk. *(SokoPay starts here.)* |
| **PSP Medium** | Sign up merchants, aggregate payments, deploy POS, manage billers. |
| **PSP Enhanced** | Process payments on our own rails, run a payment gateway, inward remittance. |
| **DEMI** (Dedicated E-Money Issuer) | Hold customer money in a wallet, person-to-person transfers, cash-in/cash-out, an agent network. |

We **build the software for all of these now**, but every feature is locked behind a
**licence gate**. A feature only works when the licence that legally permits it is active.
So we ship the wallet, the agent app and cross-border features in the code from day one —
switched off — and turn each on the day BoG grants the matching licence. No rebuilds.

See [`docs/LICENCE-CAPABILITY-MATRIX.md`](docs/LICENCE-CAPABILITY-MATRIX.md) for the exact
mapping, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how it is enforced in code.

## The parts

| Part | Technology | Folder |
|---|---|---|
| Backend API + core engine | Django, Django REST Framework, Python 3.12 | `backend/` |
| Merchant portal (web) | Django + HTMX + Alpine.js + Tailwind | `backend/` (server-rendered) |
| Admin portal (web) | Django + HTMX + Alpine.js + Tailwind | `backend/` (server-rendered) |
| Customer app (iOS/Android) | Flutter | `mobile/customer/` *(later milestone)* |
| Agent app (iOS/Android) | Flutter | `mobile/agent/` *(later milestone)* |
| Database | PostgreSQL 16 | — |
| Cache / queues | Redis 7 | — |

## The money model, in one line

Every movement of money is a **balanced double-entry journal** in an **append-only ledger**,
recorded in the smallest currency unit (pesewas) as whole numbers. Balances are *calculated*
from the ledger, never edited by hand. This is the single most important rule in the codebase.

---

## Getting started (developers)

Prerequisites: Python 3.12, PostgreSQL 16, Redis 7 (or Docker for the last two).

```bash
# 1. Start Postgres + Redis
docker compose up -d

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env
python manage.py migrate
python manage.py runserver
```

Run the tests (the ledger and licence-gate tests are the ones that matter most):

```bash
cd backend
pytest
```

> **Note on the database:** the ledger relies on row-level locking (`SELECT … FOR UPDATE`)
> to stay correct under concurrent requests. That works on PostgreSQL, not SQLite. Always
> run the money-related tests against PostgreSQL.

## Repository layout

```
sokopay-platform/
  README.md                 ← you are here
  docker-compose.yml        ← Postgres + Redis for local dev
  docs/                     ← architecture, standards, licence matrix
  backend/
    config/                 ← Django settings (base/dev/prod), URLs, WSGI/ASGI
    apps/
      common/               ← shared base models, the Money helper
      accounts/             ← users, roles (custom user model from day one)
      licensing/            ← the licence-capability gate (build-all, roll-out-per-licence)
      ledger/               ← the double-entry ledger (the core)
    tests/                  ← cross-app tests
  mobile/                   ← Flutter apps (later milestone)
```

## Status

Backend (Django): built and tested (61 tests, ruff + bandit clean).
- `common`, `accounts` (custom user) — foundations
- `licensing` — the licence-capability gate
- `ledger` — double-entry ledger
- `rails` — RailProvider interface + mock + Korba/Nsano stubs
- `payments` — bill pay, airtime/data, secure webhooks
- `merchants` — KYB onboarding, checkout, settlement (maker-checker), merchant API
- `portal` — merchant + admin HTMX portals (login, dashboards, review & approval queues)
- `accounts` auth — consumer phone+OTP+PIN sign-in with JWT for the mobile apps

Mobile (Flutter): `mobile/customer/` scaffolded — auth flow, home, bill-pay. Needs the
Flutter SDK to build (see mobile/README.md). `mobile/agent/` (DEMI) is a later milestone.

Next: real SMS, the Flutter agent app, and turning the Korba/Nsano stubs into real
integrations once partner API docs are available.
