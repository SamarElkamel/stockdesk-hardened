# Design note

**Understanding and assumptions.** The real question is not "is the Dockerfile tidy?" but "can StockDesk be exposed to outsiders next month without handing them, or anyone, the cluster?" Reading the files, the delivery chain was more dangerous than the app: anyone on the network could run code through the inspector, any PR author could run code with the deploy secrets, and a departed contractor controls the deploy action. I assumed the worst wherever nobody could confirm (RISKS.md, A1–A7): the secrets are live and stolen, KUBECONFIG is cluster-admin, the LoadBalancer is public, the contractor still has access. My recommendation for suppliers: read-only, one key each, through a separate production environment behind TLS.

**Architecture.** PR: test + secret/config scan + build + image scan + SBOM, with no secrets. `main`: the same, then one gated `release` job that pushes the scanned image and deploys it **by digest** with plain `kubectl`, using a namespace-scoped credential. The workload runs non-root with a read-only FS in its own namespace with Pod Security `restricted`, default-deny NetworkPolicy, probes split into liveness/readiness, 2 replicas and a PDB. Secrets are created out of band. A small script checks that the files agree with each other.
*Hesitation 1: moving to a dedicated namespace.* It gives real isolation (RBAC, NetworkPolicy, Pod Security) but changes the LoadBalancer address and breaks the short `db` DNS name, and `apply` leaves the old exposed objects behind. I kept it, because the CI credential can only be scoped to a namespace if StockDesk has one, and I documented the migration (README runbook, R16).
*Hesitation 2: readiness on the DB and 2 replicas.* Readiness on `/health` means a DB outage takes the Service down. That is the correct signal if the real app needs PostgreSQL, but this demo serves from memory, where each pod would even keep its own stock. I followed the code comment ("the real application reads PostgreSQL") and wrote down the fallback (1 replica, no PDB).

**Tested / not tested.** Tested: `npm test` 9/9 (4 new tests), every YAML file parses, a consistency script (port, UID, image, labels, namespace, probe routes, env vars, no old secret value), which I mutation-tested so I know it can fail, and a manual server run. Not tested: `docker build`, compose, any scanner or linter, `kubectl` dry-run, the pipeline itself, Node 24, SIGTERM. Action SHAs, digests and scanner versions are **TODO verify**. I had no network access to look them up and did not want to invent them.

**What I cut, and why** (also my answer to "what did you not have time for"):
- The API authentication model (per-supplier keys, scopes, audit log, `timingSafeEqual`): it changes the API contract and needs the CTO's decision first.
- TLS / Ingress / the production environment: recommended, not designed.
- Sealed Secrets: described, not installed.
- Distroless image: could not build or test it.
- OIDC instead of a long-lived CI token.
- Image signing (cosign) and admission policies.
- Real SHAs and digests.
- A Git history rewrite: it has to wait until after rotation.
- `src/db.js` was not touched.

I prioritised what turns "someone could" into "anyone can" (R1–R5) over completeness.

**AI use.** I used Claude Code (Anthropic, Claude Opus 5.5) as a pair throughout. It read the repository, proposed the risk list and ranking, drafted the hardened files, tests, checker and documents, and ran the local checks. I checked its output by running the tests and the YAML parser, by making the consistency script fail on purpose, and by refusing anything it could not look up: SHAs, digests and versions are TODO, not invented. Two corrections came out of that review: placeholder scanner versions instead of remembered ones, and the checker reading old secrets from Git instead of embedding them. **[Candidate: add here what you reviewed and changed yourself, and the time you spent.]**

**With one more day.** Run everything for real: `docker build`, hadolint, trivy, gitleaks over the full history, actionlint, kubeconform, and `kubectl apply --dry-run=server` on a kind cluster with Calico to prove Pod Security and NetworkPolicy. Look up and pin every SHA and digest. Then implement per-supplier read-only keys with an audit log, and write the supplier-facing Ingress/TLS manifests.
