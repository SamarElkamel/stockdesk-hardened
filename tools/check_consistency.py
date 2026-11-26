"""Offline checks for the StockDesk delivery files (no Docker, no cluster needed).

    python tools/check_consistency.py        (needs PyYAML: pip install pyyaml)

1. Every YAML file parses.
2. The pipeline, the Dockerfile and the Kubernetes manifests agree with each other
   (port, user, image name, labels, namespace, probe routes, Node version, env var names).
3. Known bad patterns are gone (pull_request_target, write-all, "latest", 9229, old secret values).
It checks consistency, not behaviour: it does not replace docker build, a real cluster or a CI run.
"""
import base64
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
errors = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        errors.append(msg)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def load_all(rel):
    return [d for d in yaml.safe_load_all((ROOT / rel).read_text(encoding="utf-8")) if d is not None]


print("1. YAML parses")
yaml_files = sorted(
    p.relative_to(ROOT).as_posix()
    for p in list(ROOT.rglob("*.yml")) + list(ROOT.rglob("*.yaml"))
    if ".git" not in p.parts and "node_modules" not in p.parts
)
docs = {}
for rel in yaml_files:
    try:
        docs[rel] = load_all(rel)
        check(True, f"{rel} ({len(docs[rel])} document(s))")
    except yaml.YAMLError as e:
        check(False, f"{rel}: {e}")
if errors:
    sys.exit(1)

print("2. Cross-file consistency")
dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
server_js = (ROOT / "src/server.js").read_text(encoding="utf-8")
ci = docs[".github/workflows/ci.yml"][0]
kust = docs["k8s/kustomization.yaml"][0]
by_kind = {}
for rel in kust["resources"]:
    check((ROOT / "k8s" / rel).exists(), f"kustomization resource k8s/{rel} exists")
    for d in docs.get(f"k8s/{rel}", []):
        by_kind.setdefault(d["kind"], []).append(d)

dep = by_kind["Deployment"][0]
svc = by_kind["Service"][0]
cm = by_kind["ConfigMap"][0]
pod = dep["spec"]["template"]["spec"]
ctr = pod["containers"][0]
labels = dep["spec"]["template"]["metadata"]["labels"]

expose = re.findall(r"^EXPOSE\s+(.+)$", dockerfile, re.M)
check(expose == ["3000"], f"Dockerfile exposes only 3000 (found {expose})")
check([p["containerPort"] for p in ctr["ports"]] == [3000], "container exposes only port 3000")
check(cm["data"]["PORT"] == "3000", "ConfigMap PORT is 3000")
check(svc["spec"]["type"] != "LoadBalancer" or "loadBalancerSourceRanges" in svc["spec"],
      "a LoadBalancer Service restricts its source ranges")
check([p["targetPort"] for p in svc["spec"]["ports"]] == ["http"], "Service only forwards the http port")

user = re.search(r"^USER\s+(\d+):(\d+)", dockerfile, re.M)
check(user is not None, "Dockerfile USER is numeric uid:gid")
if user:
    sc = pod["securityContext"]
    check(sc["runAsUser"] == int(user.group(1)) and sc["runAsGroup"] == int(user.group(2)),
          "pod runAsUser/runAsGroup match the Dockerfile USER")
check(pod["securityContext"].get("runAsNonRoot") is True, "runAsNonRoot")
check(pod.get("automountServiceAccountToken") is False, "no service-account token mounted")
csc = ctr["securityContext"]
check(csc.get("readOnlyRootFilesystem") is True and csc.get("allowPrivilegeEscalation") is False
      and csc["capabilities"]["drop"] == ["ALL"], "container securityContext is restricted")
check(any(m["mountPath"] == "/tmp" for m in ctr.get("volumeMounts", [])),
      "/tmp is writable (startup marker file) despite the read-only root filesystem")
check("limits" in ctr.get("resources", {}), "resource limits set")

for probe in ("readinessProbe", "livenessProbe"):
    path = ctr[probe]["httpGet"]["path"]
    check(f'url.pathname === "{path}"' in server_js, f"{probe} path {path} is a route in src/server.js")
check(ctr["livenessProbe"]["httpGet"]["path"] != "/health", "liveness does not depend on the database")

ns = kust["namespace"]
for kind, items in by_kind.items():
    for d in items:
        check(d["metadata"].get("namespace") == ns, f"{kind}/{d['metadata']['name']} is in namespace {ns}")
admin_ns = [d for d in docs["k8s/admin/namespace.yaml"] if d["kind"] == "Namespace"][0]
check(admin_ns["metadata"]["name"] == ns, "k8s/admin creates the namespace used by the manifests")
check(admin_ns["metadata"]["labels"].get("pod-security.kubernetes.io/enforce") == "restricted",
      "namespace enforces Pod Security 'restricted'")
rbac = {d["kind"]: d for d in docs["k8s/admin/ci-deployer-rbac.yaml"]}
check(not any("secrets" in r["resources"] for r in rbac["Role"]["rules"]), "CI role cannot read Secrets")
check(rbac["Role"]["kind"] == "Role" and "ClusterRole" not in rbac, "CI credential is namespace-scoped")

check(svc["spec"]["selector"].items() <= labels.items(), "Service selector matches pod labels")
check(dep["spec"]["selector"]["matchLabels"].items() <= labels.items(), "Deployment selector matches pod labels")
for np in by_kind["NetworkPolicy"]:
    sel = np["spec"]["podSelector"].get("matchLabels", {})
    check(sel.items() <= labels.items(), f"NetworkPolicy {np['metadata']['name']} selects the pods")
