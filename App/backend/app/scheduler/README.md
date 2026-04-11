# `app.scheduler` — Proactive engagement + freshness (Phase 20)

> **Status:** scaffolded — all runtime logic lands in Phase 20.

## Target responsibilities

1. **Deadline reminders** — durable timers that notify users
   before their filing deadlines (VAT monthly, PAYE monthly,
   provisional tax, final returns).  Temporal.io workflows so
   restart/retry semantics are correct even on long intervals.
2. **Freshness pipeline** — watch URA publication sources
   (CMS / RSS / scraper) for new circulars, re-embed, upsert
   to Qdrant + KG, emit a drift alert if the index snapshot
   would materially change what's returned.
3. **Notification dispatch** — multi-channel fan-out via
   PWA Push (service worker) → email (Resend/SES) → SMS
   (Africa's Talking) with per-user channel preferences.

## Target architecture (Phase 20)

```
scheduler/
├── __init__.py
├── workflows/                 # Temporal workflow definitions
│   ├── deadline_reminder.py
│   ├── freshness_watch.py
│   └── notification_dispatch.py
├── channels/                  # Notification transports
│   ├── pwa_push.py
│   ├── email.py
│   └── sms.py
├── ingest/
│   ├── cdc_consumer.py        # Debezium → Kafka consumer
│   ├── nightly_diff.py        # fallback scraper
│   └── drift_alert.py
└── tests/
```

## Dependencies

- Phase 14 auth + user profile (for preferences)
- Phase 16 memory (for user context in notification copy)
- Temporal.io cluster
- Africa's Talking + Resend accounts with DPAs

## Effort

3 weeks.
