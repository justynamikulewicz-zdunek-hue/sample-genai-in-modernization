# ADR-001: User Authentication (STX company users + clients)

- **Status:** Proposed
- **Date:** 2026-07-23
- **Product context:** MAP Agentic Accelerator — a multi-tenant blueprint; each client gets a
  fully isolated environment (`${client_name}-...`), including its own Cognito User Pool.

---

## Context

The application runs behind an ALB on ECS Fargate. The auth layer is **Amazon Cognito**
(OAuth2 code flow, Hosted UI) — currently in "admin-create only" mode: an administrator
creates a user who receives a temporary password by email. Defined in
[modules/cognito/main.tf](modules/cognito/main.tf).

We have **two distinct audiences** with different needs:
1. **Company users (STX Next)** — we already run Google Workspace and AWS IAM Identity Center.
2. **External clients (enterprise)** — each has its own identity system (Azure AD/Entra,
   Okta, etc.) and requires isolation from other clients.

Important distinction: **application authentication** (who the user is) is not the same as
**network access** (who can reach the ALB). Pritunl/VPN is a network layer — it can run
alongside, but does not replace login.

---

## Decision

### 1. STX company users → Cognito federated with Google Workspace (OIDC)
- Add an identity provider of type Google/OIDC to the Cognito User Pool
  (`aws_cognito_identity_provider`) and add it to `supported_identity_providers` on the app
  client (alongside/instead of `COGNITO`).
- Result: login with an `@stxnext.pl` account, auto-provisioning, no separate passwords.
- **Rationale:** lowest effort for the internal team; reuses the existing Google Workspace.

### 2. External clients → per-tenant Cognito + federation with the client's IdP (SAML/OIDC)
- Each client has its **own Cognito User Pool** (already guaranteed by `client_name`) → full
  identity isolation between clients.
- Login model per client, in order of preference:
  1. **Federation with the client's IdP** (SAML 2.0 or OIDC): Azure AD/Entra ID, Okta, etc. —
     the client manages its own users, we trust their IdP. Recommended for enterprise.
  2. **Cognito "admin-create only"** (current state) — when the client has no IdP or does not
     want to connect one. We create the accounts, temporary password sent by email.
- Parameterization: login type as a per-client variable (e.g. `auth_mode = "saml" | "oidc" | "cognito"`),
  so the blueprint stays generic.

### 3. Pritunl / VPN — optional network layer, NOT a login method
- Can restrict network access to the ALB (e.g. company network only) as defense-in-depth.
- **Does not replace** Cognito authentication. Treated as a separate, optional network decision.

---

## Rejected alternatives

| Option | Why rejected |
|---|---|
| **LDAP / on-prem AD** | Heavy (needs ADFS/Keycloak as a SAML bridge); only makes sense when a client runs on-prem AD. Overkill for STX. |
| **Custom in-app password system** | Reinventing the wheel; weaker security than Cognito; no SSO. |
| **Pritunl as login** | It is a VPN (network layer), not identity — does not satisfy the user authentication requirement. |

---

## Consequences

**Positive**
- STX users: login with corporate Google, zero password management.
- Clients: log in with their own IdP; full per-tenant isolation maintained.
- Blueprint stays generic (login mode as a per-client parameter).

**To do (roadmap)**
- [ ] `modules/cognito`: add an optional `aws_cognito_identity_provider` (Google OIDC) + an `auth_mode` variable.
- [ ] Update `callback_urls`/`supported_identity_providers` on the app client.
- [ ] Per-client runbook: how to connect Azure AD/Okta (metadata URL, attributes, claim mapping).
- [ ] (optional) Network decision: whether to put the ALB behind a VPN/IP allowlist for selected clients.

**Cost**
- Cognito: the free MAU tier covers a small team; beyond that ~$0.005-0.015 / active user / month.
- Federation with Google/AWS SSO/client IdP: no additional AWS charge.
