# FounderLens AI — Frontend

Next.js (App Router) + TypeScript + Tailwind shell for FounderLens AI.

```bash
npm install
cp .env.example .env.local
npm run dev        # http://localhost:3000
npm run lint
npm run typecheck
npm run build
```

Only `NEXT_PUBLIC_*` environment variables are readable from the browser. Service-role
keys and provider API keys belong in the backend environment and must never appear here.

See the [root README](../README.md) for the full architecture.
