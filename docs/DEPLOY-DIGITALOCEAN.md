# Deploying SokoPay to the DigitalOcean droplet (third project, pilot test)

This guide adds SokoPay to the **existing DigitalOcean droplet** as a **third project**, next to the two already running there. The code is pulled straight from the GitHub repository **SokoPay/sokopay-platform**.

SokoPay runs the way it will in production:
- **PostgreSQL 16** database. The local demo's SQLite file is only a laptop convenience.
- **Redis**, for the cache and task queue.
- **Celery** workers and scheduler.
- **HTTPS** through the droplet's existing web server.

When testing is finished, section 13 removes SokoPay completely **without touching the other two projects**.

> **Test data only.** SokoPay runs its `staging` profile:
>
> - **Same as production:** HTTPS only, secure cookies, encryption key required, PostgreSQL, JSON logs, debug off.
> - **One difference:** it uses the **mock payment partner** until Korba or Nsano is contracted and certified.
>
> Every page shows a "TEST ENVIRONMENT" banner. Don't enter real customers' data or real Ghana Card numbers. The droplet is outside Ghana, which is fine for demo data but not for real customer data without Bank of Ghana and Data Protection Commission approval.

## How SokoPay stays separate from the other two projects

| Concern | How it's handled |
|---|---|
| Names | Everything is in the Docker Compose project **`sokopay`**: containers, volumes and networks are all prefixed `sokopay-`. |
| Database | SokoPay gets **its own PostgreSQL container and volume**. It doesn't use, or need access to, the other projects' databases. |
| Ports | PostgreSQL and Redis publish **no ports**. The web app listens only on **127.0.0.1:8100** (configurable). Nothing new appears on the internet. |
| Web traffic | The droplet's **existing** Nginx or Caddy gets one more site (a subdomain) that forwards to 127.0.0.1:8100. The other sites aren't changed. |
| Memory | Every SokoPay container has a memory limit (about 3 GB in total), so it can't starve the other projects. |
| Removal | `wipe.sh` deletes only SokoPay's containers, volumes, image and files. There is no global `docker system prune`, and the droplet is **not** destroyed. |

> **Never run** `docker system prune`, `docker volume prune` or `docker compose down` from another folder on this droplet. They can delete the other projects' data. Always run SokoPay commands from `/opt/sokopay/deploy`.

---

## 1. What you need

| Item | Notes |
|---|---|
| SSH access to the droplet | The user you normally log in with (this guide calls it `deploy`). It needs `sudo` and permission to run `docker`. |
| A subdomain for SokoPay | For example `pay-test.yourdomain.com`, as an A record pointing at the droplet's IP (section 4). The phone apps need real HTTPS. |
| GitHub | The repository `SokoPay/sokopay-platform` is public for now, so no access setup is needed (section 5). |
| Free resources | About **3 GB of RAM** and **10 GB of disk** for SokoPay (section 2). |

---

## 2. Check the droplet first

Log in, then look at what's already there:

```bash
ssh deploy@<droplet-ip>

free -h                                   # memory: SokoPay needs ~3 GB available
df -h /                                   # disk: SokoPay needs ~10 GB free
nproc                                     # CPUs
docker --version && docker compose version  # Compose must be v2.20 or newer
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'   # what's running in Docker
sudo ss -ltnp | grep -E ':(80|443|8100) '                        # who uses 80, 443, 8100
```

Decide from the results:

