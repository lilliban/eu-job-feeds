# Decisions

Numbered, with the context, what else was considered, what was chosen, and what
it costs. The costs are stated because someone will inherit these choices and
deserves to know what they are paying for.

---

## 1. Personio is validated by its XML root element, never by its status code

**Context.** Every other provider answers a request for an unknown company with
a 404. Personio redirects to `www.personio.com` and returns its marketing site.
Captured while building this, from `zzznotarealcompanyzzz.jobs.personio.de/xml`:
a redirect to `https://personio.com` and 33 KB of HTML. The status was 429 in
that capture and 200 in others — neither says anything about whether the company
exists.

**Alternatives.** (a) Trust the status code. (b) Check the final URL after
redirects. (c) Parse the body and require the Personio root element.

**Decision.** (c). `PersonioConnector._parse_root` returns the root only if the
document parses as XML *and* its root tag is `workzag-jobs`. Anything else means
"no board here".

**Consequences.** A connector trusting the status code would have declared that
every company in the world has a Personio board, and the dataset would have
filled with marketing HTML under real company names. The cost is that a future
change to Personio's XML root breaks the connector outright — which is the right
failure: loud and total, rather than quiet and wrong. The response body is also
read in full before it can be rejected, so a 1.7 MB marketing page is downloaded
to be discarded. A cheap substring pre-check avoids the parse but not the
download.

The same function refuses documents containing `<!DOCTYPE` or `<!ENTITY`.
`xml.etree` expands internal entities, and a job board has no reason to declare
any.

---

## 2. The canonical company name lives in the registry, never in a connector

**Context.** Connectors know slugs. Greenhouse says `datadog`; a person searching
the dataset types `Datadog`. Postings filed under a name nobody types are
postings nobody finds.

**Alternatives.** (a) Derive the name from the slug (`slug.title()`). (b) Use
whatever display name the provider returns. (c) Curate it in the registry.

**Decision.** (c). `registry/companies.yaml` holds `name`, and `build_posting`
takes `company_name` as a required argument. `Registry.name_for` falls back to
the raw slug — never a title-cased guess — when a board is unknown.

**Consequences.** (a) fails on the cases that matter: `BoschGroup` becomes
"Boschgroup" and `amazingcarecareers` becomes "Amazingcarecareers". (b) is
inconsistent — only Greenhouse, Recruitee and Workable return a name at all, and
they disagree about formatting ("SmartRecruiters Inc" vs "SmartRecruiters").
The cost is real: **adding a company is a manual step.** Discovery can propose
entries, but a human names them. That is the intended trade.

---

## 3. The lifecycle acts only on complete reads

**Context.** A posting closes after two consecutive reads that did not return
it. The failure mode is comparing the archive against a read that was not the
whole catalogue — a partial page walk, a timeout, or a filtered subset — which
closes everything outside the comparison.

**Alternatives.** (a) Count a miss whenever a posting is absent. (b) Count a miss
unless the request threw. (c) Have connectors declare explicitly whether they
walked the entire feed, and act only on that.

**Decision.** (c). `FetchOutcome.complete` is set by each connector, and
`lifecycle.merge` returns the archive untouched when it is false — no misses
counted, no timestamps moved, nothing closed.

`complete` is true for a clean 404 (the board genuinely does not exist) and for
an empty-but-valid response such as Lever's `200 []`. It is false for 5xx, 429,
transport errors, an unexpected payload shape, and a paginated walk that ended
short of `totalFound`.

**Consequences.** A provider having a bad day costs nothing: the data goes stale
rather than wrong. The cost is that a company that genuinely removes every
posting while its provider is also erroring keeps stale postings until a clean
read happens. Stale beats deleted.

Note that `complete` deliberately says nothing about whether advert *text* was
fetched — see #7.

---

## 4. Postings are keyed by the provider's id, not by `content_hash`

**Context.** The contract defines `content_hash` over title, company and the
first 500 characters of requirements. Location is not in it. Measured on
Datadog's real board: **29 hashes are shared by 83 of the 429 postings** — the
same role posted in several cities. `Commercial Account Executive` in Seoul,
Sydney and Denver is one hash and three adverts.

**Alternatives.** (a) Key the archive by `content_hash` and let the file hold one
row per hash. (b) Key by the provider's id and allow duplicate hashes in the
file. (c) Change the hash to include location.

**Decision.** (b), with (c) rejected outright — the hash is in production in the
consumer and cannot change. `lifecycle.identity` keys on `provider:external_id`,
and `external_id` is stored in each posting as a new field, which the contract
permits.

**Consequences.** With (a), 83 of Datadog's postings would collapse into 29 and
the dataset would report 375 openings where 429 exist; worse, which city
survived would depend on read order. With (b) **a consumer may see two rows
sharing a `content_hash`** — which is exactly what the contract says that field
is for, since it calls it the deduplication key. Files are slightly larger.

