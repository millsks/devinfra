# PRD Quality Review — devinfra

## Overall verdict

This is an unusually strong PRD for its stakes: the Vision is specific enough that it could not be swapped into any other document, the Non-Goals are opinionated rather than defensive, the counter-metrics are real, and the factual claims it makes about the existing repository check out against the files almost without exception. What is at risk is not the thinking — it is the propagation. Two open questions were resolved during the session and the resolutions did not reach every place that depends on them: §4.2 still lists LocalStack as a candidate Service that FR-10 rules out, and the §6 inline `[ASSUMPTION]` still invites pushback on sequencing the maintainer has already confirmed. Separately, four FRs (FR-7, FR-11, FR-16, FR-19) carry "Consequences (testable)" that a story generator cannot turn into an acceptance criterion, and the MVP's first step is gated on an open question the body never flags.

## Decision-readiness — strong

The document reads like someone made decisions and is willing to defend them. §5 does not hedge — "*Requests to 'harden it a bit' are rejected on principle: a stack that is almost safe to deploy is more dangerous than one that obviously is not*" is a decision with its cost named. FR-10's Out of Scope entry rules out LocalStack "*on verified evidence, not preference*" and then lists the evidence. §8 Q5 (five containers vs `otel-lgtm`) names what is given up on both sides — "*less configurable, and it would discard the existing tuned configs under `docker/`*" — rather than concluding. The open questions are genuinely open; Q4 even labels itself as a confirm-it-stays-true question rather than pretending to be a requirement. The `[NOTE FOR PM]` at §6.2 sits on the highest-tension item in the document (the consumption model), not on a safe checkpoint.

The one place the PRD dodges is a sequencing collision it is aware of elsewhere. §6.1 makes CI the first MVP step. §8 Q8 leaves the task-runner surface open. The addendum states the interaction plainly — "*this decision interacts with FR-16 and should be settled before CI is built, not after — otherwise CI is written against Make and then rewritten*" — but nothing in §6.1 or FR-16 carries that warning forward. A reader who works only from the PRD body will start building step 1 on an unresolved foundation.

### Findings

- **high** MVP step 1 depends on an unresolved open question, and §6 does not say so (§6.1 item 1; §8 Q8) — "*Durability first (§4.4, FR-16 and FR-17). CI and automated updates come first*" is listed with no caveat, while addendum §E.4 warns the runner decision must be settled *before* CI. Whoever generates the durability epic will pick Make by default and inherit a rewrite. *Fix:* add a blocking note to §6.1 item 1 — "resolve §8 Q8 before starting" — or fold the runner decision into step 1 explicitly.
- **medium** §6's inline assumption tag contradicts its own index entry (§6 preamble vs §9 item 8) — the body still reads "*the phasing below is my proposal, not your stated plan … If you want a different cut, this is the section to push back on*", while §9 item 8 is struck through and marked "**RESOLVED — confirmed by the maintainer.**" A reader landing on §6 first will treat settled sequencing as provisional. *Fix:* strike the inline tag the way §8 Q2 and Q3 were struck, and replace it with the confirmed-order sentence.

## Substance over theater — strong

There is no furniture here. There are no personas — §2 declines to invent them and says why ("*This is a personal-stakes project: it succeeds if it keeps earning its place in the maintainer's own workflow*"), which is the right call for the shape. The Vision does the thing most Visions fail to do: it earns its generalities with specifics that could only come from having been burned — "*a `clientScopes` array in a Keycloak realm import strips the built-in scopes out of every client; Loki 3.x rejects OTLP outright without one config flag*". Both are real, and both are in the repository's README Gotchas section. The differentiation in the Landscape note is not template-filling; every competitor named is disqualified on a specific, checkable property rather than on vibes.

The NFRs in §4.5 are mostly bounded rather than adjectival — `127.0.0.1`, "under two minutes", "pinned to an explicit tag" — which is where most PRDs at this stakes level collapse into "secure and reliable." No findings worth raising.

