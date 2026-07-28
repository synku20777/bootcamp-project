# ADR: Web onboarding feasibility

- Status: Accepted for the current delivery
- Decision: Keep secure setup in the terminal; consider a localhost-only wrapper
  as a later enhancement
- Scope: First-run configuration and recovery, not normal analytical dashboard use

## Context

The project needs a beginner-friendly first run, but setup crosses several
security and lifecycle boundaries. It collects Snowflake credentials, performs a
short account-administration phase, creates least-privilege roles, writes an
ignored local `.env`, executes Snowflake deployment SQL, builds containers, and
validates services. The existing bootstrap command already provides atomic
configuration, resumable postconditions, secret redaction, structured audit
logs, and non-destructive interruption behavior.

A web form can improve discoverability, but it does not remove the need to
install Docker and uv or to create the Snowflake account and Marketplace listing
outside the application. It also introduces a credential-handling service that
must be secured before the normal application exists.

## Options considered

### 1. Localhost-only setup wizard that wraps the bootstrap command

A small process could bind only to `127.0.0.1`, display setup explanations, send
values directly to the existing bootstrap boundary, and stream sanitized
progress events. It must not duplicate SQL, role, resume, or validation logic.

Advantages:

- Offers guided fields, inline validation, and visible progress.
- Can reuse the existing setup state and user-facing error taxonomy.
- Can be stopped after setup, reducing the long-term attack surface.

Risks and requirements:

- Must bind to loopback only and reject remote host/origin access.
- Must use CSRF protection and a one-time, short-lived setup token.
- Must never place secrets in URLs, browser storage, analytics, logs, traceback
  pages, or progress events.
- Password fields must use hidden input and be cleared after submission.
- The backend must write `.env` atomically with restrictive permissions and
  reuse the current backup/validation behavior.
- Setup must run as a single controlled job with explicit cancellation and
  interruption semantics; arbitrary command execution cannot be exposed.
- The process should shut down, or disable setup routes, after successful setup.

Conclusion: feasible, but it is a separate security-sensitive product feature,
not a documentation shortcut.

### 2. Setup page inside the existing Dash application

Advantages:

- Reuses the existing visual shell and component library.
- Avoids a second user-facing port after the application is running.

Problems:

- Dash currently starts through Compose, but first-run setup is responsible for
  creating its `.env`, building the image, and starting Compose. This creates a
  circular dependency.
- The long-lived analytical application would gain account-administration and
  secret-writing responsibilities.
- A remotely reachable dashboard setup route increases CSRF, credential
  exposure, and privilege-retention risks.

Conclusion: rejected. The analytical dashboard must not become a Snowflake
account bootstrap service.

### 3. Terminal-only guided bootstrap

Advantages:

- Already owns the complete secure and resumable workflow.
- Runs before containers exist and works consistently across clean machines.
- Hidden prompts keep passwords out of shell history and process arguments.
- Structured JSON audit records can exclude secrets while terminal messages
  remain actionable.
- Has a small attack surface and no temporary HTTP credential endpoint.

Limitations:

- Users must be comfortable opening a terminal and entering the repository root.
- Layout, hyperlinks, and interactive explanations are less rich than a browser.

Conclusion: selected for the current delivery, paired with a beginner-first
README and clearer numbered prompts, failure banners, success output, and
recovery commands.

## Decision

Keep `scripts/bootstrap.py` as the only setup authority. Preserve its atomic
`.env` writer, checksum/postcondition resume state, least-privilege role split,
structured logging, redaction, and non-destructive failure behavior. Improve its
terminal UX and documentation rather than copying setup logic into Dash.

If a browser wizard is later approved, implement option 1 as a thin,
localhost-only adapter over the same bootstrap operations. Do not implement a
setup page inside the production dashboard.

## Recommended phased approach

1. Current phase: beginner-first README, numbered terminal progress, explanatory
   hidden prompts, actionable failure taxonomy, and explicit verification.
2. Follow-up discovery: usability-test the terminal flow with new users and
   identify steps where a browser materially improves completion.
3. Security design: threat model the loopback wizard, token lifecycle, CSRF,
   origin policy, secret memory lifetime, process privileges, and shutdown path.
4. Optional implementation: build a thin adapter that consumes the existing
   bootstrap API/events; add no independent SQL or deployment implementation.
5. Verification: test loopback-only binding, redaction, setup-token expiry,
   browser storage absence, resumability, cancellation, and post-success shutdown.

## Consequences

New users still need a terminal, but they receive one authoritative path that
works before any container is running. The production dashboard retains its
least-privilege runtime boundary. A future wizard remains possible without
weakening or replacing the secure setup architecture.
