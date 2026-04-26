# PDFReader

Prototype web-based PDF reader modeled after the sibling `ZoomableReader` project.

## Features

- upload a local PDF or import one from a URL
- parse the PDF in the Python backend
- extract text chunks with approximate normalized positions
- generate placeholder keywords for each chunk
- render the PDF in the browser and overlay each chunk's keywords at 50% opacity

## Backend

Create a virtual environment, install `backend/pyproject.toml`, and run:

```powershell
uvicorn backend.app:app --reload
```

The API listens on `http://127.0.0.1:8000`.

## Frontend

Install `frontend/package.json`, then run:

```powershell
npm run dev
```

The Vite app listens on `http://127.0.0.1:5173` and proxies `/api` to the backend.
