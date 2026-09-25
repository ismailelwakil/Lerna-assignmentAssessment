# 🚀 Lerna — Deployment Guide

Three ways to deploy, pick one:

| Path | Best for | Effort |
|---|---|---|
| [A · Docker Compose](#a--docker-compose-recommended) | VPS / any server with Docker | ~10 min ⭐ |
| [B · Bare metal (systemd + Nginx)](#b--bare-metal-systemd--nginx) | VPS without Docker | ~20 min |
| [C · PaaS (Railway/Render)](#c--paas-railwayrender) | fastest public URL | ~5 min |

---

## Before you deploy (all paths)

### 1. Prepare `.env`

```bash
cp .env.example .env
```

Then edit `.env`:

```ini
# REQUIRED — LLM (at least one; Groq has a free tier)
GROQ_API_KEY=gsk_...
# and/or
OPENROUTER_API_KEY=sk-or-...

# REQUIRED in production — generate a strong secret:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
ASSESS_SECRET=<paste generated secret>

# REQUIRED for production mode (enforces the secret check)
ASSESS_ENV=production

# Optional but recommended (better evidence retrieval)
GEMINI_API_KEY=...            # neural embeddings
QDRANT_URL=...                # vector search (Qdrant Cloud free tier)
QDRANT_API_KEY=...

# Optional — swap SQLite for PostgreSQL on busy servers
# ASSESS_DB_URL=postgresql+psycopg://user:pass@host:5432/lerna
```

> **Fail-closed safety:** with `ASSESS_ENV=production` the API refuses to
> start if `ASSESS_SECRET` is missing, default, or shorter than 24 chars.

### 2. Ports

| Service | Port | Purpose |
|---|---|---|
| REST API | **8002** | what your platform/frontend calls |
| Streamlit UI | **8501** | testing/demo interface (optional to expose) |

---

## A · Docker Compose (recommended)

One command runs both the API and the UI with a persistent volume.

```bash
# on your server
git clone <your-repo> lerna && cd lerna     # or scp -r the folder
cp .env.example .env && nano .env           # fill keys + ASSESS_SECRET (see above)

docker compose up -d --build
```

Verify:

```bash
docker compose ps                          # both services "running (healthy)"
curl http://localhost:8002/health          # {"status":"ok","module":"assessment",...}
# UI: http://<server-ip>:8501
# API docs: http://<server-ip>:8002/docs
```

**Add HTTPS with a reverse proxy** (recommended — put Caddy in front, it does
TLS automatically). Append to `docker-compose.yml`:

```yaml
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
```

…and create `Caddyfile`:

```
lerna.example.com {
    reverse_proxy /api/* api:8002
    reverse_proxy ui:8501
}
```

Then `docker compose up -d` again. Done — HTTPS is live.

**Operations:**

```bash
docker compose logs -f api          # tail logs
docker compose restart              # restart
docker compose down                 # stop (data volume survives)
docker compose down -v              # stop + DELETE data (careful!)
docker exec -it lerna-api pytest tests -q   # run the test suite in-container
```

**Backups** (the SQLite DB lives in the `lerna_data` volume):

```bash
docker run --rm -v lerna_lerna_data:/data -v $(pwd):/backup alpine \
    sh -c "cp /data/assessment.db /backup/backup-$(date +%F).db"
```

---

## B · Bare metal (systemd + Nginx)

Ready-made unit files live in `deploy/`.

```bash
# 1. install
sudo useradd -r -m -d /opt/lerna lerna
sudo mkdir -p /opt/lerna && sudo chown lerna:lerna /opt/lerna
sudo -u lerna cp -r . /opt/lerna/          # or git clone into /opt/lerna
cd /opt/lerna

# 2. virtualenv + deps
sudo -u lerna python3 -m venv .venv
sudo -u lerna .venv/bin/pip install -r requirements.txt

# 3. config
sudo -u lerna cp .env.example .env
sudo -u lerna nano .env                     # keys + ASSESS_SECRET + ASSESS_ENV=production

# 4. sanity check (must print {"status":"ok",...})
sudo -u lerna .venv/bin/python -c "from app.api import app" && echo "config OK"

# 5. services
sudo cp deploy/lerna-api.service deploy/lerna-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lerna-api lerna-ui
sudo systemctl status lerna-api --no-pager  # active (running)?

# 6. Nginx + TLS
sudo apt install -y nginx certbot python3-certbot-nginx
sudo cp deploy/nginx-lerna.conf /etc/nginx/sites-available/lerna
sudo ln -s /etc/nginx/sites-available/lerna /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-available/lerna   # set your domain
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d lerna.example.com    # free TLS
```

Verify: `https://lerna.example.com/api/v1/assess/` → 404 JSON (API alive),
`https://lerna.example.com/` → Lerna UI.

---

## C · PaaS (Railway/Render)

**Railway:** create a project → deploy from repo → set **Start Command**:

```
uvicorn app.api:app --host 0.0.0.0 --port $PORT --workers 2
```

→ add ALL variables from `.env` (secrets + `ASSESS_ENV=production`) → attach
a volume mounted at `/app/data` (keeps the SQLite DB across deploys).

**Render:** new Web Service → build `pip install -r requirements.txt` →
start command as above → add env vars → attach disk at `/app/data`.

> SQLite + one instance works fine for a demo/small deployment. For
> multi-instance or high traffic, set `ASSESS_DB_URL` to PostgreSQL
> (`postgresql+psycopg://...`, add `psycopg2-binary` to requirements).

---

## Post-deployment checklist

```bash
# 1. health
curl https://your-host/api/v1/assess/../health 2>/dev/null || curl https://your-host:8002/health
# → {"status":"ok","module":"assessment","llm":true,...}

# 2. providers honestly reported?
curl https://your-host:8002/health | python3 -m json.tool
# llm: true (your key works) · embeddings/vector reflect your optional keys

# 3. register a test instructor + create a course → upload material →
#    build a quiz → publish → register a student → submit → AI evaluation

# 4. run the full test suite against the deployment environment
docker exec -it lerna-api pytest tests -q        # docker
.venv/bin/pytest tests -q                        # bare metal
# → 28 passed
```

## Security checklist (production)

- [ ] `ASSESS_ENV=production` set (fail-closed secret check active)
- [ ] `ASSESS_SECRET` is a fresh 32+ char random value
- [ ] HTTPS in front (Caddy/Nginx/certbot or PaaS TLS)
- [ ] `.env` never committed (`.gitignore` covers it)
- [ ] API docs (`/docs`) restricted or accepted as public
- [ ] Server firewall: only 80/443 (+ SSH) open; 8002/8501 bound to localhost behind the proxy
- [ ] Backups scheduled for the data volume / DB

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits: "ASSESS_SECRET must be set…" | Generate a strong secret (guide in the error) and set it in `.env` |
| `llm: false` in /health | No LLM key reached the container — check `env_file` / variables |
| AI evaluation "temporarily unavailable" | Provider rate limit (Groq free tier) — retry in a minute or add OpenRouter credits |
| UI can't reach API | Both share one DB; the UI calls services directly — check container logs |
| 413 on material upload | Raise `client_max_body_size` in Nginx (30m set in the provided config) |
| Lost data after redeploy | Volume missing — `docker compose down` keeps volumes, `-v` deletes them |