## Strategic coherence — strong

The thesis is stated outright: "*The bet is that a stack you can trust and take pieces of beats a stack you have to take whole.*" The four features are not a backlog — modularity is the thesis, durability protects it, batteries make it worth taking, and catalog expansion is the payoff that only makes sense once the first three exist. §6.1's ordering follows that logic explicitly rather than from ease.

The Success Metrics are the strongest section per word in the document. SM-2 ("*Real projects run partial Selections rather than the whole Stack*") is a falsification test for the thesis, not an activity count. And SM-C1 is a genuine counter-metric that fights the PRD's own §4.2 — "*a Catalog that grows past what the maintainer actually uses has failed, not succeeded*". Very few PRDs are willing to name a section as the thing to guard against.

### Findings

- **low** §4.2 names seven candidate categories, §6.1 picks three, and the selection rule is soft (§4.2, §6.1 item 4) — "*the three the maintainer named or implied most directly*" does not say which were named and which were implied, so SM-C1 has nothing concrete to hold the next three against. *Fix:* one sentence distinguishing stated need from inferred need per candidate.

## Done-ness clarity — adequate

Most FRs land. FR-3 is a model of the form — "*Selecting `keycloak` without `postgres` fails before any container starts, with a message naming `postgres` as the missing dependency*" plus "*creates no containers and no volumes*" is directly executable as a test. FR-2's fourth consequence (Selection changes preserve volumes for Services that remain selected) and FR-12's anti-drift clause ("*A port or credential change in configuration causes the documentation to change or CI to fail — the two cannot silently diverge*") are both sharp. FR-9 is fully testable in all four bullets.

But four FRs put aspirations in testable clothing, and this is the dimension story generation leans on hardest. FR-19 is the worst offender: its title promises "Container Runtime Portability" and both of its consequences are satisfied by writing a paragraph. FR-16's runtime bound exists only inside an `[ASSUMPTION]` tag, so the consequence itself is unfalsifiable. FR-7's third bullet has no observable at all. FR-11 is a placeholder with no behavior of its own.

### Findings

- **high** FR-19's consequences require documentation, not portability (FR-19) — "*At least one alternative runtime is documented as supported, with any required deviations stated*" is satisfied by a README edit; nothing requires the Stack to actually start or the Smoke Test to pass on Podman or Colima. *Fix:* make the consequence "the Smoke Test passes on at least one non-Docker-Desktop runtime, verified in CI or by a documented manual run recorded per release."
- **high** FR-16's timing consequence is an aspiration with the number hidden in a tag (FR-16) — "*A full CI run completes in a time the maintainer will tolerate on every push. `[ASSUMPTION: under 15 minutes.]`*" The testable half is in the parenthesis; the bullet as written cannot fail. *Fix:* hoist it — "a full CI run completes in ≤15 minutes on a hosted runner" — and leave the assumption tag on the *number*, not on the existence of a bound.
- **medium** FR-7's README-consistency bullet has no observable (FR-7) — "*The existing Redis `noeviction` rationale documented in the README remains correct and is not silently invalidated*" cannot be checked mechanically. The real assertion already exists in `scripts/smoke-test.sh` ("maxmemory-policy is noeviction"). *Fix:* replace with "the existing `maxmemory-policy` smoke check still passes, and if the broker role moves off Redis the README rationale is updated in the same change."
- **medium** FR-6's Seed Data clause is not machine-checkable but is asserted to be (FR-6) — the FR requires "*Seed Data where the Service is unusable without it*" and then claims "*A Module lacking any of the four is rejected by CI*". CI cannot judge "unusable without it." *Fix:* require each Module to declare Seed Data explicitly — a directory, or an explicit `seed: none` marker with a one-line justification — so the CI check becomes presence-of-declaration.
- **medium** FR-11 is a backlog note wearing an FR number (FR-11) — its consequences are "*Each, if built, satisfies FR-6 in full*" and "*None is required for MVP*". Neither describes behavior. An epic generator walking FR-1..FR-19 will emit a story for it. *Fix:* demote to a "Catalog backlog" subsection under §4.2 or a §6.2 bullet, and renumber or explicitly mark FR-11 as reserved.
- **medium** FR-13's requirement is stronger than its check (FR-13) — the requirement is "*at least one dashboard exists showing traces, logs, and metrics*"; the verification bullet only asserts "*at least one dashboard is present and its datasource resolves*". A dashboard with three broken panels passes. *Fix:* assert one panel per signal renders non-empty against the smoke-injected trace/log/metric, which the existing smoke test already generates.
- **low** §4.5 "Configuration honesty" is the one adjectival NFR (§4.5) — "*the configuration must not depend on readers knowing it*" has no bound and no observable. *Fix:* state it as a check, e.g. "no `env_file` value is referenced by Compose interpolation; `docker compose config` output is asserted in CI."
- **low** FR-14's third consequence is a documentation statement, not a consequence (FR-14) — "*it is not a template to build production applications from, and says so*". Harmless, but it will read as an acceptance criterion downstream. *Fix:* move to the feature Description.

