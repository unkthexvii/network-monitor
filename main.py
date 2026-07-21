import sys
import os
import socket
import shutil
import traceback
import asyncio
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    os.chdir(APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

def global_exception_handler(exc_type, exc_value, exc_traceback):
    with open(os.path.join(APP_DIR, "crash.log"), "w") as f:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
    print("CRASH LOGGED TO crash.log")
    _safe_pause()
    sys.exit(1)

def _safe_pause():
    """Pause with a prompt only when running interactively (stdin is a TTY).
    In frozen/non-interactive contexts (service, pipes, CI), just sleep briefly to let logs flush."""
    try:
        if sys.stdin.isatty() and not getattr(sys, 'frozen', False):
            input("Press Enter to exit...")
        else:
            raise EOFError
    except (EOFError, OSError):
        import time
        time.sleep(2)

sys.excepthook = global_exception_handler

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

import logging.handlers
import time

# Ensure logs directory exists
os.makedirs(os.path.join(APP_DIR, "logs"), exist_ok=True)

# Configure structured logging for the entire application
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Rotating File Handler (10MB max, keep 5 backups)
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(APP_DIR, "logs", "app.log"), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(log_formatter)

# Root Logger Config
root_logger = logging.getLogger()
# Remove any pre-existing handlers (e.g., from uvicorn's own logging setup) to prevent duplicates
for h in list(root_logger.handlers):
    root_logger.removeHandler(h)
root_logger.setLevel(logging.INFO)
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

# Suppress overly verbose logs
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)

