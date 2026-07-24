# Costs — MAP Agentic Accelerator (answers for the team)

> All figures are approximate (region eu-north-1, 2026 pricing). The real bill depends on
> traffic, number of reports, and current AWS/Bedrock rates.
> **Current choice:** ECS Fargate 2 vCPU / 4 GB, 1x NAT Gateway, ALB, Bedrock Claude Sonnet 4.5.

---

## 1. Averaged cost with the CURRENT choice

### A) Infrastructure (independent of AI)

| Resource | Rate | Cost / month (24/7) |
|---|---|---|
| ECS Fargate (2 vCPU + 4 GB) | 2×$0.0405 + 4×$0.00445 /hr | **~$72** |
| NAT Gateway (1x) | ~$0.045/hr + data transfer | **~$37** |
| ALB | ~$0.0225/hr + LCU | **~$18** |
| S3 + DynamoDB + ECR + Cognito + Logs | usage-based | **~$5** |
| **TOTAL infra (ECS 24/7)** | | **~$132 / month** |
| **TOTAL infra (ECS scale-to-zero)** | NAT+ALB+rest only | **~$60 / month** |
| **After `tofu destroy`** | | **~$0** |

### B) Bedrock (Claude Sonnet 4.5) — pay per token
- Rate: **$3 / 1M input**, **$15 / 1M output** (+~10% for cross-region `eu.`).
- **Cost of 1 generated business case ≈ $1.80** (assumption: ~250k input + ~60k output tokens across 9 agents).

| Reports / month | Bedrock cost / month |
|---|---|
| 50 | ~$90 |
| 100 | ~$180 |
| 200 | ~$360 |

### C) Averaged bill — 3 realistic scenarios (100 reports/month)

| Scenario | Infra | Bedrock | **Total / month** |
|---|---|---|---|
| **Always-on** (ECS 24/7) | ~$132 | ~$180 | **~$312** |
| **Business hours** (ECS ~220 h/mo, NAT/ALB 24/7) | ~$82 | ~$180 | **~$262** |
| **On-demand PoC** (`destroy` when idle, ~40 h/mo) | ~$20 | ~$180 | **~$200** |

> **Number to remember:** with the current choice and ~100 reports/month it is
> **~$260-310 / month**, of which AI (Bedrock) is ~$180 and the infrastructure itself
> ~$80-130. The dominant fixed infra cost is the **NAT Gateway (~$37)**, not compute.

---

## 2. How to make it cheaper (ordered by leverage)

1. **Scale-to-zero off-hours** (`desired_count=0`) — already in place. Cuts compute to $0 when idle. Saving: ~$50/mo.
2. **Fargate Spot: -70%** on compute for non-prod/PoC. Compute ~$72 → ~$22/mo.
3. **Drop NAT Gateway → VPC Endpoints** (Bedrock, S3, DynamoDB, ECR). The container only talks to AWS services, so NAT (~$37) can be removed. Note: interface endpoints cost ~$7/mo each, so it pays off with longer uptime.
4. **Bedrock: batch -50% + prompt caching up to -90%** on repeated input (the same 9 system prompts). Biggest lever on the AI side.
5. **`tofu destroy` for weekends/PoC** — infra bill → ~$0.

---

## 3. Compute alternatives — including "without Fargate at all" + operational overhead

The key trade-off is **cost vs. operational overhead vs. fit for a long-running, stateful workflow**
(our graph of 9 agents runs for minutes; workflow budget up to 30 min, 10 min per agent).

| Option | Compute cost/mo (100 rep.) | NAT needed? | Operational overhead | Fits workload? |
|---|---|---|---|---|
| **Fargate (current)** | ~$72 (24/7) / ~$22 Spot | Yes (~$37) | **Low** — no servers, AWS runs it | ✅ Yes |
| **App Runner** (managed containers) | ~$25-70, scales to zero | No | **Low** — no ALB/NAT to manage | ✅ Yes |
| **ECS on EC2** | ~$15-30 (Spot) | Yes | **Medium** — you manage EC2 capacity/AMIs | ✅ Yes |
| **Plain EC2** (docker on 1 box) | ~$8-15 (t4g Spot/reserved) | No (public subnet) | **High** — you patch OS, no auto-heal/scale | ✅ Yes (fragile) |
| **Lambda + Step Functions** | **~$10-20** (see below) | No (Bedrock is public) | **High one-time** — full rearchitecture | ⚠️ Only if split per agent |

### 3a. Lambda — the numbers (as requested)

If we ignore architecture for a second and just price the compute:
- Lambda: **$0.0000166667 / GB-second** + $0.20 / 1M requests.
- A report = 9 agents. Say each agent runs ~2-5 min at 4 GB:
  - 9 × 180s × 4 GB × $0.0000166667 ≈ **$0.11 per report** (generous 5 min: ~$0.18).
- **100 reports ≈ $11-18/month of compute.** Step Functions transitions + API Gateway ≈ pennies.
- **No NAT** (Bedrock is a public API, Lambda can run outside the VPC) → save ~$37.
- **~$0 when idle** (no always-on server).

➡️ On paper Lambda is the **cheapest compute** (~$10-20/mo vs ~$72 Fargate) **and removes NAT**.

### 3b. So why don't we use Lambda? (operational overhead is the real cost)

The cheap number hides a large one-time and ongoing **operational overhead**:

1. **15-minute hard limit per function.** Our workflow budget is 30 min. We'd have to **split each agent into its own Lambda** and orchestrate with **Step Functions** — a real rewrite of the current single long-running `GraphBuilder` process.
2. **Stateful web/login server.** Today it's one Gunicorn server with sessions. Serverless means splitting UI/login into API Gateway + Lambda (or a separate small host), plus an **async job pattern** (submit → poll/stream progress) because a report can't be a single 30-min HTTP request.
3. **Cold starts + streaming.** Interactive UX and long streaming are awkward on Lambda; needs provisioned concurrency (extra cost) or UX changes.
4. **More moving parts to operate.** Step Functions, per-agent Lambdas, API Gateway, distributed tracing, harder local dev/debug. The team owns more surface area.

**Verdict:** Lambda's *compute* is ~$50/mo cheaper and kills NAT, but the **rearchitecture + ongoing operational overhead** outweigh that for a long-running, stateful, interactive app **at this stage**. We get most of the savings with far less risk via **Fargate Spot + scale-to-zero + VPC endpoints**. Lambda becomes attractive only if we redesign the workflow into short, stateless, event-driven steps.

### 3c. Cheapest realistic path *without Fargate* (and its overhead)

- **Lowest bill, highest overhead:** Lambda + Step Functions (~$10-20 compute, no NAT) — but requires the rewrite above; the team maintains a distributed serverless system.
- **Balanced:** **App Runner** — no Fargate, scales to zero, no ALB/NAT to manage; **lowest operational overhead** of the non-Fargate options while keeping the current container as-is. Good "cheaper + simpler" middle ground.
- **Cheap but brittle:** single EC2 running the container — cheapest steady box, but you own patching, availability, and deploys (**high ongoing overhead, no auto-heal**).
