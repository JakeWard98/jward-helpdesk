# Cloudflare tunnel

The helpdesk publishes no host ports. The only way in is the tunnel, which
reaches the `web` container over the `edge` docker network.

## Option A — your existing cloudflared container

If you already run `cloudflared` for other services, leave the `cloudflared`
service in this stack disabled (it sits behind the `tunnel` profile) and attach
your existing container to this stack's `edge` network:

```bash
# Fedora / bash
docker network connect jward-helpdesk_edge cloudflared
```

```fish
# CachyOS / fish
docker network connect jward-helpdesk_edge cloudflared
```

Check the network name first with `docker network ls` — the prefix comes from
the Portainer stack name.

Then in the Cloudflare dashboard (**Zero Trust → Networks → Tunnels → your
tunnel → Public hostname**):

| Field | Value |
| --- | --- |
| Subdomain | `helpdesk` |
| Domain | your domain |
| Service type | HTTP |
| URL | `helpdesk-web:8080` |

HTTP is correct here: the hop from cloudflared to nginx is inside a docker
network with no route off the host, and Cloudflare terminates TLS at the edge.

## Option B — tunnel inside this stack

Set `CLOUDFLARE_TUNNEL_TOKEN` in the stack environment (copy it from the
tunnel's install command) and deploy with the profile:

```bash
docker compose --profile tunnel up -d
```

In Portainer, add `tunnel` under **Stacks → your stack → Compose profiles**, or
simply remove the `profiles:` line from the `cloudflared` service.

## Required application settings

```
PUBLIC_BASE_URL=https://helpdesk.example.com
CORS_ORIGINS=https://helpdesk.example.com
SESSION_COOKIE_SECURE=true
TRUST_CLOUDFLARE_HEADERS=true
```

`TRUST_CLOUDFLARE_HEADERS=true` makes the API take the client IP from
`CF-Connecting-IP` for rate limiting and the audit log. **Only set it when the
tunnel is genuinely the only route to the container.** If anything else can
reach `web` directly, the header is attacker-controlled and the login rate
limiter can be sidestepped by spoofing it.

## Cloudflare Access (recommended)

Putting Access in front means unauthenticated traffic never reaches the app at
all — it is stopped at Cloudflare's edge, before the tunnel.

**Zero Trust → Access → Applications → Add an application → Self-hosted**:

- Application domain: `helpdesk.example.com`
- Policy: allow your email address, or your identity provider's group
- Session duration: whatever suits you

Two things to know:

1. **Access does not replace the helpdesk's own sign-in.** You will authenticate
   twice. That is the point: Access controls who can reach the app, the app
   controls who can see which tickets.
2. **Requesters need to get in too.** If you want people outside your Access
   policy to raise tickets in the GUI, either widen the policy, or put Access
   on a path-based policy covering `/admin` only, or leave Access off and rely
   on the app's own authentication. Emailing in always works regardless — that
   path never touches the web front end.

The `CF_ACCESS_*` variables in `.env.example` are placeholders for verifying
Access JWTs inside the API as an extra layer; the current build does not
enforce them, so leave `CF_ACCESS_ENABLED=false`.

## Hardening at the edge

Worth doing in the Cloudflare dashboard:

- **WAF → Rate limiting**: cap `POST /api/auth/login` per IP. The app already
  does this, but stopping it at the edge saves the tunnel the traffic.
- **WAF → Managed rules**: the free managed ruleset is fine.
- **Security → Settings**: set a sensible security level and enable Bot
  Fight Mode.
- **Geo restriction**: if the helpdesk is only ever used from one country,
  block everything else.
- **Always Use HTTPS** and a minimum TLS version of 1.2.

## Checking it works

```bash
# from outside your network
curl -sI https://helpdesk.example.com/ | head -20
```

You should see `200`, `strict-transport-security`, `content-security-policy`
and `x-frame-options: DENY`. If you see a Cloudflare error page instead, the
tunnel cannot reach `helpdesk-web:8080` — check both containers share the
`edge` network.
