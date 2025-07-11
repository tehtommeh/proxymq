from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import logging

app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test-service")

@app.get("/")
async def root():
    return {"message": "Test service is running"}

@app.get("/success")
async def success():
    return {"message": "Success response", "status": "ok"}

@app.post("/success")
async def success_post():
    return {"message": "Success POST response", "status": "ok"}

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
    logger.info("Returning 500 error")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": "Test 500 error"}
    )

@app.post("/error500")
async def error_500_post():
    logger.info("Returning 500 error for POST")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": "Test 500 error for POST"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)