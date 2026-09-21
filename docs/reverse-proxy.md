# Reverse proxy and the tunnel

The chain is:

```
browser ──▶ Cloudflare ──▶ cloudflared ──▶ Nginx Proxy Manager ──▶ helpdesk-web:8080
                                                                        │
                                                                        ▼
                                                                   helpdesk-api
```

The helpdesk publishes no host port. NPM reaches the `web` container by name
over a shared docker network, and the tunnel is configured wherever you already
run it — this stack does not manage it.

## Joining the NPM network

`docker-compose.yml` treats the `edge` network as **external** and joins it:

```yaml
networks:
  edge:
    external: true
    name: ${PROXY_NETWORK:-npm_default}
```

Find your NPM network's real name:

```fish
# CachyOS / fish
docker network ls | grep -i npm
docker inspect nginx-proxy-manager --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'
```

```bash
# Fedora / bash
docker network ls | grep -i npm
docker inspect nginx-proxy-manager --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'
```

Set `PROXY_NETWORK` in the stack environment to whatever that prints. If the
network does not exist, the stack refuses to deploy with a clear error — that
is better than starting an unreachable helpdesk.

## Proxy host in NPM

**Hosts → Proxy Hosts → Add Proxy Host**

| Tab | Field | Value |
| --- | --- | --- |
| Details | Domain Names | `helpdesk.yourdomain.com` |
| Details | Scheme | `http` |
| Details | Forward Hostname | `helpdesk-web` |
| Details | Forward Port | `8080` |
| Details | Block Common Exploits | on |
| Details | Websockets Support | off (not used) |
| SSL | SSL Certificate | your existing certificate, or none |

`http` between NPM and the helpdesk is correct: that hop lives inside a docker
network with no route off the host, and TLS is terminated by Cloudflare at the
edge.

**Do not enable NPM's caching.** The API already sends `Cache-Control:
no-store`, but a proxy cache in front of an authenticated app is a bad idea in
general.

### Passing the real client IP

Under **Advanced**, add:

```nginx
proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto https;
```

Without this the helpdesk sees every request as coming from NPM, which makes
the per-IP login rate limiter useless — one person tripping it would lock out
everyone.

## Cloudflare tunnel

Configure the public hostname on whichever cloudflared you already run:

| Field | Value |
| --- | --- |
| Subdomain | `helpdesk` |
| Domain | your domain |
| Service type | HTTP |
| URL | your NPM container, e.g. `nginx-proxy-manager:80` |

cloudflared and NPM need to share a network for that to resolve, which they
presumably already do if other services go through the same path.

## Application settings

```
PUBLIC_BASE_URL=https://helpdesk.yourdomain.com
CORS_ORIGINS=https://helpdesk.yourdomain.com
SESSION_COOKIE_SECURE=true
PROXY_NETWORK=npm_default
TRUST_CLOUDFLARE_HEADERS=true
```

### About `TRUST_CLOUDFLARE_HEADERS`

This makes the API take the client IP from `CF-Connecting-IP`. **It is only
safe when every route to the helpdesk passes through the tunnel.**

With NPM in the chain, ask yourself whether NPM also serves the same proxy host
to your LAN. If someone on the LAN can hit NPM directly, they can set
`CF-Connecting-IP` to anything and rotate it per request, which defeats the
login rate limiter. In that case either:

- set `TRUST_CLOUDFLARE_HEADERS=false` (you lose real client IPs in the audit
  log, but the limiter then counts NPM's address, which at least cannot be
  spoofed); or
- restrict the proxy host in NPM to Cloudflare's IP ranges under **Access
  Lists**, so only the tunnel can reach it.

## Cloudflare Access (worth considering)

Putting Access in front stops unauthenticated traffic at Cloudflare's edge,
before it ever reaches your tunnel.

**Zero Trust → Access → Applications → Add an application → Self-hosted**,
domain `helpdesk.yourdomain.com`, policy allowing your own email.

Two things to know:

1. **It does not replace the helpdesk's sign-in.** You authenticate twice:
   Access decides who reaches the app, the app decides who sees which tickets.
2. **Anyone you expect to use the GUI needs to be in the Access policy.** For a
   household that is easy — add their email addresses. Emailing the helpdesk
   works regardless, since that path never touches the web front end.

The `CF_ACCESS_*` variables in `.env.example` are placeholders for verifying
Access JWTs inside the API as well; the current build does not enforce them, so
leave `CF_ACCESS_ENABLED=false`.

## Checking it works

```bash
curl -sI https://helpdesk.yourdomain.com/ | head -20
```

Expect `200` plus `strict-transport-security`, `content-security-policy` and
`x-frame-options: DENY`.

| Symptom | Cause |
| --- | --- |
| NPM shows a 502 | `web` is not on `PROXY_NETWORK`, or the container name is not `helpdesk-web` |
| Stack will not deploy, "network not found" | `PROXY_NETWORK` does not match `docker network ls` |
| Sign-in works, everything says "Too many attempts" | Client IP headers are not being passed; see above |
| Cloudflare error page | The tunnel cannot reach NPM — check they share a network |

## Not using NPM?

Delete the `external: true` and `name:` lines from the `edge` network and
publish a port on the `web` service instead:

```yaml
    ports:
      - "127.0.0.1:8088:8080"
```

Bind to `127.0.0.1` so it is reachable only from the host, then point whatever
proxy you do use at `127.0.0.1:8088`.
