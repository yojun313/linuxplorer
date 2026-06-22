from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.routes import auth_routes, explorer_routes
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Linuxplorer")

app.include_router(auth_routes.router)
app.include_router(explorer_routes.router, prefix="/explorer")


@app.get("/")
async def root():
    return RedirectResponse(url="/explorer/view")