`external_id` is optional on load with an empty default, and `stored_identity`
falls back to `source_url`, so an archive written before the field existed keeps
matching instead of being treated as entirely new and closed.

---

## 5. Failed discoveries are remembered for 30 days

**Context.** Knowing a company has no public ATS is a useful result: it saves
seven probes on every run. Recording it permanently means a company that adopts
Greenhouse next month is never found again.

**Alternatives.** (a) Do not remember. (b) Remember permanently. (c) Remember
with an expiry.

**Decision.** (c), 30 days, in `registry/negative.json`.

Two answers are never written down:

- `ProbeResult.ERROR` — a timeout or a 429 answered nothing. Caching it would
  turn a moment's outage into a month of silence. Personio 429s readily, so this
  is not hypothetical.
- `ProbeResult.AMBIGUOUS` — see #6.

**Consequences.** A company adopting an ATS is found within 30 days rather than
immediately. The file grows by one line per confirmed miss and is purged of
expired entries on each discovery run.

---

## 6. SmartRecruiters can never prove a company absent

**Context.** An unknown company and a company with no open roles both return
`200` with an empty list. There is no way to distinguish them.

Separately, and undocumented: **the company identifier is case-sensitive.**
`bosch` returns 0 postings; `BoschGroup` returns 4713. Both are `200`. This is a
silent failure of exactly the kind the other traps are.

**Alternatives.** (a) Treat an empty list as "not found". (b) Treat it as
"found". (c) Add a third answer.

**Decision.** (c). `ProbeResult.AMBIGUOUS` is returned, surfaced to the operator,
and never cached. Slugs are treated as opaque and case-preserving everywhere —
in the registry, in URLs, and in filenames (`store._safe_stem` only replaces
characters a path cannot hold).

**Consequences.** SmartRecruiters companies must be confirmed by a person. That
is slower, and it is the only honest option available.

---

## 7. Advert text is fetched under a budget, and that does not affect `complete`

**Context.** SmartRecruiters and Workday return no advert text in their list
responses. One extra request per posting means 4713 requests for Bosch and 2000
for NVIDIA, on every run.

**Alternatives.** (a) Fetch every body every run. (b) Never fetch bodies.
(c) Fetch only bodies not already stored, under a per-run ceiling.

**Decision.** (c). `already_detailed_ids` returns the ids whose text is already
on disk; connectors skip those and stop after `DETAIL_BUDGET` (250 for
SmartRecruiters, 200 for Workday).

Crucially, this does **not** change `complete`. The list walk decides which
postings exist; a posting whose text has not been fetched is still present, and
the lifecycle must not close it.

**Consequences.** A first run against a large board leaves most postings without
`description` — NVIDIA showed 10% coverage on its first pass — and they fill in
over subsequent runs. Steady state costs almost nothing. A posting whose text
the company later edits is not re-read, so **descriptions can go stale**; that
is the real cost, and it is accepted because the title, location and URL stay
fresh and those are what a search filters on.

---

## 8. Workday is not a `Connector`

**Context.** Every other provider identifies a company with one opaque slug.
Workday needs three independent parts: tenant, data-centre number (`wd1`…`wd5`),
and career-site name. None is derivable from the others. A wrong site name on the
right tenant answers 422; some tenants answer 401 regardless. Both were observed
on real tenants (`sap`, `adidas`, `siemens`).

**Alternatives.** (a) Encode the triple into one slug string and parse it back.
(b) Give Workday its own type and code path.

**Decision.** (b). `WorkdayTarget` is a frozen dataclass; `WorkdayConnector` does
not inherit from `Connector`, and `CONNECTORS` does not contain it.

Discovery for Workday is **manual only**. Brute-forcing `wd1`–`wd5` against a
list of common site names is dozens of requests per company with an almost
always negative result, and it would look like abuse.

**Consequences.** The pipeline has one `if entry.provider == "workday"` branch.
That is a fair price for not smuggling a tuple through a string. Workday coverage
grows only as fast as someone adds entries by hand.

Also of note: **`limit` is capped at 20** — anything larger is a 400 — so
NVIDIA's 2000 openings are 100 POSTs before any detail call.

---

## 9. Only the four contract currencies are ever emitted

**Context.** The contract lists `EUR | USD | GBP | CHF | null`. European adverts
also quote SEK, NOK, PLN and CZK.

**Alternatives.** (a) Emit whatever currency was found. (b) Emit the amounts with
a null currency. (c) Emit nothing when the currency is outside the contract.

