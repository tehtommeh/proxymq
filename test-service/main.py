from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import logging
import asyncio
import random
from datetime import datetime, timedelta
from settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-service")

# Global variable to track when service becomes healthy again
healthy_after: datetime

@asynccontextmanager
async def lifespan(app: FastAPI):
    global healthy_after
    # Simulate startup delay
    if settings.startup_delay_seconds > 0:
        logger.info(f"Starting up... waiting {settings.startup_delay_seconds} seconds")
        await asyncio.sleep(settings.startup_delay_seconds)
    
    # Initialize as healthy
    healthy_after = datetime.now()
    logger.info("Service ready")
    yield
    # Cleanup on shutdown
    logger.info("Shutting down")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    current_time = datetime.now()
    
    if current_time < healthy_after:
        remaining_seconds = int((healthy_after - current_time).total_seconds())
        logger.info(f"Health check failed - unhealthy for {remaining_seconds} more seconds")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy", 
                "message": f"Service marked unhealthy for {remaining_seconds} more seconds"
            }
        )
    
    return {"status": "healthy", "message": "Service is ready"}

@app.post("/mark-unhealthy")
async def mark_unhealthy(request: Request):
    global healthy_after
    
    body = await request.json()
    duration_seconds = body.get("duration_seconds", 60)
    
    healthy_after = datetime.now() + timedelta(seconds=duration_seconds)
    logger.info(f"Service marked unhealthy for {duration_seconds} seconds until {healthy_after}")
    
    return {
        "status": "marked_unhealthy",
        "duration_seconds": duration_seconds,
        "healthy_after": healthy_after.isoformat()
    }

@app.get("/")
async def root():
    await asyncio.sleep(settings.request_processing_seconds)
    return {"message": "Test service is running"}

@app.get("/success")
async def success():
    delay = settings.request_processing_seconds + random.uniform(0, 1)
    await asyncio.sleep(delay)
    return {"message": "Success response", "status": "ok"}

@app.post("/success")
async def success_post(request: Request):
    delay = settings.request_processing_seconds + random.uniform(0, 1)
    await asyncio.sleep(delay)
    
    try:
        body = await request.body()
        return {"echo": body.decode("utf-8") if body else ""}
    except Exception as e:
        logger.warning(f"Error reading request body: {e} (type: {type(e).__name__})", exc_info=True)
        return {"echo": "", "error": f"Failed to read request body: {e}"}

@app.get("/error422")
async def error_422():
    logger.info("Returning 422 error")
    return JSONResponse(
        status_code=422,
        content={"error": "Unprocessable Entity", "detail": "Test 422 error"}
    )

@app.post("/error422")
async def error_422_post():
    logger.info("Returning 422 error for POST")
    return JSONResponse(
        status_code=422,
        content={"error": "Unprocessable Entity", "detail": "Test 422 error for POST"}
    )

@app.get("/error500")
async def error_500():
    delay = settings.request_processing_seconds + random.uniform(0, 1)
    await asyncio.sleep(delay)
    logger.info("Returning 500 error")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": "Test 500 error"}
    )

@app.post("/error500")
async def error_500_post():
    delay = settings.request_processing_seconds + random.uniform(0, 1)
    await asyncio.sleep(delay)
    logger.info("Returning 500 error for POST")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": "Test 500 error for POST"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)