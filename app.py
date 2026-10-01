"""Start the FastAPI application; build frontend first for a single-port UI."""
import uvicorn

if __name__ == '__main__':
    uvicorn.run('backend.main:app', host='127.0.0.1', port=8000)