| You see | Do this |
|---|---|
| Less than ~3 GB of memory free | **Resize** the droplet in DigitalOcean (Droplet, then **Resize**, CPU and RAM only). A short reboot affects all three projects, so pick a quiet time. A 4 vCPU / 8 GB droplet suits three projects. |
| Port **8100** already used | Pick another free port (for example 8110) and use it as `SOKOPAY_WEB_PORT` in section 6 and in the proxy config in section 7. |
| **Nginx** on 80/443 (in `ss`: `nginx`) | Use **section 7A**. |
| **Caddy** on 80/443 (in `ss`: `caddy`, on the host) | Use **section 7B**. |
| A proxy **container** on 80/443 (Traefik, nginx-proxy, Caddy in Docker) | Use **section 7C**. |
| Nothing on 80/443 | Use **section 7D** (SokoPay's own Caddy). |
| Docker not installed, or Compose older than v2.20 | `curl -fsSL https://get.docker.com \| sudo sh`. This upgrades Docker in place and keeps existing containers. Then `sudo usermod -aG docker deploy` and log in again. |

**Don't** change the droplet's SSH settings, time zone or firewall defaults for SokoPay; the other projects rely on them. Ports 80 and 443 are already open, since the other projects use them.

---

## 3. Swap (only if the droplet has none)

```bash
swapon --show            # prints nothing = no swap
# If empty: add 2 GB, which helps the SokoPay image build without squeezing the others
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 4. A subdomain for SokoPay

At your DNS provider (or in DigitalOcean: **Networking**, then **Domains**), add an **A record**. For example, `pay-test.yourdomain.com` points to the droplet's IP. Check it from your computer:

```bash
ping pay-test.yourdomain.com          # should show the droplet's IP
```

With no spare domain, you can use the droplet IP with dashes plus `.sslip.io`, for example `203-0-113-10.sslip.io`. It resolves automatically and gets a normal certificate.

This guide writes **`pay-test.yourdomain.com`**; use your own name everywhere.

---

## 5. Pull the code from GitHub

The repository **SokoPay/sokopay-platform** is currently **public**, so the droplet clones it over HTTPS with no key or password:

```bash
sudo mkdir -p /opt/sokopay && sudo chown deploy:deploy /opt/sokopay
git clone https://github.com/SokoPay/sokopay-platform.git /opt/sokopay
cd /opt/sokopay && git log -1 --oneline
```

The droplet only ever **reads** from GitHub. It can't push, and no GitHub credentials are stored on it.

### 5.1 If the repository is made private again

`git pull` (and `update.sh`) will then fail with "Repository not found" or ask for a username. Give the droplet a **read-only deploy key** and point the clone at it; nothing else changes.

```bash
ssh-keygen -t ed25519 -C "droplet-sokopay-deploy" -f ~/.ssh/sokopay_deploy -N ""
cat >> ~/.ssh/config <<'EOF'
Host github-sokopay
    HostName github.com
    User git
    IdentityFile ~/.ssh/sokopay_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
cat ~/.ssh/sokopay_deploy.pub            # copy this line (starts with ssh-ed25519)
```

On GitHub, open the repository, then **Settings**, then **Deploy keys**, then **Add deploy key**:
- Title: `DigitalOcean droplet (pilot)`. Paste the key.
- **Leave "Allow write access" unticked**, then click **Add key**.

Then switch the existing clone over to the key:

```bash
ssh -T git@github-sokopay              # first time: type "yes"
git -C /opt/sokopay remote set-url origin git@github-sokopay:SokoPay/sokopay-platform.git
git -C /opt/sokopay pull --ff-only
```

The separate `github-sokopay` alias means the key is used only for SokoPay; the other projects' keys are unaffected.

`/opt/sokopay` now holds the full repository. Everything SokoPay needs on the server lives in this folder.

---

## 6. The `.env` file (settings and secrets)

All configuration lives in **one file: `/opt/sokopay/deploy/.env`**. Docker Compose and the containers read it.
- Git ignores it, so it's never pulled or pushed.
- Its permissions are `600`.
- Never email it or paste it into chat.

### 6.1 Create it and generate the secrets

```bash
cd /opt/sokopay/deploy
bash scripts/make-secrets.sh
```

This copies `.env.example` to `.env` and fills every `CHANGE_ME` with a strong random value. The generated secrets:

| Variable | What it is |
|---|---|
| `SECRET_KEY` | Django signing key (sessions, tokens, signed links) |
| `FIELD_ENCRYPTION_KEY` | Encrypts Ghana Card numbers. **If you lose it, that data can't be read.** |
| `POSTGRES_PASSWORD` | SokoPay's own database password (also in `DATABASE_URL`) |
| `REDIS_PASSWORD` | SokoPay's Redis password (also in `REDIS_URL`) |
| `USSD_SHARED_SECRET` | The USSD gateway must send this (section 12.4) |
| `RAIL_WEBHOOK_SECRET` | Signs the mock partner's callbacks |

### 6.2 Fill in the domain, port and profile

```bash
nano .env
```

```dotenv
SOKOPAY_DOMAIN=pay-test.yourdomain.com
ACME_EMAIL=you@yourcompany.com
ALLOWED_HOSTS=pay-test.yourdomain.com,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://pay-test.yourdomain.com
QR_BASE_URL=https://pay-test.yourdomain.com
SOKOPAY_WEB_PORT=8100
# Shared droplet (Nginx/Caddy already on 80/443): localdb only
COMPOSE_PROFILES=localdb
```

Save with Ctrl+O, then exit with Ctrl+X. Check that nothing is left unfilled; the following should print nothing:

```bash
grep CHANGE_ME .env
```

### 6.3 What the other settings mean

| Variable | Pilot value | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.staging` | Production hardening plus the mock partner. Real production uses `config.settings.prod` (section 14). |
| `COMPOSE_PROFILES` | `localdb` | Add `,caddy` only when nothing else uses ports 80/443 (section 7D). |
| `SOKOPAY_ACTIVE_LICENCE` | `DEMI` | Every feature on, for a full test. |
| `RAIL_PROVIDER`, `KYC_IDENTITY_PROVIDER` | `mock` | No real money or real identity checks. |
| `SMS_PROVIDER` | `console` | Sign-in codes are written to the log (section 12.2). |
| `PUSH_PROVIDER` | `console` | Set to `fcm` once Firebase is configured. |

**Optional: DigitalOcean Managed PostgreSQL** instead of the container. Set `COMPOSE_PROFILES=` (empty, or just `caddy`) and paste the cluster's connection string, keeping `sslmode=require`:

```dotenv
DATABASE_URL=postgres://doadmin:<password>@<host>:25060/defaultdb?sslmode=require
```

---

## 7. HTTPS: connect SokoPay to the droplet's web server

Use the one option that matches what you found in section 2.

### 7A. The droplet runs Nginx (most common)

```bash
sudo cp /opt/sokopay/deploy/nginx/sokopay.conf /etc/nginx/sites-available/sokopay
sudo sed -i 's/test.sokopay.example/pay-test.yourdomain.com/' /etc/nginx/sites-available/sokopay
# If you chose a different SOKOPAY_WEB_PORT, change 8100 in that file too.
sudo ln -s /etc/nginx/sites-available/sokopay /etc/nginx/sites-enabled/sokopay
sudo nginx -t && sudo systemctl reload nginx      # test first; the reload doesn't interrupt the other sites
sudo apt -y install certbot python3-certbot-nginx  # skip if certbot is already installed
sudo certbot --nginx -d pay-test.yourdomain.com --redirect -m you@yourcompany.com --agree-tos -n
```

Certbot adds HTTPS **only to the SokoPay site** and renews it automatically, with the same timer as the other projects' certificates.

### 7B. The droplet runs Caddy (on the host)

Add this block to `/etc/caddy/Caddyfile`, below the existing sites:

```caddyfile
pay-test.yourdomain.com {
    encode zstd gzip
    request_body {
        max_size 10MB
    }
    reverse_proxy 127.0.0.1:8100
}
```

Then run `sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy`. Caddy gets the certificate automatically.

### 7C. The droplet's proxy runs in Docker (Traefik, nginx-proxy, Caddy container)

That proxy can't reach `127.0.0.1` on the host. Give it SokoPay's web container instead:
1. Find the proxy's network: `docker network ls`.
2. Connect SokoPay's web container to it: `docker network connect <proxy-network> sokopay-web-1`.
3. Add a route for `pay-test.yourdomain.com` to `sokopay-web-1:8000`, the way the other two projects are routed (labels for Traefik, `VIRTUAL_HOST` for nginx-proxy).

### 7D. Nothing else uses ports 80/443

Use SokoPay's own Caddy. Set `COMPOSE_PROFILES=localdb,caddy` in `.env`. Caddy then serves `SOKOPAY_DOMAIN` with an automatic certificate.

---

## 8. Build and start SokoPay

```bash
cd /opt/sokopay/deploy
docker compose build                       # ~5 minutes the first time
docker compose up -d db redis
docker compose run --rm web python manage.py migrate --noinput
docker compose up -d
docker compose ps                          # web (healthy), worker, beat, db, redis: "running"
docker ps --format '{{.Names}}'            # the other two projects' containers are still there
```

**Check from your computer.** All three should load:

```text
https://pay-test.yourdomain.com/healthz   -> {"status": "ok", ...}
https://pay-test.yourdomain.com/readyz    -> {"status": "ok"}
https://pay-test.yourdomain.com/legal/    -> the legal pages with the orange TEST ENVIRONMENT banner
```

Also open the other two projects' sites, to confirm they still work.

### 8.1 Load the demo data

```bash
docker compose run --rm web python manage.py seed_demo > ~/sokopay-demo-credentials.md
chmod 600 ~/sokopay-demo-credentials.md
cat ~/sokopay-demo-credentials.md
```

This creates the same people, shop, agents and activity as `docs/SokoPay-Demo-Guide.pdf`, with one difference. Because this server is on the internet, **every password, PIN and 2FA secret is random**, and the real ones exist only in that file on the droplet. Share them with testers through a password manager, never in chat.

The shop's merchant code is still **GRACEMTN**.

---

## 9. Daily operations

Always run these from `/opt/sokopay/deploy`; they then affect SokoPay only.

| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Logs (all SokoPay) / web only | `docker compose logs -f --tail 100` / `docker compose logs -f web` |
| Restart SokoPay | `docker compose restart` |
| Stop / start SokoPay | `docker compose stop` / `docker compose up -d` |
| Django shell | `docker compose run --rm web python manage.py shell` |
| SokoPay's memory and CPU | `docker stats --no-stream $(docker compose ps -q)` |
| Back up SokoPay's database | `bash scripts/backup.sh` (keeps the last 14 in `deploy/backups/`) |
| Nightly backups at 02:00 | `(crontab -l; echo "0 2 * * * cd /opt/sokopay/deploy && bash scripts/backup.sh") \| crontab -` |

**Health:**
- The staff portal's **Health** page shows stuck payouts, failed payments, overdue disputes and safeguarding.
- DigitalOcean **Monitoring** alerts cover the whole droplet; check them after adding SokoPay.

**Restore a backup:**

```bash
docker compose stop web worker beat
docker compose exec -T db dropdb -U sokopay sokopay
docker compose exec -T db createdb -U sokopay sokopay
docker compose exec -T db pg_restore -U sokopay -d sokopay --no-owner < backups/<file>.dump
docker compose up -d
docker compose run --rm web python manage.py db_fingerprint      # ledger sums to zero, no integrity breaks
```

---

## 10. Updating to a new version (git pull)

When a new version is pushed to GitHub:

```bash
cd /opt/sokopay/deploy
bash scripts/update.sh                     # pulls main, backs up, rebuilds, migrates, restarts, checks /readyz
BRANCH=some-branch bash scripts/update.sh  # to test another branch
```

`update.sh` does `git pull --ff-only`. If someone edited files on the droplet, it stops rather than overwrite them; see `git -C /opt/sokopay status`. Your `.env` is never touched, because git ignores it.

To go back to the previous version:

```bash
git -C /opt/sokopay log --oneline -5
git -C /opt/sokopay checkout <previous-commit>
docker compose build web && docker compose up -d
```

Restore the backup `update.sh` made only if a migration changed the data.

---

## 11. Other things to know on a shared droplet

- **Ports:** only `127.0.0.1:${SOKOPAY_WEB_PORT}` is used on the host, plus 80/443 if you chose 7D.
- **Images:** SokoPay builds `sokopay-backend:local` and uses the public `postgres:16`, `redis:7` and `caddy:2` images. If another project already uses those images, they're shared but not modified.
- **Disk:** `docker system df` shows Docker's usage for all projects. Clean SokoPay's old build layers with `docker builder prune --filter "label=com.docker.compose.project=sokopay"`. **Don't** run a global `docker system prune`.
- **Reboots:** SokoPay restarts by itself after a droplet reboot (`restart: unless-stopped`), like the other projects.

---

## 12. Testing with the apps and portals

### 12.1 Phone apps pointed at the server

On your computer:

```bash
API_BASE_URL=https://pay-test.yourdomain.com/api/v1 bash mobile/scripts/build_apks.sh
```

The APKs land in `mobile/dist/`. They work on **real phones** because the server has real HTTPS. They're debug-signed and for testing only.

### 12.2 Sign-in codes

With `SMS_PROVIDER=console`, no SMS is sent. Read the codes on the droplet:

```bash
docker compose logs web | grep "SMS:console" | tail -5
```

### 12.3 Portals

- **Address:** `https://pay-test.yourdomain.com/dashboard/`. Use the logins from `~/sokopay-demo-credentials.md`.
- **2FA:** add each account's 2FA secret to an authenticator app, or print the current code:
  ```bash
  docker compose run --rm web python manage.py demo_2fa_code +233200000004
  ```
- **Mock partner:** `https://pay-test.yourdomain.com/dev/mock-partner/` approves MoMo prompts and payouts. On the server it needs the superuser, signed in with 2FA.

### 12.4 USSD

```bash
curl -s -X POST "https://pay-test.yourdomain.com/api/v1/ussd/callback?key=<USSD_SHARED_SECRET from .env>" \
  --data-urlencode sessionId=t1 --data-urlencode phoneNumber=+233244000201 --data-urlencode text=
```

### 12.5 Following the demo guide

Every walkthrough in `docs/SokoPay-Demo-Guide.pdf` works on the server with these changes:
- Use `https://pay-test.yourdomain.com` instead of `http://127.0.0.1:8000`.
- Use the logins from the credentials file.
- Read sign-in codes from `docker compose logs web`.

---

## 13. After the test: remove SokoPay (the other projects stay)

1. **(Optional) keep evidence:** export what you need from the portal first (audit log CSV, statements, regulatory figures). Download a final backup only if you need it; it contains the test data.
   ```bash
   scp deploy@<droplet-ip>:/opt/sokopay/deploy/backups/<latest>.dump .
   ```
2. **Wipe SokoPay's data:**
   ```bash
   cd /opt/sokopay/deploy && bash scripts/wipe.sh
   ```
   Type `WIPE SOKOPAY` to confirm. This deletes:
   - SokoPay's containers;
   - its PostgreSQL, Redis and Caddy volumes;
   - its built image and backups;
   - `.env`, with every secret;
   - its backup cron job.

   At the end it lists the containers still running; the other two projects should be there.
3. **Remove SokoPay's web server site and certificate.** Use the option you set up in section 7:
   - **Nginx (7A):**
     ```bash
     sudo rm /etc/nginx/sites-enabled/sokopay /etc/nginx/sites-available/sokopay
     sudo nginx -t && sudo systemctl reload nginx
     sudo certbot delete --cert-name pay-test.yourdomain.com
     ```
   - **Caddy on the host (7B):** delete the `pay-test.yourdomain.com { ... }` block, then `sudo systemctl reload caddy`.
   - **Proxy in Docker (7C):** remove the SokoPay route from that proxy's configuration.
4. **Remove the code and keys:**
   ```bash
   sudo rm -rf /opt/sokopay
   shred -u ~/sokopay-demo-credentials.md
   ```
   If you set up a deploy key (section 5.1), also run `rm -f ~/.ssh/sokopay_deploy ~/.ssh/sokopay_deploy.pub`, delete the `Host github-sokopay` block from `~/.ssh/config`, and remove the key on GitHub (repository Settings, then Deploy keys).
   Consider making the repository **private** again once the test is over (GitHub: Settings, then General, then Danger Zone, then Change visibility).
5. **Remove the DNS record** for `pay-test.yourdomain.com`. If you added swap only for SokoPay and want it gone: `sudo swapoff /swapfile && sudo rm /swapfile`, then delete its line from `/etc/fstab`.
6. **Check the other projects** still work: open their sites and run `docker ps`.
7. **On your computer:** delete any downloaded dumps and credential files. Uninstall the test APKs from testers' phones. Rotate any real credentials you used (Hubtel, Firebase, Sentry).

The droplet itself is **not** destroyed: the other two projects keep running.

---

## 14. Moving to real production later

| Area | Pilot (this guide) | Production |
|---|---|---|
| Settings | `config.settings.staging` (mock partner allowed) | `config.settings.prod`: refuses to start with any mock, and needs a configured and certified Korba or Nsano (`docs/RAILS-INTEGRATION.md`) |
| Server | Shared droplet (third project) | Dedicated servers, so SokoPay doesn't share a machine with other systems |
| Data location | DigitalOcean (outside Ghana) | Hosting approved by the Bank of Ghana and the Data Protection Commission |
| Database | PostgreSQL container | Managed PostgreSQL with failover, point-in-time recovery, and a restore drill |
| Data | Demo only | Real customers, so the legal documents must be signed off first (`docs/legal`) |

---

## 15. Troubleshooting

| Symptom | Fix |
|---|---|
| `port is already allocated` | Another project uses 8100. Pick a free port for `SOKOPAY_WEB_PORT` (and in the proxy config), then `docker compose up -d`. |
| `502 Bad Gateway` from Nginx or Caddy | SokoPay's web container isn't up yet, or the port differs between `.env` and the proxy config: `docker compose ps`, then `curl -s 127.0.0.1:8100/healthz`. |
| Certificate error in the browser | DNS for the subdomain must point at the droplet. Run certbot again (7A), or check `journalctl -u caddy` (7B). |
| `Bad Request (400)` | The subdomain is missing from `ALLOWED_HOSTS` in `.env`. Then `docker compose up -d`. |
| `CSRF verification failed` on portal forms | `CSRF_TRUSTED_ORIGINS` must be `https://<subdomain>`, and the proxy must send `X-Forwarded-Proto` (the template does). |
| `web` keeps restarting | `docker compose logs web`. Usually a missing `.env` value or a wrong `DATABASE_URL`. |
| The other projects slowed down | `docker stats` shows who uses what. SokoPay's limits are in `deploy/docker-compose.yml` (`mem_limit`). Lower them, or resize the droplet. |
| `git pull` refuses (local changes) | Files were edited on the droplet: `git -C /opt/sokopay status`. Keep your changes elsewhere, then `git -C /opt/sokopay checkout -- .`. |
| `Repository not found` or a username prompt on `git pull` | The repository was made private again. Set up the deploy key (section 5.1). |
| `Permission denied (publickey)` on pull | The deploy key wasn't added on GitHub, or `~/.ssh/config` lacks the `github-sokopay` block (section 5.1). |
| A MoMo payment stays "pending" | Approve it on `/dev/mock-partner/` as the superuser. |
