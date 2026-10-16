"""Synthetic company data for "Nimbus Labs", a fictional SaaS startup.

Everything here is invented for the hackathon (rule: public, synthetic or own data only).
The story is built so the graph has real questions to answer:
  * Why did Nimbus move its event bus from RabbitMQ to Kafka?  (decision chain + sources)
  * Who still knows how to rotate the auth signing keys?        (a key person left)
  * What breaks if the event bus goes down?                     (dependency impact)
"""

TEAMS = ["Platform", "Infra", "Payments", "Product", "Web", "Data"]

PEOPLE = [
    # id, name, role, team, status
    ("P1", "Aisha Rahman", "CTO", "Platform", "active"),
    ("P2", "Marco Silva", "Staff Engineer", "Platform", "active"),
    ("P3", "Priya Nair", "Backend Engineer", "Platform", "active"),
    ("P4", "Tom Becker", "SRE", "Infra", "active"),
    ("P5", "Lena Fischer", "Engineering Manager", "Payments", "active"),
    ("P6", "Ravi Menon", "Backend Engineer", "Payments", "active"),
    ("P7", "Chloe Dubois", "Product Manager", "Product", "active"),
    ("P8", "Jake Morgan", "Frontend Engineer", "Web", "active"),
    ("P9", "Sofia Alvarez", "Data Engineer", "Data", "active"),
    ("P10", "Omar Haddad", "Security Engineer", "Infra", "active"),
    ("P11", "Dana Whitfield", "Senior Engineer", "Platform", "left"),
    ("P12", "Ken Watanabe", "Designer", "Product", "active"),
]

# (system, owning team)
SYSTEMS = [
    ("auth-service", "Platform"),
    ("api-gateway", "Platform"),
    ("event-bus", "Platform"),
    ("notification-service", "Platform"),
    ("k8s-cluster", "Infra"),
    ("payments-service", "Payments"),
    ("billing-db", "Payments"),
    ("web-app", "Web"),
    ("data-warehouse", "Data"),
]

# (system, depends on system)
DEPENDS_ON = [
    ("web-app", "api-gateway"),
    ("api-gateway", "auth-service"),
    ("api-gateway", "k8s-cluster"),
    ("auth-service", "k8s-cluster"),
    ("event-bus", "k8s-cluster"),
    ("payments-service", "event-bus"),
    ("payments-service", "billing-db"),
    ("payments-service", "auth-service"),
    ("payments-service", "notification-service"),
    ("notification-service", "event-bus"),
    ("data-warehouse", "event-bus"),
]

# (name, status, lead person id, description, systems used)
PROJECTS = [
    ("Project Atlas", "active", "P5", "Checkout v2: faster, idempotent checkout flow.",
     ["payments-service", "web-app", "event-bus"]),
    ("Project Beacon", "active", "P3", "Notification revamp with per-user preferences and push.",
     ["notification-service", "event-bus"]),
    ("Project Citadel", "active", "P10", "Zero-trust security rollout (mTLS everywhere).",
     ["auth-service", "api-gateway"]),
    ("Project Delta", "active", "P9", "Analytics pipeline streaming events into the warehouse.",
     ["data-warehouse", "event-bus"]),
]