**Decision.** (c). `extract_salary` recognises the other currencies precisely so
it can *refuse* them rather than mislabel them, and `build_posting` clears
`salary_min`/`salary_max` whenever `salary_currency` is null.

**Consequences.** Nordic and Polish salaries are dropped even when clearly
stated. (a) risks a consumer comparing 500000 SEK against 50000 EUR as if they
were the same unit; (b) produces numbers with no unit, which is worse than
silence. Adding a currency is a one-line change once the consumer agrees.

---

## 10. Hourly and daily rates are not annualised; monthly rates are

**Context.** The contract has no field for a pay period, so every figure has to
mean the same thing.

**Decision.** Yearly figures pass through; monthly are multiplied by 12; weekly
by 52 when the provider states the period as structured data. **Hourly and daily
rates produce nothing.**

**Consequences.** Converting an hourly rate needs contracted hours, which no
provider reliably gives — a part-time role would be inflated to a full-time
salary. Monthly is unambiguous, and Recruitee states the period in a structured
field (`{"min": "2850", "period": "month", "currency": "EUR"}`), so nothing is
inferred. Contract and freelance roles quoted by the day are simply absent from
salary filters.

---

## 11. One file per company, stable byte-for-byte, with a monthly orphan branch

**Context.** A single JSON document rewritten every run makes each commit a full
copy of the dataset. Four runs a day for a year is a repository nobody wants to
clone.

**Alternatives.** (a) One document. (b) One file per company. (c) One file per
company plus periodic history pruning.

**Decision.** (c). `data/{provider}/{slug}.json`, fixed field order
(`models.FIELD_ORDER`), postings sorted by `(content_hash, source_url)`, `indent=2`,
trailing newline. `write_company` compares against what is on disk and returns
False when nothing changed, so the workflow commits only real differences.

History is collapsed monthly by `prune-history.yml`, which first publishes the
current dataset as a Release asset and then rebuilds the branch as an orphan with
a single commit.

**Consequences.** A run that changes nothing produces no commit at all. The cost
of pruning is that **per-commit history is lost**: "when exactly did this posting
appear" is answerable only to the granularity of the monthly snapshots. The
snapshots exist precisely so the data itself is never lost. Anyone tracking the
branch must expect a force-push once a month.

