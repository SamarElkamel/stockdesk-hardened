# StockDesk: hardened delivery chain

Review and hardening of StockDesk's delivery chain (pipeline, image, compose, Kubernetes) before it opens to suppliers.
**Fictional:** every name, registry, token and range here is invented. No real secret appears anywhere.

| Read this | For |
|---|---|
| [RISKS.md](RISKS.md) | Every risk found, ranked and justified, with its status (fixed / reduced / left), and **what to do about the secrets already exposed** (order, owners). |
| [THREAT-NOTE.md](THREAT-NOTE.md) | One page: assets, attackers, 4 attack paths, which change cuts each one, and what remains. |
| [DESIGN-NOTE.md](DESIGN-NOTE.md) | One page: understanding, choices, tests, what I cut, AI use, next day. |

## How to read the diff

The first commit is the **untouched original**; the second holds all my changes.

```
git log --oneline
git diff HEAD~1 --stat
git diff HEAD~1 -- .github/workflows/ci.yml Dockerfile k8s/deployment.yaml
```

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | `pull_request` instead of `pull_request_target`; read-only token; no hard-coded or echoed token; tests block; gitleaks, trivy config and image scans; SBOM; deploy only from `main`, through the `test-cluster` environment, **by digest**, with plain `kubectl` instead of the third-party action. |
| `Dockerfile` | `node:24-alpine` instead of `latest`; copies only `package*.json` and `src/`; `npm ci`; non-root numeric user; no inspector or port 9229; healthcheck. |
| `.dockerignore` (new) | Allow-list: `.env`, `k8s/`, `.git/` can no longer reach an image. |
| `k8s/deployment.yaml` | Own namespace; non-root, read-only FS, no capabilities, seccomp, no SA token; requests and limits; readiness `/health` + liveness `/livez`; 2 replicas; image set by CI; Service: HTTP only, source ranges. |
| `k8s/configmap.yaml`, `networkpolicy.yaml`, `pdb.yaml`, `kustomization.yaml` (new) | Non-secret settings; default-deny + DNS/DB egress; disruption budget; what CI applies. |
| `k8s/admin/` (new) | Applied **once by a cluster admin**: namespace with Pod Security `restricted`, and the namespace-scoped CI credential. |
| `k8s/secret.yaml`, `.env` (deleted) | Secrets out of Git; `.env.example` holds example values. |
| `docker-compose.yml`, `docker-compose.dev.yml` | Values from an untracked `.env`; no published DB port; app on loopback by default; debug tools only in the opt-in dev file. |
| `src/server.js`, `test/` | Secrets no longer logged; `/livez` route; 1 KiB body limit; SIGTERM handling; 4 new tests. Port 3000 and env var names unchanged. |
| `.github/dependabot.yml`, `.github/CODEOWNERS`, `.gitattributes`, `package-lock.json`, `tools/check_consistency.py` (new) | Update proposals; review rule; LF line endings; lockfile for `npm ci`; offline checks. |

## Run and test

Node.js 18 or later, no dependency.

```
npm test                               # 9 tests
node src/server.js                     # http://localhost:3000/api/products, /livez, /health
pip install pyyaml && python tools/check_consistency.py   # YAML parses + cross-file consistency
```

Compose (needs Docker; I did not run it):

```
cp .env.example .env                   # put your own values in it
docker compose up -d                                                   # test-server / local
docker compose -f docker-compose.yml -f docker-compose.dev.yml up      # developer only: inspector on 127.0.0.1:9229
```

## Verification: what I checked and what I could not

**Checked (on Windows, Node 18.20.6, PyYAML 6.0.3):**
- `npm test`: **9/9 pass** (the 5 original tests plus `/livez`, the body limit, a valid adjust, and the log redaction).
- The YAML check from the original README, extended to every YAML file: **`YAML ok`**.
- `tools/check_consistency.py`: **all checks pass**. It checks that the files agree with each other (port 3000 everywhere, Dockerfile UID = pod `runAsUser`, image name in CI = manifests = kustomization, labels and selectors, namespace, probe paths are real routes, CI Node version = Dockerfile, every env var the app reads is provided by Kubernetes and compose), and that the bad patterns are gone (`pull_request_target`, `write-all`, `latest`, 9229, old secret values; the old values are read from the baseline commit, never written in the script). I made sure it can fail: with `EXPOSE 3000 9229` and `runAsUser: 0` put in on purpose, it reported 3 failures.
- A manual run of the server: `/livez` answers, and the startup log shows `"dbPassword":"[set]","apiKey":"[set]"`.
- `npm ci --ignore-scripts` works with the new lockfile.