## Scope honesty — strong

Omissions are explicit and argued. §5 is doing real work rather than listing things nobody asked for, and §6.2 gives a reason per deferral rather than a silent drop — FR-10's deferral is especially honest, distinguishing "no longer blocked" from "still out of MVP purely on sequencing." The FR-15 note excluding Redis from backup coverage states the reasoning and then flags the condition under which it would stop holding, which is exactly what a `[NOTE FOR PM]` is for. Open-items density (six live open questions, seven live assumptions, three PM notes) is entirely appropriate for a personal-stakes brownfield document — none of them are load-bearing for a green light.

The gap is one resolved question that changed a *behavior* without any FR taking ownership of it.

### Findings

- **medium** The Q2 resolution mandates a Makefile behavior change that no FR owns (§8 Q2; FR-18) — "*Either way `make keycloak-reimport` stops dropping the database … Folded into FR-18.*" FR-18 is the Gotcha Register, and all four of its consequences concern documentation. Verified in `Makefile`: `keycloak-reimport` still runs `DROP DATABASE IF EXISTS keycloak WITH (FORCE)`. Nothing in §4 or §6 requires changing it. *Fix:* add a consequence to FR-18 ("`make keycloak-reimport` no longer drops the `keycloak` database") or a small FR under §4.3.

## Downstream usability — thin

This dimension matters here — §0 names `bmad-architecture` and epic generation as consumers. The scaffolding is good: FR IDs are contiguous 1–19 with no gaps or duplicates, UJ-1..6 and SM-1..3 plus SM-C1..C2 are clean, every UJ is realized by at least one FR, every FR referenced in §6.1 and §6.2 exists, and the §6 partition covers all nineteen FRs with no overlap. The Glossary is genuinely load-bearing and mostly held to.

What drags it down is that the two resolved open questions did not propagate into the sections a generator will actually read, and the Assumptions Index does not round-trip in either direction.

### Findings

