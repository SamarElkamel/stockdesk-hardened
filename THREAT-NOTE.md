# StockDesk threat note (delivery chain)

**What we protect, in order:** (1) the **shared test cluster and the image registry**, because a foothold there reaches other teams' workloads and decides what code runs; (2) the **secrets** that open them (KUBECONFIG, registry token, DB password, API key); (3) the **integrity of stock levels**, since wrong stock means wrong orders and shipments; (4) the **availability** of StockDesk for staff and, next month, for suppliers.

**Who could attack:** any of the **~40 repository readers**, including an external contractor (curious, careless, or with a stolen account); the **departed contractor**, whose access was never removed; **anyone who can reach the LoadBalancer**, meaning the office and VPN for sure and the Internet unless proven otherwise; next month, **suppliers or a stolen supplier key**; and a **compromised upstream** (action, base image, scanner).

## Attack paths through the delivery chain

| # | Path | Changes that cut it | What remains |
|---|---|---|---|
| P1 | **Read the repo → use the leaked values.** `DEPLOY_TOKEN` → push a malicious `stockdesk:latest` → the next deploy runs it. Or DB password → PostgreSQL on the test server's public 5432. | Secrets removed from every file; gitleaks in CI; the deploy uses the **digest** of the image CI just scanned, so a pushed `latest` is never run; push credential only in a `main`-only environment; DB port no longer published. | **The values work until they are rotated** (RISKS.md, Steps 0–1). Git history and every clone still contain them. |
| P2 | **Open a pull request (any reader, from an org fork).** `pull_request_target` runs the PR's code and install scripts with `write-all` and the registry token; or a PR **title** containing shell commands. → Token stolen, image pushed, then KUBECONFIG (cluster). | `pull_request` trigger; `contents: read` token; `npm ci --ignore-scripts`; no event text in shell; secrets only in the `test-cluster` environment (main only, required reviewer); CODEOWNERS on the delivery files. | A maintainer approving a malicious change. Branch protection and the environment are **settings** that still have to be applied. |
| P3 | **Network → Node inspector on port 9229** (LoadBalancer, test server) → JavaScript runs inside the pod **as root** → read env secrets, use the mounted service-account token, move laterally from `default` in the shared cluster. | Inspector removed from the image, Deployment and Service (dev-only, loopback); non-root UID, read-only FS, no capabilities, no SA token; own namespace with Pod Security `restricted`; NetworkPolicy default-deny (egress: DNS + DB only); `loadBalancerSourceRanges`. | The **old Service in `default`** keeps 9229 open until deleted (hotfix, runbook). NetworkPolicy only works if the CNI enforces it. Bugs in the app itself. |
| P4 | **Departed contractor pushes to `corvane-labs/k8s-deploy-action@main`** → the next deploy hands his code the KUBECONFIG (assumed cluster-admin) → the whole shared cluster. | Action removed; plain `kubectl` and GitHub-owned actions only (SHA pinning TODO); new **namespace-scoped** CI credential that cannot read Secrets or change RBAC. | **His accounts must be removed** and the old KUBECONFIG revoked (human action). Other repos may still use that action. |

## Opening to suppliers next month: what I would put in front

Do **not** expose this shared test cluster. Use a **separate production environment** with an **Ingress + TLS** (cert-manager, Let's Encrypt), rate limiting at the Ingress, and suppliers limited to **read-only** `GET /api/products` with **one key per supplier** (revocable, logged). `POST .../adjust` stays internal: office and VPN only, or a separate internal Service. If suppliers must adjust stock, per-client keys plus an audit log of who changed what become a **blocker**, not an improvement.

## What remains after my changes

Nothing was **executed**: no `docker build`, no cluster, no CI run (README, "Verification"). Several settings live **outside the files** (branch protection, environments, fork-PR policy, the contractor's access). The API authentication model (R10), TLS, and secret management beyond `kubectl create secret` (Sealed Secrets) are **not done**. Digests and action SHAs are marked **TODO verify**.
