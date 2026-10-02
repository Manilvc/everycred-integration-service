# Deployment

> Last updated: 2026-09-30

The service runs as a Docker container (or, alternatively, as systemd
services; see [Without Docker](#without-docker-systemd)) on the same host
as the existing EveryCRED backend and is published on the **shared backend domain** under
`/integration/`, the same way the audit service is mounted at `/audit/`.

```mermaid
flowchart LR
    C[Clients] -->|https://api-evrc.viitorcloud.in| N[nginx on host]
    N -->|/| B[Main backend :8070]
    N -->|/audit/| A[Audit :8020]
    N -->|/integration/ prefix stripped| I[Integration container 127.0.0.1:8030]
    I --> DB[(MySQL)]
    I --> SM[(AWS Secrets Manager + KMS)]
    I --> P[Providers]
```

| Public URL | Reaches |
|------------|---------|
| `https://api-evrc.viitorcloud.in/integration/v1/...` | The API |
| `https://api-evrc.viitorcloud.in/integration/health/live` | Liveness |
| `https://api-evrc.viitorcloud.in/integration/health/ready` | Readiness (checks MySQL) |
| `https://api-evrc.viitorcloud.in/integration/docs`, `/redoc` | Only if `ENABLE_DOCS=true` and not production |

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage image: dependencies built with uv, slim non-root runtime (uid 10001), health check |
| `deploy/docker-entrypoint.sh` | `serve` (uvicorn) or `migrate` (Alembic) |
| `docker-compose.yml` | `migrate` runs once, then `api` starts; port published on loopback only |
| `docker-compose.mysql-socket.yml` | Optional override: MySQL on the same server through its Unix socket |
| `deploy/nginx/integration-subpath.conf` | Upstream and `location /integration/` to paste into the existing vhost |
| `deploy/systemd/everycred-integration-api.service` | Without Docker: API on `127.0.0.1:8030`, migrations before each start |
| `deploy/systemd/everycred-integration-worker.service` | Without Docker: the background worker |
| `.dockerignore` | Keeps `.env`, `.venv`, tests, and docs out of the image |

## 1. Prepare the environment

On the server, in the repository directory:

```bash
cp .env.example .env
```

Set at least:

| Variable | Deployed value |
|----------|----------------|
| `ENVIRONMENT` | `production` (or `staging`) |
| `DATABASE_URL` | MySQL on this server: the socket URL from [MySQL on the same server](#mysql-on-the-same-server-recommended-socket). Separate server: `mysql+aiomysql://<user>:<password>@<db-host>:3306/everycred_integration?charset=utf8mb4` |
| `JWT_SECRET_KEY`, `API_KEY_HASH_SECRET`, `CONNECTION_ENCRYPTION_KEYS` | New random values (generation commands in `.env.example`) |
| `SUPER_ADMIN_BOOTSTRAP_TOKEN` | Set for the first deploy only; remove after creating the first super admin |
| `SECRET_STORE_BACKEND` | `aws` (required in production) |
| `AWS_REGION`, `SECRETS_KMS_KEY_ID` | Region and KMS key for the secret store |
| `LOG_JSON` | `true` |
| `ENABLE_DOCS` | `false` in production |
| `CORS_ALLOWED_ORIGINS` | Only the frontends that call this service directly, e.g. `["https://admin.example.com"]` |

`ROOT_PATH`, `PORT`, and `WEB_CONCURRENCY` are set in `docker-compose.yml`,
not `.env`.

Restrict the file: `chmod 600 .env`.

### MySQL

Create the database and a user limited to it (do not use root):

```sql
CREATE DATABASE everycred_integration CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'everycred_integration'@'%' IDENTIFIED BY '<strong password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
  ON everycred_integration.* TO 'everycred_integration'@'%';
```

Narrow `'%'` to the Docker bridge subnet or the host's address if MySQL
listens beyond localhost. MySQL on the host must accept connections from
the Docker bridge (`bind-address` not limited to `127.0.0.1`).

### MySQL on the same server (recommended: socket)

When MySQL runs on the Docker host itself, connect through its Unix socket
instead of TCP. It works whatever MySQL's `bind-address` and the firewall
say, and MySQL stays off the network.

1. Find the socket: `mysql -e "SELECT @@socket"` (usually
   `/var/run/mysqld/mysqld.sock`).
2. Create the account for `localhost` (socket connections count as local):

   ```sql
   CREATE DATABASE IF NOT EXISTS everycred_integration CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'everycred_integration'@'localhost' IDENTIFIED BY '<strong password>';
   GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
     ON everycred_integration.* TO 'everycred_integration'@'localhost';
   ```

3. In `.env`:

   ```dotenv
   COMPOSE_FILE=docker-compose.yml:docker-compose.mysql-socket.yml
   DATABASE_URL=mysql+aiomysql://everycred_integration:<password>@localhost/everycred_integration?unix_socket=/run/mysqld/mysqld.sock&charset=utf8mb4
   # Only if the socket directory is not /var/run/mysqld:
   # MYSQL_SOCKET_DIR=/path/to/socket/dir
   ```

   `COMPOSE_FILE` makes plain `docker compose ...` commands include the
   socket override, which mounts the directory into both containers at
   `/run/mysqld`. Keep `/run/mysqld/mysqld.sock` in the URL; that is the
   path inside the container.

4. `docker compose up -d`.

Before migrating, the `migrate` container waits up to `DB_WAIT_SECONDS`
(30 by default) for the database and, if it cannot connect, prints the
cause and fix in plain words (for example "127.0.0.1 inside a container is
the container itself") instead of a traceback. Passwords are never
printed.

### MySQL elsewhere (TCP)

For MySQL on another machine, or when you prefer TCP to a local MySQL,
use a normal host in `DATABASE_URL`. For MySQL on the Docker host itself
that host is `host.docker.internal`, which every service already maps to
the host (`extra_hosts: host-gateway` in `docker-compose.yml`):

```dotenv
DATABASE_URL=mysql+aiomysql://everycred_integration:<password>@host.docker.internal:3306/everycred_integration?charset=utf8mb4
```

Ubuntu's MySQL accepts none of that by default. On the host:

1. Listen on the Docker bridge as well as loopback (MySQL 8.0.13+
   accepts a list). Find the bridge address with
   `ip -4 addr show docker0`, usually `172.17.0.1`, then in
   `/etc/mysql/mysql.conf.d/mysqld.cnf`:

   ```ini
   bind-address = 127.0.0.1,172.17.0.1
   ```

   and `sudo systemctl restart mysql`. Avoid `0.0.0.0`, which also
   exposes MySQL on the public interface.

2. Create the account for Docker's networks, not `localhost`:

   ```sql
   CREATE USER 'everycred_integration'@'172.16.0.0/255.240.0.0'
     IDENTIFIED BY '<password>';
   GRANT ALL PRIVILEGES ON everycred_integration.*
     TO 'everycred_integration'@'172.16.0.0/255.240.0.0';
   ```

3. If ufw is active:
   `sudo ufw allow from 172.16.0.0/12 to any port 3306 proto tcp`.

Do not set `COMPOSE_FILE` to the socket override when using TCP.

### AWS credentials

The container uses the standard AWS credential chain.

- **EC2 instance role (recommended):** attach a role with the policy from
  [secret store](core/secret_store.md). Containers on the default bridge
  network are one extra network hop from the instance metadata service,
  so set the instance's IMDSv2 **hop limit to 2**, or the SDK cannot get
  credentials:

  ```bash
  aws ec2 modify-instance-metadata-options --instance-id <id> \
    --http-put-response-hop-limit 2 --http-tokens required
  ```

- **Access keys:** only if no role is possible; put
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in `.env`.

## 2. Build and start

```bash
docker compose build
docker compose up -d
docker compose ps           # migrate: Exited (0), api: Up (healthy), worker: Up (healthy)
docker compose logs -f api
```

`migrate` applies every pending Alembic migration and exits; `api` and
`worker` start only if it succeeded. `worker` delivers webhooks, expires
sessions and deletes expired results; keep exactly one running. The API
listens on `127.0.0.1:8030`, so it is not reachable from outside the
host except through nginx.

If `migrate` exits with 1, `docker compose logs migrate` says why in
plain words, without printing passwords or other `.env` values:

- "The settings in .env are invalid": a setting is missing or wrong;
  the line names it (for example `SECRET_STORE_BACKEND=aws` without
  `AWS_REGION` and `SECRETS_KMS_KEY_ID`).
- "Cannot reach the database": followed by the likely cause and fix
  for the socket or TCP setup above.

Check locally on the server:

```bash
curl -s http://127.0.0.1:8030/health/live     # {"status":"ok"}
curl -s http://127.0.0.1:8030/health/ready    # {"is_ready":true,...}
```

## 3. Add the nginx location

Edit the existing vhost for `api-evrc.viitorcloud.in` (the one that already
contains `location /audit/`):

1. Put the `upstream integration_api { ... }` block at the top of the file,
   outside any `server { }` block.
2. Paste the `location = /integration` and `location /integration/` blocks
   inside the existing `listen 443 ssl` server block.

Both are in `deploy/nginx/integration-subpath.conf`. Then:

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -s https://api-evrc.viitorcloud.in/integration/health/live
```

The trailing slash in `proxy_pass http://integration_api/;` strips the
prefix, and `ROOT_PATH=/integration` tells the app about it. Change both
together if the mount point ever changes.

## 4. First super admin

Once, with the bootstrap token from `.env`:

```bash
curl -X POST https://api-evrc.viitorcloud.in/integration/v1/super-admins/register \
  -H "Content-Type: application/json" \
  -H "X-Bootstrap-Token: <SUPER_ADMIN_BOOTSTRAP_TOKEN>" \
  -d '{"email": "admin@example.com", "full_name": "Platform Admin", "password": "<12+ characters>"}'
```

Then remove `SUPER_ADMIN_BOOTSTRAP_TOKEN` from `.env` and run
`docker compose up -d` to apply it.

## Without Docker: systemd

The same service can run directly on the host as two systemd units, like
the audit service. Use either Docker or systemd, not both: both listen on
`127.0.0.1:8030`.

Running on the host also avoids the container networking of the MySQL
setup above: `localhost` is the host's MySQL, so a MySQL account for
`'localhost'` is enough and `bind-address` and the firewall stay as they
are.

1. Install uv (once) and the dependencies into `.venv`:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh    # installs to ~/.local/bin
   cd /var/www/everycred-integration-service
   ~/.local/bin/uv sync --frozen --no-dev
   ```

2. Create the MySQL account for `localhost` and point `.env` at the
   socket (no `COMPOSE_FILE` or `MYSQL_SOCKET_DIR` lines):

   ```sql
   CREATE USER 'everycred_integration'@'localhost' IDENTIFIED BY '<password>';
   GRANT ALL PRIVILEGES ON everycred_integration.* TO 'everycred_integration'@'localhost';
   ```

   ```dotenv
   DATABASE_URL=mysql+aiomysql://everycred_integration:<password>@localhost/everycred_integration?unix_socket=/var/run/mysqld/mysqld.sock&charset=utf8mb4
   ```

3. Stop the containers, then install and start the units:

   ```bash
   docker compose down
   sudo cp deploy/systemd/everycred-integration-*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now everycred-integration-api everycred-integration-worker
   systemctl status everycred-integration-api everycred-integration-worker
   curl -s http://127.0.0.1:8030/health/ready
   ```

The units run as `everycred-local`, which must be able to read `.env`
(`chmod 640 .env`). Each API start first runs
`python -m app.core.db_check` and `alembic upgrade head`; if either fails
the API does not start, and the reason is in the journal:

```bash
journalctl -u everycred-integration-api -n 50 --no-pager
journalctl -u everycred-integration-worker -f
```

To update: `git pull`, `uv sync --frozen --no-dev`, then
`sudo systemctl restart everycred-integration-api everycred-integration-worker`.
The nginx location is the same as for Docker.

## Updating

```bash
git pull
docker compose build
docker compose up -d        # migrate runs again first; no-op if up to date
```

Migrations run before the new API starts. Review migrations before
deploying: MySQL runs DDL outside transactions, so a failed migration can
leave the schema partly changed. Take a database backup first.

## Operations

| Task | Command |
|------|---------|
| Logs | `docker compose logs -f api` (JSON lines when `LOG_JSON=true`) |
| Worker logs | `docker compose logs -f worker` |
| Restart | `docker compose restart api` |
| Current migration | `docker compose run --rm migrate alembic current` |
| Shell in a new container | `docker compose run --rm api sh` |
| Stop | `docker compose down` |

## Hardening in place

- Non-root user, read-only root filesystem, all Linux capabilities dropped,
  `no-new-privileges`.
- Port bound to `127.0.0.1` only; nginx terminates TLS.
- No compiler, uv, or build cache in the runtime image; `.env` excluded
  from the build context.
- Memory and CPU limits (`768m`, `1.0` CPU) and log rotation (5 × 20 MB).
- uvicorn's `Server` header disabled.
- Production refuses to start without the AWS secret store.
