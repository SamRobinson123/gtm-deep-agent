# Tricentis — What We Sell & Who We Compete With

**When to load**: Any task that reasons about *competitive threat* or *build-vs-buy* risk in a
deal — e.g. "which deals mention a competitor", "who are we losing to", "customers talking about
building their own testing", win/loss by competitor. Primary consumer: `gtm-risk-report`.
**Nature**: Reference knowledge, not a data contract — it names products and rivals so call-text
signals can be recognized and mapped to the threatened product. Vendor/market landscape as of
mid-2026; refresh the competitor lists periodically (see Sources at the bottom).

---

## What Tricentis does (one paragraph)

Tricentis is an enterprise **continuous testing / AI-driven quality-engineering** platform —
model-based, largely codeless automation across UI, API, data, and performance, with deep
**SAP / packaged-app** strength (SAP solution extension; S/4HANA, Fiori, Signavio, SAP Cloud ALM
integration). HQ Vienna (AT) + Austin (TX). In 2026 the portfolio is being unified under an
**"Agentic"** (autonomous AI) brand. We sell **software subscriptions** (per-product licenses +
services) — so the deal dies if the customer picks a **rival tool** *or* decides to **build and
maintain their own** instead. Those are the two threats this reference supports. Per-competitor
detail — why customers pick each one, price signals, how we counter — is in
**[Competitor threat profiles](#competitor-threat-profiles--the-color-behind-each-name)** below;
read it to add color to a flag rather than just naming the tool.

**Portfolio & platform facts (mid-2026)** — context for the narrative:

- **Built by acquisition.** Testim (AI UI automation, ~$200M, 2022), Waldo (mobile, 2023), and
  **SeaLights** (quality intelligence, ~$150M, 2024) are bolt-ons now folded into the Agentic
  platform. SeaLights (= **C-Lights** in transcripts) maps code changes to tests and uses ML to run
  the *minimum* set of functional tests — test-impact analysis, not just a coverage %.
- **AI is the 2026 wedge.** Tosca ships **Vision AI** + **Agentic Test Automation** (natural-language
  test generation, 160+ technologies); qTest has **Agentic Test Creation**; **NeoLoad** was the first
  perf tool to implement **MCP** (natural-language-directed load tests) and has an **Augmented
  Analysis** engine. When a customer weighs an "AI-native" challenger, this is our counter-story.
- **SAP is the moat.** Tricentis is an SAP **solution extension**; **SAP Change Impact Analysis (SAP
  CIA)** is literally our LiveCompare engine SAP-branded and wired into **Cloud ALM** release gates —
  so "SAP CIA" is **us**, not a rival. SAP Solution Manager mainstream maintenance ends
  **2027-12-31**, which is *driving* S/4HANA testing deals (tailwind) but also why customers shop now.

---

## Our products → the segment each competes in

A customer naming a rival is threatening a *specific* product. Map the mention to the product.
(SKU family names as they appear in `sku_nacv_fact` / the model vocabulary are in **bold**.)

| Product (2026 "Agentic" name) | What it does | Threatened by (segment) |
|---|---|---|
| **Tosca** — *Agentic Test Automation* (incl. Tosca Cloud, Vision AI, ATA, DEX/Elastic Execution Grid) | Model-based, codeless UI/API/E2E automation; flagship | Functional-UI automation + SAP automation (below) |
| **Testim** (+ Testim Salesforce, Mobile) | AI-based UI/functional automation | Functional-UI automation |
| **qTest** — *Agentic Test Creation* (Manager, Insights, Explorer, Pulse, ATC) | Test management | Test-management (below) |
| **NeoLoad** — *Agentic Performance Testing* | Performance / load testing | Performance (below) |
| **LiveCompare** | SAP change-impact analysis / change intelligence | SAP change intelligence (below) |
| **Data Integrity** (aka Tosca DI / Tosca BI) | Data / ETL / BI / report testing | Data testing / observability (below) |
| **SeaLights** — *Agentic Quality Intelligence* | Test/quality intelligence, code coverage, test-impact analysis | Quality intelligence / coverage (below) |
| **Vera** | Validation & compliance (regulated industries; e-sign on test evidence) | Niche — usually no direct rival named |
| **Device Cloud** | Real-device mobile testing | Cloud device/browser grids (below) |

---

## Competitor landscape by segment (names customers actually say)

Recognize these in call text. **Spelling in transcripts is often mangled** — see the variants note.

- **Functional / UI automation** (vs Tosca, Testim): Selenium, Playwright, Cypress, Appium
  (these four are open source — see the build-vs-buy overlap note), **Katalon**, **OpenText UFT
  One / UFT Developer** (ex–Micro Focus), **SmartBear TestComplete**, **Ranorex**, **Leapwork**,
  **Keysight Eggplant**, **ACCELQ**, **UiPath Test Suite / Agentic Automation**, mabl, Functionize,
  testRigor, Sahi Pro, Applitools (visual), BugBug.
- **SAP / packaged-app automation** (vs Tosca, LiveCompare): **Worksoft (Certify)**, **Panaya**
  (Smart Testing & Change Intelligence — the closest LiveCompare rival), Basis Technologies
  (Testimony), **SAP's own** (SAP Cloud ALM test automation, Signavio), ACCELQ, OpenText.
- **Performance / load** (vs NeoLoad): **Apache JMeter** (open source, dominant share), **Grafana
  k6** (open source/cloud), **Gatling**, **OpenText LoadRunner** / Professional Performance
  Engineering, **BlazeMeter** (Perforce), Locust, The Grinder.
- **Test management** (vs qTest): **Xray** (Jira), **Zephyr** (SmartBear), **TestRail**
  (Idera/Gurock), PractiTest, Testmo, native Jira / Azure DevOps.
- **Cloud device/browser grids** (vs Device Cloud): **BrowserStack**, **Sauce Labs**, **LambdaTest**,
  **Perfecto** (Perforce).
- **Data testing / observability** (vs Data Integrity): QuerySurge, iCEDQ, Datagaps, **Panaya**;
  data-observability crossovers **Monte Carlo**, Great Expectations.
- **Quality intelligence / coverage** (vs SeaLights): Codecov, JaCoCo, Launchable, Datadog CI Visibility.

---

## Build-vs-buy ("do it in-house") signals

The second threat: the customer decides to **build and maintain their own** testing instead of
buying. This is *not* the same as naming a commercial rival — here there's no vendor, they DIY.
Recognize phrasing like:

- "we'll **build our own** framework / harness / tool", "**home-grown**", "**in-house** solution",
  "**our own** automation", "**maintain it ourselves**", "roll our own"
- "our **internal QE / SDET / quality-engineering / platform team** will handle it / is
  standardizing on …", "we have engineers who can script this"
- Standing up an **open-source stack themselves**: Selenium/Playwright/Cypress/Appium (functional)
  or JMeter/k6/Gatling (perf) framed as *"we'll build/own/maintain it"* rather than *"we're
  evaluating it as a product"*
- "just keep doing it **manually**", "not sure we need a tool", "budget won't cover a tool so we'll
  do it internally"

**Competitor vs build-in-house — the overlap that trips people up.** Open-source names
(Selenium, Playwright, Cypress, JMeter, k6) sit on both lists. Decide by *intent*:
- Customer is **evaluating/comparing it as an alternative product** → **competitor** signal.
- Customer intends to **build and own/maintain a framework on it themselves** → **build-in-house** signal.
A deal can carry **both** (e.g. "we're leaning toward building our own Playwright suite" = build-in-house;
"we're also trialing Katalon" in the same call = competitor). Label what the calls actually say.

---

## Reading transcript mentions — gotchas

- **Mangled spellings are common** (auto-transcription). Treat these as the real tool:
  `Catalon` = Katalon · `Renorex` = Ranorex · `Testam` = Testim · `Trisentis`/`Tricentis`/`Tri-census`/`Tracentis` = **us** ·
  `Neoload`/`Neo Load` = NeoLoad · `Live Compare` = LiveCompare · `C-Lights`/`Sealights` = SeaLights ·
  `Qtest`/`Q-Test` = qTest · `Monte Carlo` = the data-observability vendor (not the statistical method).
- **Us ≠ a competitor.** Customers name *our* products constantly (that's the deal). Only a
  **rival** vendor or a **build-in-house** intent is a threat. Don't flag a deal because "Tosca" appears.
- **Seller-mentioned competitors don't count.** An AE saying "unlike Selenium, Tosca…" is a pitch,
  not a threat. Look for the *customer* raising / preferring / already using the alternative.
- **A competitor already displaced ≠ a threat to this deal.** "We're replacing UFT with Tosca" is a
  *win* (we're the chosen tool), not a competitive risk — the incumbent is who we're beating.
- **Context sets severity**: "we also glanced at Katalon" (weak) vs "Katalon came in 30% cheaper and
  procurement prefers them" or "leadership wants us to standardize on our in-house Selenium framework"
  (strong, deal-threatening). Quote the line and let severity speak for itself.

---

## Competitor threat profiles — the color behind each name

Use these to turn a bare flag ("competitor: Katalon") into a sentence a sales leader can act on:
*what* the rival is, *why the customer would pick them* (the win reason), a *price signal*, how it
*reads in a call*, and *how we counter*. Threat level is a rough mid-2026 read — always let the actual
call wording set severity. Mindshare figures (PeerSpot, regression-testing category) are directional:
**Tosca ~27.5% · Katalon ~17.9% · UFT One ~13.9%**.

### Functional / UI automation — threatens **Tosca, Testim**

- **Katalon** *(high threat, price-led)* — Freemium Studio + paid Platform; adopters cite **30–40%
  lower cost** and fast onboarding. The dominant price/ease objection in mid-market and with teams
  balking at Tosca's licensing and learning curve. Weaker on cloud scale, enterprise governance.
  *Reads as:* "Katalon came in cheaper / easier for our manual testers." *Counter:* enterprise
  governance, SAP depth, model-based reuse and self-healing at scale (TCO, not sticker price).
- **OpenText UFT One / UFT Developer** *(usually a displacement WIN)* — ex–Micro Focus legacy
  incumbent (~$8k/seat list), broad tech but flagged for flakiness/maintenance. We normally *replace*
  it — only a threat on renewal inertia or an OpenText ELA. *Reads as:* "we're staying on UFT" (threat)
  vs "replacing UFT with Tosca" (**win — don't flag**).
- **UiPath (Test Cloud / Agentic Automation)** *(high threat, consolidation-led)* — most dangerous when
  it's *already in-house* as the RPA platform: the pitch is "consolidate testing into the automation
  platform we already own." Strong agentic/AI story, high satisfaction on complex flows; also fields
  **UiPath Impact Analysis** against LiveCompare. *Reads as:* "we already have UiPath, why add Tosca" /
  "running a UiPath-vs-Tosca bake-off." *Counter:* testing depth, model-based maintainability, SAP,
  governance — RPA ≠ purpose-built testing.
- **ACCELQ** *(rising enterprise challenger)* — codeless, GenAI "Live Model," aimed at
  business-logic-heavy / regulated apps; positions as easier than Tosca at enterprise scale.
- **Leapwork** *(no-code, business-tester led)* — the most *visually* accessible no-code experience;
  wins non-technical testers with slick, hands-on trials. *Counter:* enterprise scale + SAP + agentic.
- **AI-native self-healing pack — mabl, testRigor, Testsigma, Functionize** *(modern challengers)* —
  sell "AI-native, plain-English tests, auto-heal, low maintenance"; the "less brittle than
  model-based" pitch. Watch on greenfield / cloud-native teams. *Counter:* Vision AI + Agentic Test
  Automation match the AI story with enterprise scale and SAP the challengers lack.
- **Ranorex, SmartBear TestComplete** *(mid-market desktop/UI incumbents)* — cheaper, simpler;
  TestComplete shows up in SAP-adjacent shops. Often an incumbent to displace.
- **Selenium / Playwright / Cypress / Appium** — as a *product* comparison they're the "free"
  benchmark; as a *framework the team owns* they're the build-in-house threat. Decide by intent
  (see build-vs-buy) — Playwright is the rising favorite and has the lowest OSS TCO.

### SAP / packaged-app automation — threatens **Tosca, LiveCompare**

- **Worksoft (Certify)** *(the classic SAP rival)* — deep SAP/GUI heritage, frequently pushed by SIs
  (Accenture et al.) and procurement. *Reads as:* "procurement / our SI prefers Worksoft." *Counter:*
  one platform (test + change-impact + perf), Cloud ALM integration, model-based reuse.
- **Panaya (Smart Testing & Change Intelligence)** *(closest LiveCompare rival, also a Tosca alt)* —
  bundles test management + codeless automation + change-impact in one SAP-focused suite; Gartner ranks
  it a top-3 Tosca alternative. *Reads as:* "Panaya does impact analysis and testing in one tool."
  *Counter:* LiveCompare depth + Tosca automation + SAP-certified CIA in Cloud ALM.
- **SAP's own — Cloud ALM test automation, Signavio** *("free with SAP" objection)* — "it's included,
  why pay?" Note **SAP CIA is our LiveCompare** — not a competitor. *Counter:* Cloud ALM covers basics;
  Tricentis is the SAP-*recommended* depth (solution extension); SolMan EoL 2027-12-31 is the wedge.
- **Basis Technologies (Testimony)** — SAP robotic test automation, niche.

### Performance / load — threatens **NeoLoad**

- **Apache JMeter** *(the #1 build-in-house perf threat)* — OSS, dominant deployed share, free, huge
  plugin/protocol ecosystem. *Reads as:* "we already run JMeter / why pay when it's free." *Counter:*
  correlation & maintenance burden, no-code, Augmented Analysis, SAP protocols, MCP.
- **Grafana k6** *(fastest-rising perf threat)* — cloud-native, developer/CI-first, riding the Grafana
  observability ecosystem; both a commercial rival *and* a DIY (devs "own it"). *Counter:* enterprise
  protocols/SAP, analysis engine, governance vs a code-only dev tool. **Both LoadRunner and NeoLoad are
  losing share to k6/JMeter — the real fight is often NeoLoad vs OSS, not vs LoadRunner.**
- **OpenText LoadRunner / Professional PE** *(usually a displacement WIN)* — legacy incumbent, losing
  share; threat only on renewal inertia. *Reads as:* "renewing LoadRunner" (threat) vs "LoadRunner →
  NeoLoad" (**win**).
- **Gatling, BlazeMeter (Perforce), Locust** — mid-tier: Gatling code-first, BlazeMeter JMeter-cloud,
  Locust OSS/Python.

### Test management — threatens **qTest**

- **Xray & Zephyr (Scale)** *(the dominant qTest objection)* — Jira-native, cheapest path, "lives where
  the devs already are." *Reads as:* "why pay for qTest when Xray/Zephyr is right in Jira." *Counter:*
  enterprise governance, multi-project single-source-of-truth, cross-tool orchestration, compliance,
  Agentic Test Creation.
- **TestRail** *("gold standard" dedicated TM)* — best-in-class reporting/traceability, cheaper, strong
  mid-market. *Counter:* enterprise scale (30–100+ testers), governance, native Tosca/automation link.
- **Native Jira / Azure DevOps** — the "we'll just use Jira/ADO" good-enough objection.

### Data testing / observability — threatens **Data Integrity**

- **Monte Carlo** *(the "observability gap" threat)* — ML-driven data + AI **observability** (anomaly
  detection, column-level lineage, incident mgmt), **consumption-priced (~$50k small → $100–200k+
  enterprise)**. The wedge: we do data *testing/validation*, it does continuous *monitoring* — customers
  wanting "integrated observability + flexible pricing" pick it. *Reads as:* "Monte Carlo's pricing was
  more appealing and it does observability we don't." *Counter:* honest split — we win where they need
  validation *in the pipeline*; acknowledge the monitoring gap rather than fight it head-on.
- **Great Expectations** *(build-in-house data quality)* — OSS, code-first, free. *Counter:* TCO /
  maintenance, no-code, enterprise support.
- **QuerySurge, iCEDQ, Datagaps, Acceldata** — data-testing / observability rivals.

### Quality intelligence / coverage — threatens **SeaLights**

- **Codecov, JaCoCo (OSS), Launchable, Datadog CI Visibility** — JaCoCo/Codecov are the "we already
  have coverage tooling / it's free" objection; Datadog CI Visibility rides the Datadog platform.
  *Counter:* SeaLights does **test-impact analysis** (code change → which tests to run), not just a
  coverage percentage.

### Cloud device / browser grids — threatens **Device Cloud (Waldo)**

- **BrowserStack** — broadest real-device fleet (30k+ devices, 3,500+ combos), best manual/exploratory;
  often already in-house and *integrated* with Tosca — usually complementary, occasionally a "we
  already pay BrowserStack" objection.
- **Sauce Labs** — enterprise/CI-oriented, strong analytics, private device cloud.
- **LambdaTest** — cost-aggressive, fast parallel Selenium/Playwright grids.

---

## Why deals are lost here (for the narrative)

- **Price / OSS-is-free**: "Selenium/Playwright is free; why pay for Tosca?" — the classic
  build-vs-buy loss. Counter is **TCO of a home-grown suite**, and the numbers are concrete: over
  three years a Selenium/Playwright framework runs **~$450–550k** in ops + infra; the harness,
  reporters, waits and utilities a commercial tool bundles are a **20–30% tax on a senior engineer**;
  grid/cloud execution is **$8–20k/yr** once parallel runs are real; and **flaky tests eat ≥2.5% of
  developer time**. "Free" OSS is a staffing and maintenance bill, not a zero. (Playwright has the
  lowest OSS TCO — built-in parallelism/sharding — so it's the framework DIY teams increasingly pick.)
- **Incumbent inertia**: they already run UFT/Worksoft/JMeter and switching cost feels high.
- **Procurement/SI preference**: a system integrator or procurement pushing a rival they resell.
- **"Good enough" internal capability**: a strong internal QE team that believes it can build it.
- **SAP-specific**: Worksoft/Panaya are the credible SAP rivals; SAP's own Cloud ALM tooling is a
  "free with SAP" objection. (SAP Solution Manager mainstream maintenance ends **2027-12-31**, which
  is *driving* many S/4HANA testing deals — a tailwind, but also why customers weigh alternatives now.)

---

## Sources

Compiled from Tricentis product documentation and public market/competitor research (refreshed
2026-07-30). **Figures are directional market snapshots — verify against the live market before
quoting anything customer-facing.** Threat levels and win-reasons are analyst/market composites, not
Tricentis-official positioning.

Key research consulted:

- **Portfolio / acquisitions:** Tricentis newsroom & blog (SeaLights acquisition, ~$150M, Jul 2024;
  Testim ~$200M, 2022; Waldo, 2023); Tricentis "Agentic Test Automation in Tosca" blog.
- **Functional/UI + agentic:** PeerSpot regression-testing mindshare (Tosca/Katalon/UFT); Gartner Peer
  Insights & G2 (Tricentis vs Katalon, vs UiPath Agentic Automation); Functionize, Testsigma round-ups;
  ACCELQ "codeless test automation tools" audit; DEV "11 Agentic Testing Tools 2026"; Shiplight AI.
- **SAP:** Gartner (Panaya vs Tricentis); SAP Community "UiPath Impact Analysis vs LiveCompare"; CoreALM
  / Merito LiveCompare + Cloud ALM; TrustRadius/G2 Tosca alternatives (Panaya, Eggplant, Ranorex).
- **Performance:** QAInsights "LoadRunner vs NeoLoad 2026"; Vervali & Grafana "k6 vs JMeter"; Speedscale;
  FrugalTesting — both LR & NeoLoad ceding share to k6/JMeter.
- **Test management:** Slashdot/SourceForge TestRail vs Xray vs Zephyr vs qTest; qaskills.sh 2026.
- **Data observability:** Medium/Castordoc/Orchestra — Monte Carlo vs Great Expectations, Monte Carlo
  pricing (~$50k → $100–200k+).
- **Device cloud:** BrowserStack vs Sauce Labs vs LambdaTest (qaskills.sh, Autonoma, Bug0).
- **Build-vs-buy TCO:** Autonoma "Playwright vs Selenium 2026 ($216k hidden cost)"; ContextQA "test
  automation pricing / real TCO"; Stack Overflow / testomat.io framework comparisons.
