@echo off
start cmd /k "backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload"
start cmd /k "cd frontend && npm.cmd run dev" 
