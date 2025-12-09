import sys
import logging
from pathlib import Path
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI

# --- LOGGING CONFIGURATION ---
# 1. Define the logs folder path
log_dir = Path("logs")

# 2. Create the folder if it doesn't exist
log_dir.mkdir(parents=True, exist_ok=True)

# 3. Configure logging to save inside that folder
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "app.log"), # Save to logs/app.log
        logging.StreamHandler(sys.stdout)         # AND print to terminal
    ]
)

# Fix path for imports
sys.path.insert(0, Path(__file__).parent.as_posix())

from app.core.config import settings
from app.core.database import Base, engine
from app.api.v1 import api_router

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def test_endpoint():
    return {"message": "API is working!"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)