- **high** §4.2 still lists a Service that FR-10 rules out (§4.2 vs FR-10, §8 Q3) — "*Candidate Services, in no committed order: … **OpenBao** for secrets, LocalStack for AWS services beyond S3, …*" while FR-10's Out of Scope reads "***LocalStack.** Ruled out on verified evidence, not preference*" and §8 Q3 is struck through as "**RESOLVED — not LocalStack.**" §4.2 is the section a catalog epic is generated from, so this is the contradiction most likely to produce wrong work. *Fix:* replace with "AWS service emulation beyond S3 (see FR-10 for the licensing constraint)"; the substitutes are already named in addendum §C.
- **medium** The Module completeness contract is five items in the description and four in the FR (§4.2 vs FR-6) — the description says "*Each new Service arrives as a Module … with Seed Data, a Healthcheck, a Smoke Test check, an Endpoint Contract, and any Gotchas discovered while adding it. A Service without those five things is not done*"; FR-6 lists four and drops Gotchas, then says "*A Module lacking any of the four is rejected by CI*". The CI gate and the stated bar disagree. *Fix:* pick one — either add Gotchas to FR-6 (as "a Gotchas file, possibly empty") or drop "five" from the description.
- **medium** Assumptions Index does not round-trip (§9 items 9 and 10) — §9 opens "*Every `[ASSUMPTION]` in this document*", but item 9 (§2, sole-user framing) has no inline tag anywhere in §2, and item 10 points at `addendum §E.4`, which is a different document. Conversely, every inline tag in §4 is indexed correctly. *Fix:* add the inline tag to §2, and re-scope item 10 as an addendum-sourced assumption with a label saying so.
- **medium** §8 Q8 cites the wrong addendum section (§8 Q8) — "*Trade-offs in [addendum.md](addendum.md) §F*"; §F is **Sources**. The Make-vs-pixi trade-offs are §E. §9 item 10 cites §E.4 correctly, so the two references disagree. *Fix:* change to §E.
- **medium** §4.5 NFRs have no IDs while FR/UJ/SM do (§4.5) — eight cross-cutting NFRs, including "*This is the Stack's single most load-bearing property*", none citable. Downstream architecture and story documents cannot reference "the data-durability NFR" by handle, and the traceability that FR IDs give the functional side is absent for the constraints. *Fix:* number them NFR-1..NFR-8.
- **low** Glossary drift: Profile names used as Selections (FR-2) — "*The existing Profile names (`admin`, `observability`) remain valid Selections*", but §3 defines Selection as "*the set of Modules and Bundles*", and Profile as "*Retained as an implementation detail; Bundle is the user-facing concept*". A Profile name is neither. *Fix:* "the existing Profile names remain valid Bundle names" or state that Profile names alias to Bundles.

## Shape fit — strong

The shape decisions are right and, more importantly, argued. §2.3 explicitly downscales the UJs — "*this is developer tooling with a single operator role, so full narrative journeys would be ceremony*" — which is the correct call and avoids the over-formalization this rubric warns about. Capability-spec shape with FR-grouped features, operational rather than user-facing SMs, no personas: all consistent with a hobby-stakes single-operator brownfield tool. The one-line UJs still carry a protagonist ("the maintainer", "he") and enough context to stand alone.

The brownfield handling is mostly excellent — §0 states the frame, FR-5 and FR-16 explicitly mark what they extend or change about existing behavior, and I verified the factual claims against the repo (see Mechanical notes) with essentially no errors. The gap is that the UJs do not distinguish what already works from what this phase builds.

### Findings

- **medium** New and existing UJs are not distinguished (§2.3) — UJ-1, UJ-3, UJ-4 and UJ-6 describe behavior the current stack already delivers (verified: `make init`/`make up`/`make smoke` and `make token` exist and work; every stateful Service has a named volume; datasources are provisioned). Only UJ-2 (Selection) and UJ-5 (CI) are new. The rubric's brownfield criterion asks for this distinction explicitly, and without it a story generator may scaffold work for journeys that already ship. *Fix:* tag each UJ `[EXISTING]` or `[NEW]`, or add one sentence to §2.3 naming UJ-2 and UJ-5 as the only new ones.
- **medium** UJ-1's two-minute claim is unachievable and contradicts §4.5 (§2.3 UJ-1 vs §4.5 Startup) — UJ-1 has the maintainer "*clone devinfra onto a new machine*" and run "*`make init && make up && make smoke`*" for "*thirteen verified services in under two minutes of wall time*". A new machine means a cold image cache — multiple GB of pulls — while §4.5 bounds two minutes to "*a warm image cache*". `scripts/smoke-test.sh` alone waits up to 60s for the observability backends to ingest, on top of a sub-two-minute `up`. *Fix:* split UJ-1 into image-pull time and start time, or drop `make smoke` from the two-minute claim and align the wording with §4.5's warm-cache qualifier.

