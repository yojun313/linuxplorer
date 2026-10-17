import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", "8003")),
        reload=False,
        timeout_graceful_shutdown=5,
        timeout_keep_alive=5,
        limit_concurrency=64,
        server_header=False,
    )
