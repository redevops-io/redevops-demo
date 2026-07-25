# Context Runtime × AWS — from "seams" to production

**Companion to the blog post** [*The Context Runtime Meets the AWS Agent Stack*](https://redevops.io/blog/context-runtime-meets-the-aws-agent-stack).

This started as "what do we build for AWS." The file-level audit that follows — of `contextos`
(Context Runtime), `agentic-os` (Mission Runtime + Sidekick) and `redevops-demo` (the AWS glue) —
turned up a more important conclusion than the AWS roadmap itself.

> **Implementation status — 3 PRs, ~87 new tests, no boto3 required.** Built as a provider-neutral
> layer: AWS is the first concrete `CloudProvider`; Azure/GCP/DO are a new subpackage +
> `register_provider`, no kernel change.
> - **context-runtime #15** (`feat/provider-adapters-aws`) — Phase 0 + items 1–7 + governance:
>   fail-loud SQL guard · Bedrock model tier · OpenSearch + Bedrock KB retrievers · Athena/DuckDB
>   text-to-SQL · reasoning strategies · learning-on-default-path · pgvector · Bedrock Guardrails +
>   CloudWatch + GuardedModel · one-call `build_runtime(provider, …)`. **(75 tests)**
> - **context-runtime #16** (`feat/serving-surface-aws`, stacked) — §2.4–2.5: Bedrock `/v1` upstream ·
>   API-key auth on the read routes · MCP retrieval tool for AgentCore Gateway / Strands. **(7 tests)**
> - **agentic-os #4** (`feat/sidekick-cloudwatch`) — §2.6: Sidekick reads CloudWatch post-deploy;
>   each alarm becomes a governed response mission (alarm → mission → approve/rollback). **(5 tests)**
>
> **Remaining:** §2.7 AgentCore Identity (deferred to an AgentCore-enabled account; `identity_broker()`
> returns `None` today) and Part 3 scale-hardening (durable EventStore, saga-undo verification,
> sandbox) — infra work that wants live AWS. Read-route auth (§3.2) shipped in #16.

## The headline

**Context Runtime is no longer a research project. It's a production runtime with a surprisingly
small number of missing pieces — and the missing pieces are adapters, not AI.**

Roughly **80–90% of the runtime already exists and runs**: the planner, the cost model, the execution
graph, the representation router, the mission kernel, the governance/approval/saga machinery, and the
entire learning subsystem. What's left is a short, *bounded* list of integration work:

- Bedrock model adapter
- OpenSearch retriever adapter
- Bedrock Knowledge Bases retriever adapter
- SQL / analytical retriever
- the non-single-shot reasoning implementations
- pgvector adapter
- making the (already-built) learning loop the default execution path

Every one of those is a plugin or adapter behind an existing seam — **none require kernel changes.**
That last fact is the real finding (see *Architectural validation* below).

---

## Scoreboard (the 15-second view)

| Area | Status |
|---|---|
| Core planner | ✅ Production |
| Cost model (estimate-vs-actual) | ✅ Production |
| Retrieval routing (document / graph / temporal / community) | ✅ Production |
| Mission Runtime (event-source · replay · gates · saga · dynamic-risk) | ✅ Production |
| Sidekick (governed deploy + monitor loop) | ✅ Production |
| Governance / permissions plane (row/column RBAC) | ✅ Production |
| Cloud-provider seam (AWS first; Azure/GCP/DO drop-in) | ✅ Built (PR #15) |
| Learning (bandit · reward · IPS · aggregation · persistence · replay) | ✅ Now on the default path (`learning=True`) — was opt-in only |
| Reasoning strategies | ✅ plan-worker-critic / debate / tool-loop (was single-shot only) |
| SQL / analytical retrieval | ✅ Guarded text-to-SQL (DuckDB + Athena); ⚠ silent-BM25 fixed (fail-loud) |
| Bedrock / OpenSearch / KB adapters | ✅ Built (model tier + 2 retrievers) |
| pgvector | ✅ Built (also Aurora/RDS arm) |
| Content guardrails | ✅ Bedrock Guardrails + neutral GuardedModel |
| EXPLAIN calibration | 🟡 Real isotonic, identity until fitted (cold-start) — unchanged |
| Serving surface (auth · MCP tool · `/v1`-over-Bedrock) | 🟡 Next tranche (§2.4–2.5) |
| OAuth broker (identity) | ❌ Deferred to AgentCore Identity (§2.7) |
| Durable EventStore · sandbox (scale hardening) | ❌ Part 3, next tranche |

---

## Headline conclusions

**1. The work is concentrated in integration layers and optional execution strategies.** The planner,
optimizer, mission kernel, governance model and learning infrastructure are already implemented. That
is a far stronger position than "we still need to invent the architecture." This document reads like
an engineering design review, not a roadmap for research.

**2. The learning system is stronger than the blog post implies — and its gap is a deployment
decision, not research.** The article's honesty ("the cost-model *learner* … [is a] seam") undersells
what's there. The audit found a *complete* learning stack already built and tested: contextual
`EpsilonGreedyBandit`, logged-propensity selection, self-normalised IPS off-policy evaluation, an
async idempotent aggregator with replica snapshots, atomic persistence, and Kafka bus wiring — with
learned state on disk proving it runs. The only thing missing is **flipping it on by default**
(the runtime currently defaults to the *static* knapsack optimizer). That's a config/rollout call,
not an open research question.

**3. Zero AWS code is a feature, not a weakness — it proves the runtime stayed provider-neutral.**
The whole article argued Context Runtime should *not* rebuild AWS. The audit confirms it didn't: there
is no custom IAM, no custom OAuth, no custom vector DB, no custom agent orchestration baked into the
kernel. If those existed, the fair question would be *"then why am I paying AWS?"* Instead, AWS is
simply **another provider implementation** behind the same adapter seams every other backend uses.
That is exactly how it should look.

**4. Architectural validation (the most important finding).** *The core planner, cost model, execution
graph and learning loop are already provider-neutral. Every major missing capability is implemented as
a plugin or adapter rather than requiring kernel changes.* This validates the original architecture:
production integrations **extend** the runtime rather than **modifying** it. Bedrock, OpenSearch,
Athena, pgvector and the reasoning strategies all slot into existing protocols
(`RetrieverPlugin`, `ReasonerPlugin`, model `Tier`, `CostOptimizer`) — the kernel does not move to
accommodate any of them.

**5. The SQL silent-degradation is the one finding to act on immediately** — see §1.3. It is not
merely "unsupported"; it *silently substitutes BM25 for an analytical query*. Silent degradation is
worse than an honest failure. The full retriever is sequenced later, but the **fail-loud guard ships
first, before anything else** (Phase 0).

Status legend below mirrors the article: **●** real · **◐** partial · **○**/**∅** absent · **◆** spec only.

---

## Ground-truth detail (what the audit found)

| Subsystem | Article cell | Reality in code |
|---|---|---|
| `single_shot` reasoner | ◆ "single-shot ships" | **●** real (`reasoner/single_shot.py`) |
| plan-worker-critic / debate / tool_loop | ◆ "are seams" | **∅** enum names + docstring only; `runtime.py:170` hardcodes single-shot, ignores `candidate.strategy` |
| Cost-model **learner** (bandit) | ● "core thesis" / prose "seam" | **●** real & tested — **but off the default path**; `runtime.py:60` defaults to static `KnapsackOptimizer`; `execute()` never calls `bandit.learn()`; reward is bespoke per tenant |
| Cost-model **statistics** (est-vs-actual) | ● | **●** real, on the default path (`costmodel/statistics.py`) |
| SQL / analytical retrieval | ◆ "SQL adapter … seam" | **∅** planner routes `method=sql`→"analytical", but `HopRouterRetriever` has no analytical branch → **silently degrades to BM25**; DuckDB/Postgres stores are FTS, not OLAP |
| Retrieval: BM25 / dense / hybrid-RRF / temporal / community | ● | **●** real (`adapters/store_*.py`) |
| Retrieval: **pgvector** | ● "In-tree … pgvector" | **∅** no `store_pgvector.py`; Postgres store is `tsvector` full-text only |
| Multimodal CLIP/video (ONNX) | ● | **●** real, single-node (optional deps, exact cosine, no prod ANN) |
| HippoRAG graph | ● | **◐** real wrapper (heavy deps) + lightweight `SimGraphRetriever` fallback is what actually runs live |
| EXPLAIN calibrated P(relevant) | ● | **◐** real isotonic (PAV) — but **identity until a calibration map is fitted** (cold-start) |
| Representation router | ● routes 7 | **◐** executes 4 (document/graph/temporal/community); analytical + multimodal have no `HopRouter` branch |
| Mission Runtime (event-source, replay, gates, saga newest-first, dynamic-risk) | ● | **●** all real (`agentic_os/mission/*`) |
| Permissions plane (row/column RBAC) | ◐ | **●** real, but it's data-access RBAC, **not** identity federation |
| OAuth broker / token vault | ◐ "no OAuth broker" | **∅** confirmed absent (only static app tokens + Vault→STS role assumption) |
| Content guardrails | ◐ "no content filter" | **∅** only a shell-danger regex scan; no PII/injection/toxicity on model I/O |
| **AWS retrieval/model code** (Bedrock KB, OpenSearch, Bedrock provider, AgentCore) | implied by "optimization layer over AWS" | **∅** none in `contextos`; `redevops-demo` has real STS/ECR/boto3 but **no `context_runtime` import anywhere** — the integration is planned, not wired |
| Context-serving HTTP surface | — | **●** real FastAPI (`/librechat/retrieve`, `/explain`, OpenAI-compat `/v1/chat/completions`) — **but unauthenticated and no AWS arm behind it** |

---

## Phase 0 — immediate: stop the silent SQL→BM25 substitution  ·  ⚠

Independent of the full retriever. Today the planner selects `method="sql"`, the router
(`adapters/store_router.py:40`) has no analytical branch, and the query falls through to **BM25 with
no signal that analysis never happened**. Ship the guard *first*:

- `HopRouterRetriever` must **fail loudly** (or explicitly, visibly abstain in EXPLAIN with
  `reason="analytical retrieval not configured"`) when routed an unbound analytical/`sql`/`api`
  method — never return BM25 hits as if they answered an aggregation question.
- Add a test asserting an analytical route with no analytical backend raises / abstains rather than
  substituting lexical results.

This is a few lines and removes the one genuinely dangerous behaviour in the system.

---

## Part 1 — Close the admitted seams

### 1.1 Reasoning strategies → production  ·  ∅→●
**Reality.** Only `SingleShotReasoner` exists (`context_runtime/reasoner/single_shot.py`). The other
three (`plan_worker_critic`, `debate`, `tool_loop`) are names in a `Literal` (`types.py:204`) + a
docstring; `planner/rules.py:41` hardcodes every bucket to `single_shot`; `runtime.py:170` constructs
`SingleShotReasoner` unconditionally, never reading `candidate.strategy`.

**Work.** Implement `PlanWorkerCriticReasoner` / `DebateReasoner` / `ToolLoopReasoner` behind the
existing `ReasonerPlugin` protocol (`plugins/base.py:116`); add a `strategy → reasoner` factory and
dispatch on `candidate.strategy` in `execute()` (field already plumbed, ignored); roll multi-call cost
into `models_used` (`types.py:201`, only ever one element today); assign strategies to hard buckets in
`planner/rules.py` and let the bandit pick strategy as an arm dimension; tests + a benchmark showing an
accuracy lift at a measured cost delta. **AWS tie-in:** `tool_loop` is the shape that maps onto a
Strands/AgentCore agent loop (§2.3). *Kernel change: none — new `ReasonerPlugin` implementations.*

### 1.2 Cost-model learner on the default serving path  ·  ●(opt-in)→●(default)
**Reality.** The learning loop is real — `EpsilonGreedyBandit` (`integrations/bandit.py`),
`BanditOptimizer` with logged propensity + IPS (`optimizer/online.py`), async `LearningAggregator`
(`learning/aggregator.py`), atomic persistence (`.context-runtime/*.json` on disk prove it runs). But
the core defaults to static `KnapsackOptimizer` (`runtime.py:60`); `execute()` only calls
`estimator.observe()`, never `bandit.learn()`; reward is hand-written per tenant.

**Work.** Promote `BanditOptimizer` to a first-class runtime mode (`ContextRuntime(learning=True)`),
default-on in the AWS serving profile; a **shared validated reward contract** (`quality − λ·cost`,
quality pluggable) so tenants stop reinventing it; wire `execute()` to emit `OutcomeEvent` on the
default path (replay-safe, idempotent — the aggregator already supports it); deliver the **contextual**
bandit the comments promise (`bandit.py:8` names a "River contextual bandit" that isn't there — LinUCB
/ logistic over real features, not just the bucket string); harden the Kafka bus at scale.
*This is a deployment decision, not research — the machinery exists.* *Kernel change: none — the
optimizer seam already exists.*

### 1.3 SQL / analytical retrieval engine  ·  ∅→●  (guard ships in Phase 0)
**Reality.** See Phase 0 — the planner routes to `analytical` but nothing executes it.

**Work.** A real `AnalyticalRetriever(RetrieverPlugin)`: schema introspection → guarded, read-only,
statement-allowlisted, row-capped text-to-SQL → execute → rows-as-`Hit`; bind `sql/mongo/elastic/
logs/api` in `HopRouterRetriever`; a DuckDB backend for local/offline and a warehouse backend for §2;
tests that execute a real `group by`/`count`, plus a benchmark vs BM25 on aggregation questions.
**AWS tie-in:** the highest-leverage AWS arm — text-to-SQL over **Athena / Redshift / RDS** is exactly
the "analytical representation" the router already wants. *Kernel change: none — new `RetrieverPlugin`.*

### 1.4 Representation-router completeness + LLM-head hardening  ·  ◐→●
Add `analytical` + `multimodal` branches to `HopRouterRetriever` (routable but undispatched today).
Harden `planner/llm_intent.py` (raw `urllib`, no timeout/retry/cache; already confidence-gated behind
the keyword head) with a bounded timeout, one retry, and a small decision cache.

### 1.5 EXPLAIN calibration cold-start  ·  ◐→●
Isotonic PAV calibration is real (`integrations/calibration.py`) but the map is identity until fitted
from judge labels. Ship a bootstrap calibration (prior or default fit from the passage-judge labels
the code already generates) + an auto-refit loop; surface "uncalibrated" honestly until enough labels
exist.

### 1.6 pgvector adapter  ·  ∅→● (or correct the article)
No `store_pgvector.py`; the Postgres store is `tsvector` only. Build the real adapter (vector column,
ANN index, `<=>` similarity) — which doubles as the **Aurora/RDS pgvector** arm — or downgrade the
article's ● claim to ◆ until it ships. Recommend building; it's small.

---

## Part 2 — Build the AWS-fit surface

Zero of this exists today (`context_arms/` is empty; `redevops-demo` never imports
`context_runtime`). Per headline conclusion #3, that's provider-neutrality, not neglect — AWS is one
more provider behind the seams. Each item below is an adapter, not a kernel change.

### 2.1 Bedrock as a native model Tier  ·  ∅→●  ← **do first (easy · huge demo value)**
Model plane is provider-generic HTTP today; Bedrock is only a preflight probe
(`aws_demo/preflight.py:126`). Add a `BedrockModel` adapter (`bedrock-runtime` `converse`) registered
as a CR model **Tier** with a real cost table for the Bedrock model IDs (Nova / Claude-on-Bedrock), and
mirror it as a tier in the `agentic_os` router so Sidekick missions can spend Bedrock. Now CR's core
"which model tier" decision includes Bedrock, priced and learned like any other tier.

### 2.2 OpenSearch retriever  ·  ∅→●  ← **do second (easy · real AWS integration)**
`OpenSearchRetriever(RetrieverPlugin)` over OpenSearch Serverless (`aoss`), registered as a method the
KR router and bandit can select. IAM grants already exist (`aoss:*` in `infra/terraform/safety/
roles.tf`) with no code behind them — this fills that in.

### 2.3 Bedrock Knowledge Bases retriever  ·  ∅→●
`BedrockKBRetriever(RetrieverPlugin)` wrapping `Retrieve` / `RetrieveAndGenerate`, registered
alongside OpenSearch + the local arms. With 2.1–2.3 in place, the payoff artifact lands: an offline
benchmark where **CR learns when Bedrock KB vs OpenSearch vs local hybrid wins per query class** — the
article's thesis, measured. This is the demo centrepiece and the moment "CR optimizes over AWS" stops
being positioning and starts being a running system.

### 2.4 Context-serving sidecar for AgentCore / Strands agents  ·  ●(local)→●(AWS)
`control_plane/app.py` already serves `POST /librechat/retrieve` → `{strategy, method, hits, context,
plan_id}` and `/explain` — production-shaped, but unauthenticated with only a local corpus. Wrap
`retrieve` as an **MCP tool** so an AgentCore **Gateway** (which *is* MCP) or a Strands agent calls it
as a first-class tool (ride AWS's tool plane, don't invent one); add IAM/SigV4 auth (§3.2); package as
a container deployable next to the agents in the VPC. With §2.1–2.3 behind it, the sidecar returns a
cost-optimal bundle drawn from KB / OpenSearch / local arms with a calibrated score + EXPLAIN trace.

### 2.5 CR as the optimizing OpenAI-compatible proxy in front of Bedrock  ·  ◐→●
`/v1/chat/completions` (`app.py:1654`) already plans + retrieves + injects + judges + learns, forwarding
to `CR_UPSTREAM_*`. Make Bedrock a first-class upstream (converse under the hood). Any agent pointed at
CR's `/v1` endpoint then gets context-optimized Bedrock calls with EXPLAIN + learning, zero client
change — the fastest live "CR optimizing Bedrock" story.

### 2.6 Sidekick post-deploy: CloudWatch/X-Ray + in-VPC agent comms  ·  ◐→●
Monitor loop reads Prometheus/Loki/k8s (`deploy/sidekick-devops/mcp_reads.py`); no CloudWatch;
federation is plain HTTP by URL (`federation.py`). Add a `cloudwatch_query` reader so a CloudWatch
alarm spawns a governed response mission (the `operate` operator already models OOM→remediate); a
VPC/PrivateLink-aware transport (or document the in-VPC mesh assumption); and the alarm → mission →
pause/approve/**saga-rollback** channel that closes the loop to the cockpit.

### 2.7 Identity — lean on AgentCore, don't rebuild  ·  ◐→●
No OAuth broker; the permissions plane is data RBAC; Vault→STS is AWS role assumption. Integrate
**AgentCore Identity** as the broker — CR/Sidekick tools consume AgentCore-issued delegated tokens
rather than CR building an OAuth server. Matches the post's "infrastructure we don't reimplement."

### 2.8 Content guardrails — lean on Bedrock Guardrails  ·  ◐→●
Only a shell-danger regex scan (`agentic_os/safety.py`); no PII/injection/toxicity on model I/O. Apply
**Bedrock Guardrails** on CR model I/O (via the §2.1 tier) + an optional local PII-redaction pass for
self-hosted. The article's "you'd want all three" becomes literally true.

---

## Part 3 — Cross-cutting production hardening

| # | Item | Reality | Work |
|---|---|---|---|
| 3.1 | Durable EventStore | in-memory / JSON-file, single-process, process-lock only (`mission/store.py`) | back with Postgres or DynamoDB for multi-writer + replay at scale |
| 3.2 | Serving-surface auth | `/retrieve`/`/explain`/`/compare` are open (`app.py`); only mutating POSTs are key-gated | IAM/SigV4 or API key on read routes before exposing to agents (§2.4) |
| 3.3 | Saga undo verification | compensation assumes each `undo` actually reversed the effect (`runtime.py:378`) | verify post-state after compensation; alert on undo that didn't restore |
| 3.4 | Privileged-CLI sandbox | kubectl/terraform/docker run in-process; only *scan* uses disposable containers | run operators in gVisor/Firecracker or AgentCore's managed sandbox (lean-on-AWS) |
| 3.5 | Retrieval scale | dense/multimodal use exact in-memory cosine, no persistent ANN | wire ANN (Qdrant/TurboVec seams) or use OpenSearch/pgvector (§2.2/§1.6) as the prod index |

---

## Sequencing (demo-driven)

Ordered so the first four items each produce something demonstrable on redevops.io — real AWS
integration you can *show*, not just claim. The fail-loud SQL guard is the one exception that jumps the
queue because silent degradation is a correctness bug, not a feature.

0. **Fail-loud SQL guard** (Phase 0) — a few lines; kills the one dangerous silent behaviour.
1. **Bedrock Model Tier** (§2.1) — easy, huge demo value.
2. **OpenSearch Retriever** (§2.2) — easy, real AWS integration.
3. **Bedrock KB Retriever** (§2.3) — completes the "CR routes across AWS retrieval" demo.
4. **SQL / Analytical Retriever, Athena first** (§1.3) — text-to-SQL over a data lake, demoable.
5. **Reasoning Strategies** (§1.1) — plan-worker-critic / debate / tool_loop.
6. **Bandit as default** (§1.2) — flip learning on; a deployment decision, not research.
7. **pgvector** (§1.6) — Aurora/RDS arm.
8. Everything else — §2.4 sidecar/MCP + auth, §2.5 `/v1`-over-Bedrock, §2.6 CloudWatch/in-VPC,
   §2.7 Identity, §2.8 Guardrails, and Part 3 hardening.

Rationale: items 1–4 are each a live demo on the site; 5–7 retire the article's admitted seams;
the rest is governance parity (leaning on AWS) and scale hardening.

## Article-accuracy follow-ups (keep the "we say so plainly" ethos)

Two matrix cells over-claim relative to the code:

- **pgvector** listed as an in-tree native engine (●) — not built (§1.6). Downgrade to ◆ or ship it.
- Retrieval engines described as **"In-tree (Go+Py)"** — `contextos` retrieval is **Python-only**
  (the Go port lives in `agentic-os/go/mission/*`, the mission *kernel*, not the retrieval engines).
  Qualify the claim or note that the Go port is the mission kernel.

Everything else in the matrix holds up against the code, including every ◐/◆ that already flags
partial or spec status — which is the whole point of the honest framing, and the reason this audit
raises confidence rather than lowering it.
