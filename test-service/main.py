from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import logging
import asyncio
import random
from settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-service")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Simulate startup delay
    if settings.startup_delay_seconds > 0:
        logger.info(f"Starting up... waiting {settings.startup_delay_seconds} seconds")
        await asyncio.sleep(settings.startup_delay_seconds)
    logger.info("Service ready")
    yield
    # Cleanup on shutdown
    logger.info("Shutting down")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "healthy", "message": "Service is ready"}

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
    body = await request.body()
    return {"echo": body.decode("utf-8") if body else ""}

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