## Mechanical notes

**Accuracy about the existing system — verified true.** These claims all check out against the repo:

- "*Grafana has a dashboard provider watching a directory containing only `.gitkeep`*" (§4.3) — exactly right; `docker/grafana/dashboards/` contains one zero-byte `.gitkeep`, and `docker/grafana/provisioning/dashboards/dashboards.yaml` exists.
- "*`make backup` covers Postgres and nothing else*" (§4.3) — the target is a single `pg_dumpall`.
- "*the current lint target skips `shellcheck` and YAML validation when their tools are absent, which means it can pass having verified nothing*" (FR-16, §8 Q8) — confirmed verbatim in `Makefile`; both branches echo a skip message and the target still exits 0.
- "*Nothing currently proves the Stack works except a human running `make smoke`*" (§4.4) — `.github/` contains only agent definitions; there is no workflow.
- "*Image tags are pinned*" (§4.4, §4.5) — pinned in `.env.example` and again as Compose defaults (`${POSTGRES_VERSION:-0.8.1-pg17}`).
- "*thirteen-service Compose stack*" / "*two Profiles*" (§0, §4.1) — thirteen user-facing Services plus the `minio-init` one-shot; profiles are `admin` and `observability`.
- OpenBao on 8200 "*does not collide with any port devinfra currently allocates*" (FR-9) — confirmed against every published port in `compose.yaml`.
- The Redis DB allocation, Keycloak→Postgres/Mailpit wiring, and collector fan-out described in §1 all match `compose.yaml` and the README.

**Small inaccuracies:**

- "*one 420-line [compose.yaml]*" (§4.1) — the file is 424 lines.
- "*the granularity of choice is "everything", "everything minus observability", or "the five core Services"*" (§4.1) — omits a fourth existing option, `COMPOSE_PROFILES=observability` (core + observability without the admin UIs).
- "*[scripts/smoke-test.sh], which already skips non-running Profiles*" (FR-5) — the script skips per **Service** via `running <service>`, which is finer-grained than Profile-level and slightly stronger than the PRD credits it with.
- "*The existing `make urls` output covers every selected Service*" (FR-12) — fine as a target, but "existing" overstates it: `make urls` today prints unconditionally regardless of Selection and omits Loki and Tempo entirely.
- §0 says the addendum holds "*port-allocation strategies*"; addendum §A.2 lists port allocation as an unsolved cost, not a strategy.

**Altitude leaks (implementation detail in the PRD body that belongs in the addendum):**

- §8 Q2's resolution runs to six paragraphs of Java-level and cache-level detail — `Strategy.IGNORE_EXISTING`, `removeRealm()`, `--http-management-port`, Infinispan staleness — nearly all of it duplicated in addendum §D. Two copies of the same technical finding will drift. *Fix:* keep the decision and the two operational caveats in §8; move the mechanism detail to §D and link.
- FR-9's "*Verified feasible*" paragraph ("*ships `wget` for a healthcheck, and listens on 8200*") duplicates addendum §C. Same drift risk, smaller.
- The LocalStack disqualification argument now appears three times: FR-10 Out of Scope, §8 Q3, and addendum §C.
- §4.1's assumption names Compose `include` and its minimum version, and §4.5's Fail-loud NFR names `${VAR:?message}` — both are mechanism choices. Tagged as assumptions, which mitigates it, but they pre-commit the architect.

**Stale text in the addendum:** §B.2 still describes LocalStack as "*Complementary rather than competing — see FR-10*", which contradicts §C's ruling in the same file and FR-10's Out of Scope entry. Worth correcting alongside the §4.2 fix.

**ID continuity:** FR-1..FR-19 contiguous and unique; UJ-1..UJ-6, SM-1..SM-3, SM-C1..SM-C2 all clean. Every §6 FR reference resolves. Every UJ is realized by at least one FR. The only broken cross-reference in either document is §8 Q8's "§F".
