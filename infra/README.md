# Infrastructure

`docker/docker-compose.yml` runs the stateful development dependencies that are
tedious to install natively — currently Neo4j.

```bash
cp infra/docker/.env.example infra/docker/.env   # set NEO4J_PASSWORD
docker compose -f infra/docker/docker-compose.yml up -d
docker compose -f infra/docker/docker-compose.yml down          # stop
docker compose -f infra/docker/docker-compose.yml down -v       # stop and wipe data
```

The API and frontend intentionally run on the host during development; they are
containerised only when there is a deployment target that needs it.

Supabase (Postgres, Auth, Storage) is managed by the Supabase CLI rather than
this compose file — see [`../supabase/README.md`](../supabase/README.md).