# id, title, kind, date, author person id, summary, about [(label, name)]
DOCUMENTS = [
    ("D1", "RFC-012: Move internal events to RabbitMQ", "RFC", "2025-03-10", "P2",
     "Proposes RabbitMQ as the event bus for service-to-service events. Chosen for simple operations "
     "and team familiarity. Expected load is under 500 msg/s.",
     [("System", "event-bus")]),
    ("D2", "Design Doc: Auth Service v2 token rotation", "Design Doc", "2025-06-02", "P11",
     "Introduces rotating refresh tokens with 15-minute access tokens. Dana owns the signing-key "
     "rotation procedure.",
     [("System", "auth-service")]),
    ("D3", "Runbook: JWT signing key rotation", "Runbook", "2025-08-14", "P11",
     "Manual step-by-step procedure to rotate JWT signing keys every 90 days. Requires break-glass "
     "access to the Kubernetes cluster.",
     [("System", "auth-service")]),
    ("D4", "Postmortem INC-2026-004: Duplicate payment events", "Postmortem", "2026-01-20", "P4",
     "RabbitMQ redelivered messages out of order during a failover, causing duplicate charge events "
     "for 312 customers. Root cause: no ordering or replay guarantees on the bus.",
     [("System", "event-bus"), ("System", "payments-service")]),
    ("D5", "RFC-019: Migrate the event bus to Kafka", "RFC", "2026-02-05", "P2",
     "Proposes Kafka for ordered partitions and replay. Supersedes RFC-012. Migration happens in two "
     "phases through April 2026.",
     [("System", "event-bus")]),
    ("D6", "Design Doc: Checkout v2", "Design Doc", "2026-03-01", "P5",
     "New checkout flow with idempotent payment events. Depends on the Kafka migration of the event bus.",
     [("Project", "Project Atlas"), ("System", "payments-service")]),
    ("D7", "Meeting notes: Beacon kickoff", "Meeting Notes", "2026-04-08", "P3",
     "Notification revamp: per-user preferences and push. Uses the event bus for triggers. Email "
     "provider switch discussed.",
     [("Project", "Project Beacon"), ("System", "notification-service")]),
    ("D8", "Security review: Zero-trust rollout plan", "Security Review", "2026-05-12", "P10",
     "mTLS between services, the gateway enforces JWT validation. Requires auth-service v2 tokens.",
     [("Project", "Project Citadel"), ("System", "api-gateway")]),
    ("D9", "Data Design: Analytics pipeline v1", "Design Doc", "2026-03-20", "P9",
     "Stream events from the event bus into the warehouse hourly. Columnar storage. Schema registry TBD.",
     [("Project", "Project Delta"), ("System", "data-warehouse")]),
    ("D10", "Runbook: Kubernetes cluster upgrades", "Runbook", "2025-11-03", "P4",
     "Quarterly upgrade checklist, node pool rotation and rollback steps.",
     [("System", "k8s-cluster")]),
    ("D11", "ADR: Billing DB stays on managed Postgres", "ADR", "2025-09-18", "P6",
     "Evaluated a distributed SQL store; stayed on managed Postgres because of cost and team skills.",
     [("System", "billing-db")]),
    ("D12", "Postmortem INC-2025-011: Gateway latency spike", "Postmortem", "2025-10-02", "P2",
     "Latency spike caused by unbounded retries from the gateway to auth-service. Added a circuit "
     "breaker and a 2s timeout.",
     [("System", "api-gateway"), ("System", "auth-service")]),
    ("D13", "Offboarding handoff: Dana Whitfield", "Handoff", "2026-01-28", "P1",
     "Dana left on 2026-01-31. The key rotation runbook was handed to Marco but no live walkthrough "
     "happened. The next signing key rotation is due in May 2026.",
     [("System", "auth-service")]),
]

# id, title, status, assignee person id, created, affects [system names]
TICKETS = [
    ("T-101", "Duplicate charge events after RabbitMQ failover", "closed", "P6", "2026-01-15",
     ["payments-service", "event-bus"]),
    ("T-102", "Evaluate Kafka vs Pulsar for the event bus", "closed", "P2", "2026-02-01", ["event-bus"]),
    ("T-103", "Kafka phase 1: dual-write events", "closed", "P3", "2026-03-02", ["event-bus"]),
    ("T-104", "Kafka phase 2: cut over consumers", "in_progress", "P3", "2026-04-10",
     ["event-bus", "data-warehouse", "notification-service"]),
    ("T-105", "Rotate JWT signing keys (90-day schedule)", "open", "P2", "2026-05-02", ["auth-service"]),
    ("T-106", "Add circuit breaker to gateway-to-auth calls", "closed", "P2", "2025-10-03",
     ["api-gateway", "auth-service"]),
    ("T-107", "Idempotency keys for checkout v2", "in_progress", "P6", "2026-03-05", ["payments-service"]),
    ("T-108", "mTLS pilot between gateway and auth", "in_progress", "P10", "2026-05-15",
     ["api-gateway", "auth-service"]),
    ("T-109", "Warehouse ingest failing after schema change", "open", "P9", "2026-04-15",
     ["data-warehouse", "event-bus"]),
    ("T-110", "Upgrade Kubernetes to 1.31", "open", "P4", "2026-05-18", ["k8s-cluster"]),
    ("T-111", "Push notification provider integration", "open", "P3", "2026-04-20",
     ["notification-service"]),
    ("T-112", "Checkout button misaligned on Safari", "closed", "P8", "2026-03-10", ["web-app"]),
]