pdb = by_kind["PodDisruptionBudget"][0]
check(pdb["spec"]["selector"]["matchLabels"].items() <= labels.items(), "PDB selector matches pod labels")
check(dep["spec"]["replicas"] > pdb["spec"]["minAvailable"], "replicas > PDB minAvailable (drains possible)")
np_ports = [p["port"] for np in by_kind["NetworkPolicy"] for r in np["spec"].get("ingress", []) for p in r.get("ports", [])]
check(3000 in np_ports, "NetworkPolicy lets port 3000 in")

image_name = f"{ci['env']['REGISTRY']}/{ci['env']['IMAGE_NAME']}"
check(ctr["image"].split(":")[0].split("@")[0] == image_name, f"Deployment image is {image_name}")
check(kust["images"][0]["name"] == image_name, "kustomization image entry matches the CI image name")
deploy_run = "\n".join(s.get("run", "") for s in ci["jobs"]["release"]["steps"])
check("kustomize edit set image" in deploy_run and "@$DIGEST" in deploy_run, "release deploys by digest")
check("kubectl apply -k k8s/" in deploy_run, "release applies the kustomization (not k8s/admin)")
check(f"rollout status deployment/{dep['metadata']['name']}" in deploy_run and f"-n {ns}" in deploy_run,
      "rollout status targets the right deployment and namespace")

node_major = re.search(r"^FROM node:(\d+)", dockerfile, re.M)
check(bool(node_major) and ci["env"]["NODE_VERSION"] == node_major.group(1), "CI Node version = Dockerfile Node major")

env_names = set(re.findall(r"process\.env\.([A-Z_]+)", server_js))
k8s_names = set(cm["data"]) | {"DB_PASSWORD", "API_KEY"}  # the two keys of the out-of-band Secret
check(env_names <= k8s_names, f"every env var read by the app is provided in Kubernetes ({sorted(env_names)})")
compose_app = docs["docker-compose.yml"][0]["services"]["app"]
check(env_names <= set(compose_app["environment"]), "every env var read by the app is provided by compose")
check(all(not str(p).startswith("0.0.0.0") for p in compose_app.get("ports", [])),
      "compose app port is not bound to 0.0.0.0")
check("ports" not in docs["docker-compose.yml"][0]["services"]["db"], "compose does not publish PostgreSQL")

print("3. Removed bad patterns")
on = ci.get("on", ci.get(True))  # PyYAML reads the key "on" as boolean True
check("pull_request_target" not in on, "no pull_request_target trigger")
check(on["push"]["branches"] == ["main"], "deploy path only from main")
check(ci["permissions"] == {"contents": "read"}, "default GITHUB_TOKEN permissions are read-only")
check(ci["jobs"]["release"].get("environment") == "test-cluster", "release uses a protected environment")
all_steps = [s for j in ci["jobs"].values() for s in j["steps"]]
check(all("${{ github.event" not in s.get("run", "") for s in all_steps), "no event data expanded inside run:")
check(all("npm test" not in s.get("run", "") or "|| true" not in s["run"] for s in all_steps),
      "tests are not allowed to fail silently")
uses = [s["uses"] for s in all_steps if "uses" in s]
check(all(u.startswith("actions/") for u in uses), f"only GitHub-owned actions ({sorted(set(uses))})")
check(not any(u.endswith(("@main", "@master")) for u in uses), "no action on a moving branch")

tracked = git("ls-files", "-co", "--exclude-standard").split()
texts = {f: (ROOT / f).read_text(encoding="utf-8", errors="ignore") for f in tracked if (ROOT / f).is_file()}
for f in (".env", "k8s/secret.yaml"):
    check(f not in tracked, f"{f} is not in the repository")

# The old values are read from the baseline commit (the untouched original), never written in this file.
baseline = git("rev-list", "--max-parents=0", "HEAD").split()[0]
old_secret = yaml.safe_load(git("show", f"{baseline}:k8s/secret.yaml"))["data"]
old_values = [base64.b64decode(old_secret[k]).decode() for k in ("DB_PASSWORD", "API_KEY")]
old_values.append(yaml.safe_load(git("show", f"{baseline}:.github/workflows/ci.yml"))["env"]["DEPLOY_TOKEN"])
for i, value in enumerate(old_values, 1):
    encoded = base64.b64encode(value.encode()).decode()
    hits = [f for f, t in texts.items() if value in t or encoded in t]
    check(not hits, f"old secret #{i} (from the baseline commit) is in no current file {hits or ''}")
for f, t in texts.items():
    if f.endswith((".yml", ".yaml")) or f == "Dockerfile":
        bad = re.findall(r"(?:image:|FROM)\s*\S+:latest\b|node-version:\s*latest", t)
        check(not bad, f"{f}: no 'latest' image or runtime {bad or ''}")
for f, t in texts.items():
    if f in ("Dockerfile", ".github/workflows/ci.yml", "docker-compose.yml") or f.startswith("k8s/"):
        code = "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in t.splitlines())  # ignore comments
        check("9229" not in code, f"{f}: debug port 9229 absent (comments ignored)")

print()
print(f"{len(errors)} problem(s)" if errors else "All checks passed.")
sys.exit(1 if errors else 0)