The sort key includes `source_url` because `content_hash` is not unique (#4);
sorting on the hash alone would leave the order of same-role-different-city
postings undefined and produce spurious diffs.

---

## 12. `first_seen_at` and `last_seen_at` are truncated to the day

**Context.** `last_seen_at` is rewritten for every posting on every successful
read. At full precision and four runs a day, **every company file changes four
times a day even when no posting does** — which defeats the entire reason for
one-file-per-company, and buries real changes in noise.

**Alternatives.** (a) Full precision, accept the churn. (b) Update the field only
when something else changed. (c) Truncate to midnight UTC.

**Decision.** (c). `models.seen_stamp` returns `2026-08-02T00:00:00+00:00`.

**Consequences.** (b) was rejected because it makes the field lie: it would no
longer mean "the last read that saw this". (c) keeps the meaning, to the day, and
makes an unchanged company file byte-identical between runs on the same day —
verified: a second consecutive run reports `Company files changed: 0`. The value
remains a full ISO 8601 datetime, so a consumer parsing it as one is unaffected.
The cost is that time-of-day is not recoverable; the file-level `updated_at`
records the actual read time.

---

## 13. Rate limiting is per host, with Personio slowed deliberately

**Context.** Greenhouse and Ashby are unrelated services. A global limiter would
make a request to one wait on the other, multiplying the run time by the number
of providers for no one's benefit.

**Decision.** `RateLimitedClient` keeps one limiter per host, defaulting to 0.34 s
between requests. Recruitee and Personio give every company a subdomain, so those
collapse onto the registrable suffix — limiting by full hostname would be no limit
at all. Each Workday tenant is a separate deployment and keeps its own limiter.

**Personio is spaced at 3 seconds.** Roughly two dozen requests at 2 s spacing was
enough to get *every* slug 429ed for several minutes, including ones that had
worked moments earlier. This was measured, not assumed.

Retries use full jitter (`random.uniform(0, min(2·2ⁿ, 30))`) and honour
`Retry-After`. Jitter matters because several connectors hit one host in
parallel and a fixed backoff would have them all retry on the same tick.

**Consequences.** Personio is the slowest provider by a wide margin. Only 408,
425, 429 and 5xx are retried; 401/403/404/422 are real answers and are returned
to the connector to interpret.

---

## 14. `requirements_raw` is recovered by splitting the body at a heading

**Context.** Greenhouse, Ashby, Lever and Workday ship one undivided body.
Without a split, `requirements_raw` would be null everywhere except Recruitee
and Workable — and since the contract defines `content_hash` over the
requirements, an always-empty third component would collapse every same-titled
role of a company into one hash.

**Alternatives.** (a) Leave it null. (b) Copy the description into it. (c) Split
at the first requirements-style heading, in several languages.

**Decision.** (c), with (b) as the fallback for the hash only when no heading
exists. Measured: 402 of Datadog's 429 bodies contain such a heading. Coverage
went from 0% to 93% on Greenhouse and 85% to 95% on Ashby.

**Consequences.** This is a regex over prose and it will mis-split sometimes.
When no heading matches, the body stays whole as the description and
`requirements_raw` is null rather than guessed. Two findings came out of building
it and are worth recording:

- NVIDIA's Workday adverts head their requirements `What we need to see:`, which
  no generic pattern would include.
- **Typographic apostrophes are more common than ASCII ones** — 67 vs 50 in one
  Recruitee board. Matching only `'` silently left `requirements_raw` null on 12
  of Ashby's 18 affected postings. Every pattern now accepts `’‘´'`.

---

## 15. Salary and experience extraction refuse by default

**Context.** A wrong salary is worse than a missing one: a consumer filtering on
`salary_min >= 40000` silently drops the right jobs and keeps the wrong ones,
with no error anywhere.

**Decision.** A number becomes a salary only when an explicit currency marker is
attached, *and* it falls in a plausible annual band (8 000 – 2 000 000), *and* it
is not followed by a counting noun. `tra 500 e 1000 clienti` fails all three;
`tra 50.000 e 100.000 clienti` fails the last one. Years of experience require an
experience keyword within about 70 characters, so `founded 3 years ago` and `a 5
year plan` produce nothing.

Structured provider data always wins over regex: Personio's `yearsOfExperience`
(`"7-10"`) and Recruitee's salary object are used directly.

**Consequences.** Coverage is lower than a permissive parser would report. That
is the intended trade — see the coverage table in the README, which is measured
rather than estimated. `RAL 35.000 - 45.000` without a currency yields nothing,
even though a human would read it as euros, because inferring the currency from
the language of the advert is a guess.

---

## 16. No LLM anywhere

**Context.** The normalisation rules are, in order of cost: structured provider
data, then regex, then nothing.

**Decision.** No model is called. A field that cannot be derived is null.

**Consequences.** Some fields stay sparse — `languages` in particular sits around
10% on English-language boards. An LLM would raise that number and make it
unauditable, non-deterministic between runs (breaking the byte-stability in #11),
and dependent on a paid API, which the zero-cost constraint forbids. A null is
honest; a plausible invention is not.

---

## 17. Workday's `total` is not trusted as proof a walk is complete

**Context.** Found on NVIDIA, the only Workday tenant registered: `total` on
the list endpoint reported 2000 while the tenant's real catalogue held 3237.
Every offset past 2000 kept answering with a full, non-empty page — never the
empty page a genuinely exhausted walk gives — just recycling postings already
collected. The connector took `len(postings) >= total` as proof the walk was
done, reported `complete=True` on 2000 of 3237 postings, and the lifecycle
(#3) went on to close the other 1237 across two runs as if they no longer
existed. Verified live: a "closed" posting's own URL answered `200`, and its
`externalPath` was absent from every page the connector could reach, capped or
not — the index behind the endpoint was silently capping its own result
window below what `total` claimed.

**Alternatives.** (a) Trust `total` as before. (b) Stop paginating only on an
empty page, ignoring `total` entirely. (c) Trust `total`, but confirm it with
exactly one extra request once reached.

**Decision.** (c). Once the walk believes `len(postings) >= total`, `fetch`
sends one more page request. An empty answer confirms `total` was honest,
`complete=True`. A non-empty answer means the index is capping results below
the real catalogue, and the outcome is reported `complete=False` — per #3, an
incomplete read leaves the archive untouched rather than closing what the walk
could not reach.

**Consequences.** (b) would work but pays for it on every one of the many
small Workday tenants that terminate cleanly via `total`, re-fetching pages
that add nothing new until an empty one finally shows up. (c) costs exactly
one extra request, only on tenants where `len(postings)` reaches `total` at
all. The real cost is that a tenant this large, once capped, can never be
walked to completion through this endpoint: NVIDIA's dataset is honestly stuck
at whatever the index will still hand back, correct but permanently partial,
until a facet-partitioned walk (splitting the query by `jobFamilyGroup`, each
slice its own sub-2000 window) is built — not done here, and out of scope for
the single tenant it would currently matter for.

The 1237 postings wrongly marked closed by this bug, in the two runs before
the fix, were corrected by hand in `data/workday/nvidia.json` once found:
`is_closed` and `consecutive_misses` reset, since the lifecycle itself cannot
retroactively undo a closure it was wrongly told to make.