# id, channel, author person id, date, text, mentions [(label, name)]
MESSAGES = [
    ("M1", "#platform", "P2", "2025-03-12",
     "Going with RabbitMQ for events. Simpler to run than Kafka and we are nowhere near the throughput "
     "where it matters.",
     [("System", "event-bus")]),
    ("M2", "#incidents", "P4", "2026-01-17",
     "Failover on the event bus just redelivered a whole batch of payment events out of order. "
     "Payments team, please check for duplicates.",
     [("System", "event-bus"), ("System", "payments-service")]),
    ("M3", "#payments", "P6", "2026-01-18",
     "Confirmed: 312 customers double charged. Refunds in progress. We need ordering guarantees from "
     "the bus.",
     [("System", "payments-service"), ("System", "event-bus")]),
    ("M4", "#platform", "P1", "2026-02-03",
     "Given INC-2026-004 I am okay switching to Kafka. Marco, please write the RFC. Cost is acceptable.",
     [("System", "event-bus")]),
    ("M5", "#platform", "P11", "2026-01-30",
     "Last day tomorrow! Key rotation lives in the runbook, ping me if anything is unclear.",
     [("System", "auth-service")]),
    ("M6", "#security", "P10", "2026-05-14",
     "Citadel needs auth-service v2 tokens everywhere before mTLS goes live. Who knows the signing key "
     "rotation process now?",
     [("System", "auth-service"), ("Project", "Project Citadel")]),
    ("M7", "#platform", "P2", "2026-05-14",
     "I have only read Dana's runbook, never done a rotation live. It is due this month and I am a "
     "bit nervous.",
     [("System", "auth-service")]),
    ("M8", "#data", "P9", "2026-04-15",
     "Warehouse ingest broke again after the Kafka phase 2 schema change. We need a schema registry.",
     [("System", "data-warehouse"), ("System", "event-bus")]),
    ("M9", "#product", "P7", "2026-03-05",
     "Atlas launch date depends on the Kafka cutover finishing. Can Platform confirm April?",
     [("Project", "Project Atlas"), ("System", "event-bus")]),
    ("M10", "#platform", "P3", "2026-04-12",
     "Phase 2 cutover pushed a week; notification-service consumers need updates first.",
     [("System", "event-bus"), ("System", "notification-service")]),
    ("M11", "#infra", "P4", "2026-05-20",
     "The k8s 1.31 upgrade is blocked until auth-service rolling restarts are safe, so the signing key "
     "rotation must finish first.",
     [("System", "k8s-cluster"), ("System", "auth-service")]),
]

# id, title, date, decided_by person id, rationale, sources [(label, id)], concerns [(label, name)],
# supersedes decision id or None
DECISIONS = [
    ("DEC-1", "Use RabbitMQ as the event bus", "2025-03-12", "P2",
     "Simple operations and low expected throughput (under 500 msg/s).",
     [("Document", "D1"), ("Message", "M1")], [("System", "event-bus")], None),
    ("DEC-2", "Keep the billing database on managed Postgres", "2025-09-18", "P6",
     "Cost and team skills outweighed the benefits of a distributed SQL store.",
     [("Document", "D11")], [("System", "billing-db")], None),
    ("DEC-3", "Add a circuit breaker and 2s timeout for gateway-to-auth calls", "2025-10-02", "P2",
     "Unbounded retries caused the INC-2025-011 latency spike.",
     [("Document", "D12"), ("Ticket", "T-106")],
     [("System", "api-gateway"), ("System", "auth-service")], None),
    ("DEC-4", "Rotate JWT signing keys every 90 days", "2025-08-14", "P11",
     "Limits the blast radius of a key compromise and is a compliance requirement.",
     [("Document", "D3")], [("System", "auth-service")], None),
    ("DEC-5", "Migrate the event bus from RabbitMQ to Kafka", "2026-02-05", "P1",
     "RabbitMQ redelivered messages out of order during a failover and caused duplicate charges "
     "(INC-2026-004). Kafka gives ordered partitions and replay.",
     [("Document", "D4"), ("Document", "D5"), ("Message", "M3"), ("Message", "M4"), ("Ticket", "T-101")],
     [("System", "event-bus"), ("System", "payments-service")], "DEC-1"),
    ("DEC-6", "Checkout v2 requires idempotency keys on payment events", "2026-03-01", "P5",
     "Prevents duplicate charges regardless of the bus delivery guarantees.",
     [("Document", "D6"), ("Ticket", "T-107")],
     [("System", "payments-service"), ("Project", "Project Atlas")], None),
    ("DEC-7", "Enforce mTLS between the gateway and services", "2026-05-12", "P10",
     "Reduces lateral movement risk and is required for the upcoming SOC 2 audit.",
     [("Document", "D8"), ("Message", "M6")],
     [("System", "api-gateway"), ("Project", "Project Citadel")], None),
    ("DEC-8", "Defer the Kubernetes 1.31 upgrade until the signing key rotation is done", "2026-05-20", "P4",
     "Rolling restarts of auth-service are unsafe until the key rotation completes.",
     [("Message", "M11"), ("Ticket", "T-110"), ("Ticket", "T-105")],
     [("System", "k8s-cluster"), ("System", "auth-service")], None),
]