**Not checked (no Docker, cluster or network access here):**
- I did **not** run `docker build`, `docker compose up` or `docker compose config`, so the image and the compose file are untested. That includes the busybox `wget` used by `HEALTHCHECK` and the app starting with `read_only`.
- I did **not** run hadolint, actionlint, kubeconform, trivy or gitleaks locally, and the pipeline has never run.
- I did **not** run `kubectl apply --dry-run=server` or `kubectl kustomize`. Not verified: Pod Security `restricted` accepting the pod, the CNI enforcing NetworkPolicy, `kustomize` and `kubectl` being on the runner, the CI Role being sufficient for `apply` and `rollout status`.
- Tests ran on Node 18, not on Node 24 (the version used in CI and the image).
- SIGTERM handling is untested: Windows has no POSIX signals.
- **Marked TODO verify, not looked up:** action commit SHAs, image digests (node, postgres, gitleaks, trivy), scanner versions (explicit placeholders), the current action majors, the PostgreSQL major on the test server, the office and VPN ranges, where the cluster's DB runs, and the CODEOWNERS team.

## Kubernetes secrets (no vault)

Secrets are never in Git. A cluster admin creates them once, from a file kept outside the repository:

```
# ~/stockdesk-secret.env (chmod 600, never committed):  DB_PASSWORD=...  API_KEY=...
kubectl -n stockdesk create secret generic stockdesk-env --from-env-file="$HOME/stockdesk-secret.env"
shred -u "$HOME/stockdesk-secret.env"
```

Next step (not done): **Sealed Secrets**. Secrets are encrypted with the controller's public key, the encrypted file can safely live in Git, and only the in-cluster controller can decrypt it. It is free, needs no vault, and fits a GitOps flow. Also check that etcd encryption at rest is enabled.

## CI credential (replaces the current KUBECONFIG)

```
kubectl apply -f k8s/admin/                                   # namespace + ci-deployer SA/Role/RoleBinding
TOKEN=$(kubectl -n stockdesk create token ci-deployer --duration=720h)   # the API server may cap the duration (TODO verify)
kubectl config --kubeconfig=ci-kubeconfig set-cluster shared --server=https://<api-server> --certificate-authority=<ca.crt> --embed-certs=true
kubectl config --kubeconfig=ci-kubeconfig set-credentials ci-deployer --token="$TOKEN"
kubectl config --kubeconfig=ci-kubeconfig set-context ci --cluster=shared --user=ci-deployer --namespace=stockdesk
kubectl config --kubeconfig=ci-kubeconfig use-context ci
gh secret set KUBECONFIG --env test-cluster < ci-kubeconfig && shred -u ci-kubeconfig
```

Renew the token before it expires (calendar reminder). Better later: short-lived OIDC credentials, if the cluster supports them.

GitHub settings the files rely on (repo admin): the **`test-cluster` environment** (deployment branch `main` only, one required reviewer, environment secrets `DEPLOY_TOKEN` and `KUBECONFIG`, repo-level copies deleted); **branch protection on `main`** (PR, 1 approval, Code Owners, required checks `test`, `static-checks`, `build`); fork-PR workflows need approval and get no secrets.

## Migration runbook (order matters)

1. **Hotfix now:** remove port 9229 from the live Service. Check the port index first with `kubectl -n default get svc stockdesk -o yaml`, then `kubectl -n default patch svc stockdesk --type=json -p='[{"op":"remove","path":"/spec/ports/1"}]'`.
2. Close the leaks and rotate the secrets: RISKS.md, Steps 0 and 1.
3. Cluster admin: `kubectl apply -f k8s/admin/`, create the `stockdesk-env` Secret, and build the CI credential.
4. Fill in the TODOs that block a deploy: office and VPN ranges (`k8s/deployment.yaml`), `DB_HOST` and NetworkPolicy egress (`k8s/configmap.yaml`, `k8s/networkpolicy.yaml`), scanner versions (`ci.yml`).
5. Merge to `main`, approve the `release` job, and check `kubectl -n stockdesk get pods` and `curl http://<new-LB-address>/health`.
6. **The LoadBalancer address changes** (new Service). Tell staff and the owner of the internal script, or put a DNS name in front.
7. **Delete the old objects**, which `apply` will not remove: `kubectl -n default delete deployment/stockdesk service/stockdesk secret/stockdesk-env`.