# Dedicated SNMP failure log (ERROR level only)
snmp_handler = logging.handlers.RotatingFileHandler(
    os.path.join(APP_DIR, "logs", "snmp_failures.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
snmp_handler.setFormatter(log_formatter)
snmp_handler.setLevel(logging.ERROR)
logging.getLogger("SNMPEngine").addHandler(snmp_handler)

logger = logging.getLogger(__name__)

from database.session import init_db, dispose_engine
from core.scheduler import start_scheduler, shutdown_scheduler
from core.alert_engine import register_notify_callback
from core.device_cache import device_cache
from core.workers import register_worker_notify

# Import routers
from api.devices import router as devices_router
from api.dashboard import router as dashboard_router
from api.topology import router as topology_router
from api.alerts import router as alerts_router
from api.reports import router as reports_router
from api.stream import router as stream_router, sse_publisher
from core.config import READONLY, DEFAULT_ADMIN_PASSWORD, get_readonly_from_db, set_readonly_in_db
from api.auth import router as auth_router
from core.auth import session_store, hash_password

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing Database...")
    await init_db()
    
    logger.info("Loading device cache into memory...")
    await device_cache.load_from_db()
    
    logger.info("Registering SSE callbacks...")
    register_notify_callback(sse_publisher)
    register_worker_notify(sse_publisher)
    
    logger.info("Starting Scheduler...")
    start_scheduler()
    
    # Seed default admin password if not set
    from database.models import Setting
    from database.session import async_session
    async with async_session() as session:
        existing = await session.get(Setting, "admin_password_hash")
        if not existing:
            salt, pw_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
            session.add(Setting(key="admin_password_salt", value=salt))
            session.add(Setting(key="admin_password_hash", value=pw_hash))
            await session.commit()
            logger.info(f"Default admin password set to '{DEFAULT_ADMIN_PASSWORD}' — change it via /api/auth/change-password")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Scheduler...")
    shutdown_scheduler()
    await dispose_engine()

app = FastAPI(lifespan=lifespan, title="Network Monitoring System")

_CORS_ORIGINS = [
    "http://localhost",
    "http://localhost:8000",
    "http://127.0.0.1",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
class CustomGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith("/api/stream"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

app.add_middleware(CustomGZipMiddleware, minimum_size=1000)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.middleware("http")
async def log_requests(request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000
    if not request.url.path.startswith("/api/stream"):
        logger.info(f"{request.method} {request.url.path} - Status: {response.status_code} - Latency: {process_time:.2f}ms")
    return response


@app.middleware("http")
async def auth_guard(request, call_next):
    # Allow non-mutating requests through
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    # Always allow login/logout and admin readonly toggle (needed to toggle readonly off)
    if request.url.path in ("/api/auth/login", "/api/auth/logout", "/api/auth/change-password", "/api/admin/readonly"):
        return await call_next(request)
    if request.url.path.startswith("/api/stream"):
        return await call_next(request)
    
    # Dynamic readonly check — reads from both env var and DB
    if await get_readonly_from_db():
        return JSONResponse(status_code=403, content={"detail": "Read-only mode active"})
    
    # Auth check for mutating requests on all /api/ paths
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/auth"):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not session_store.validate(token):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    
    return await call_next(request)


@app.get("/api/readonly")
async def get_readonly(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    authenticated = bool(token) and session_store.validate(token)
    readonly = await get_readonly_from_db()
    return {"readonly": readonly, "authenticated": authenticated}


@app.post("/api/admin/readonly")
async def post_readonly(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not session_store.validate(token):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    body = await request.json()
    value = bool(body.get("readonly", False))
    await set_readonly_in_db(value)
    return {"readonly": value}


# Include Routers
app.include_router(devices_router)
app.include_router(dashboard_router)
app.include_router(topology_router)
app.include_router(alerts_router)
app.include_router(reports_router)
app.include_router(stream_router)
app.include_router(auth_router)

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def extract_assets_to_cwd():
    """Extract embedded folders to the current directory if running as a compiled exe."""
    if getattr(sys, 'frozen', False):
        cwd = os.getcwd()
        meipass = sys._MEIPASS
        
        for folder in ["static", "logo"]:
            source = os.path.join(meipass, folder)
            dest = os.path.join(cwd, folder)
            if os.path.exists(source) and not os.path.exists(dest):
                shutil.copytree(source, dest)

# Trigger the extraction
extract_assets_to_cwd()

# Prioritize serving from the extracted folders in CWD
CWD = os.getcwd()
STATIC_DIR = os.path.join(CWD, "static")
if not os.path.exists(STATIC_DIR):
    # Fallback for development mode
    STATIC_DIR = os.path.join(get_base_dir(), "static")

@app.get("/")
@app.get("/dashboard")
@app.get("/topology")
@app.get("/devices")
@app.get("/alerts")
@app.get("/reports")
async def serve_root():
    """Serve the SPA as the root page."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/wall")
async def serve_wall():
    """Serve the Big Screen wall."""
    return FileResponse(os.path.join(STATIC_DIR, "wall.html"))

import glob
from fastapi import HTTPException

@app.get("/api/logo")
async def get_logo():
    """Serve the first image found in the 'logo' folder next to the executable, or 404 if none exists."""
    # Use the directory where the .exe is located, NOT the bundled temporary folder
    cwd = os.getcwd()
    logo_dir = os.path.join(cwd, "logo")
    if os.path.exists(logo_dir):
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp", "*.PNG", "*.JPG", "*.JPEG"):
            files = glob.glob(os.path.join(logo_dir, ext))
            if files:
                return FileResponse(files[0])
    raise HTTPException(status_code=404, detail="No logo found")

@app.get("/favicon.ico")
async def get_favicon():
    """Serve the logo as favicon.ico (PNG works in all modern browsers)."""
    cwd = os.getcwd()
    logo_dir = os.path.join(cwd, "logo")
    if os.path.exists(logo_dir):
        for ext in ("*.png", "*.PNG", "*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.gif", "*.GIF", "*.svg", "*.SVG", "*.ico", "*.ICO"):
            files = glob.glob(os.path.join(logo_dir, ext))
            if files:
                return FileResponse(files[0], media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="No favicon found")

# Mount static files
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

import subprocess

def force_kill_port(port=8000):
    """Force kill any process currently listening on the specified port (Windows)."""
    try:
        if os.name == 'nt':
            result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        if pid != "0":
                            logger.warning(f"Port {port} is in use by PID {pid}. Force killing it...")
                            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=10)
                            time.sleep(1)
    except Exception as e:
        logger.error(f"Failed to clear port {port}: {e}")

import ctypes

if __name__ == "__main__":
    try:
        # Enforce Administrator privileges on Windows
        if os.name == 'nt':
            try:
                is_admin = ctypes.windll.shell32.IsUserAnAdmin()
            except Exception:
                is_admin = False
                
            if not is_admin:
                logger.error("CRITICAL ERROR: Administrator privileges required.")
                print("\n[!] CRITICAL ERROR: You must run this as Administrator for network scanning to work!")
                print("[!] Please right-click and select 'Run as Administrator'.\n")
                _safe_pause()
                sys.exit(1)

        target_port = int(os.getenv("MONITOR_PORT", "8000"))
        force_kill_port(target_port)
        
        logger.info("======================================================")
        logger.info(f" Starting Network Monitoring System on port {target_port} ")
        logger.info("======================================================")
        
        # In a frozen PyInstaller exe, Uvicorn cannot dynamically import "main:app" via string.
        # We must pass the actual `app` object directly. For local dev, we still use the string so auto-reload works.
        if getattr(sys, 'frozen', False):
            uvicorn.run(app, host="0.0.0.0", port=target_port, loop="none", log_config=None)
        else:
            uvicorn.run(
                "main:app", 
                host="0.0.0.0", 
                port=target_port, 
                reload=False, 
                loop="none",
                log_config=None
            )
    except Exception as e:
        logger.error(f"Fatal crash during startup: {e}", exc_info=True)
        print(f"\n[FATAL ERROR] {e}")
        _safe_pause()
        sys.exit(1)
