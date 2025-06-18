from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, HttpUrl
import httpx
from typing import List
import os
from fastapi.responses import Response
import asyncio
import logging
import traceback
from settings import settings

app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enqueuer")

@app.on_event("startup")
async def startup_event():
    logger.info("Starting up enqueuer and initializing RabbitMQ pool...")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down enqueuer and closing RabbitMQ pool...")

class FetchRequest(BaseModel):
    url: HttpUrl
class BatchFetchRequest(BaseModel):
    requests: List[FetchRequest]

class BatchResponse(BaseModel):
    batch_size: int

@app.post("/fetch")
async def fetch_url(request: FetchRequest):
    """Fetch a single URL and return its response."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(str(request.url))
            response_headers = dict(response.headers)
            # Remove any content-encoding headers to prevent decompression attempts
            response_headers.pop('content-encoding', None)
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers
            )
        except httpx.RequestError as e:
            logger.error(f"Error fetching URL {request.url}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/fetch/batch")
async def fetch_batch(batch_request: BatchFetchRequest):
    """Fetch multiple URLs in batch."""
    requests = batch_request.requests

    async with httpx.AsyncClient() as client:
        tasks = []
        for request in requests:
            tasks.append(client.get(str(request.url)))
        
        try:
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            results = []
            for response in responses:
                if isinstance(response, Exception):
                    results.append({
                        "status": "error",
                        "detail": str(response)
                    })
                else:
                    results.append({
                        "status": "success",
                        "status_code": response.status_code,
                        "headers": dict(response.headers),
                        "content": response.content
                    })
            return results
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/fetch/batch")
async def get_batch_size() -> BatchResponse:
    """Return the maximum allowed batch size."""
    return BatchResponse(batch_size=settings.BATCH_SIZE)
