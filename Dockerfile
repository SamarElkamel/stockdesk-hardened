# StockDesk runtime image.
#
# Base image: Node.js 24 (LTS line) on Alpine, pinned by major version instead of "latest".
# TODO verify: Node 24 is still a supported LTS line on the day this is merged
#   (https://nodejs.org/en/about/previous-releases).
# TODO verify: pin by digest once it has been looked up, e.g.
#   docker buildx imagetools inspect node:24-alpine   ->   FROM node:24-alpine@sha256:<digest>
#   I could not look the digest up, so none is written here. Dependabot (see .github/dependabot.yml)
#   then proposes digest updates as pull requests.
FROM node:26-alpine

ENV NODE_ENV=production
WORKDIR /app

# Only what the application needs. .dockerignore is an allow-list, so .env, k8s/, .git/
# and any other local file can never end up in an image layer again.
# Files stay owned by root: the application user can read them but not modify them.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY src/ ./src/

# Unprivileged user. Numeric UID/GID (1000 is the "node" user of the official image) so that
# Kubernetes "runAsNonRoot: true" can check it; a user *name* cannot be checked by the kubelet.
USER 1000:1000

# Same internal port as before. The Node.js inspector (--inspect, port 9229) is gone:
# it gives anyone who can reach the port full code execution inside the container.
EXPOSE 3000

# Used by docker / docker compose; Kubernetes ignores it and uses its own probes.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/livez || exit 1

CMD ["node", "src/server.js"]
