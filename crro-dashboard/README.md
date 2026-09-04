# CRRO Dashboard

Interactive revenue-recovery command center built with Vite and React.

## Screens

| Screen | Route | API |
|---|---|---|
| Executive Overview | `/` | `GET /dashboard/overview` |
| Live Recovery Case | `/cases` | `GET /cases`, journey and explainability endpoints |
| Optimizer | `/optimizer` | `GET /dashboard/optimizer` |
| Explainability | `/explainability` | `GET /cases/{case_id}/explainability` |
| Pending Approvals | `/approvals` | approval endpoints |
| Audit / Activity | `/audit` | `GET /recovery/audit` |

## Run locally

Start the backend first:

```bash
uvicorn api:app --reload --port 8000
```

Then:

```bash
npm install
npm run dev
```

Open `http://localhost:5173`.

The dashboard uses the Vite development proxy for `/api/*` requests. PostgreSQL should be reachable for the persisted case, approval and audit views.

## Production build

```bash
npm run build
npm run preview
```

For a deployed API:

```bash
VITE_API_URL=https://your-api.example.com npm run build
```

The application intentionally keeps the decision trail visible: case context, model reasoning, selected action, policy outcome, approval and reconciliation can all be inspected from the UI.
