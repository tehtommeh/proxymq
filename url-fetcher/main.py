from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, HttpUrl
import httpx
from typing import List
from fastapi.responses import Response
import asyncio
import logging
from settings import settings

app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enqueuer")

class FetchRequest(BaseModel):
    url: HttpUrl

class BatchRequestItem(BaseModel):
    method: str
    headers: dict = {}
    body: str = ""
    correlation_id: str = None

class BatchFetchRequest(BaseModel):
    requests: List[BatchRequestItem]

class BatchResponse(BaseModel):
    batch_size: int

@app.post("/fetch")
async def fetch_url(request: FetchRequest):
    """Fetch a single URL and return its response."""
    logger.info(f"Received single fetch request for URL: {request.url}")
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
    """Process multiple HTTP requests in batch with different methods."""
    requests = batch_request.requests
    logger.info(f"Received batch request with {len(requests)} requests")

    async with httpx.AsyncClient() as client:
        tasks = []
        for i, request in enumerate(requests):
            logger.info(f"Processing request {i+1}/{len(requests)}: method={request.method}, correlation_id={request.correlation_id}")
            try:
                # Extract URL from the request body (for URL fetcher service)
                # For GET requests, URL is typically in the body as text
                # For POST/PUT requests, we can also use the body as URL or use a default endpoint
                if request.method.upper() == "GET":
                    url = request.body.strip() if request.body else "https://httpbin.org/get"
                    # Remove content-type header for GET requests as they shouldn't have a body
                    clean_headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-type'}
                    tasks.append(client.get(url, headers=clean_headers))
                elif request.method.upper() == "POST":
                    # For POST, if body looks like a URL, use it as target, otherwise use httpbin
                    if request.body.strip() and (request.body.startswith('http://') or request.body.startswith('https://')):
                        url = request.body.strip()
                        tasks.append(client.post(url, headers=request.headers))
                    else:
                        # Use httpbin for testing POST requests
                        url = "https://httpbin.org/post"
                        tasks.append(client.post(url, content=request.body, headers=request.headers))
                elif request.method.upper() == "PUT":
                    # Similar logic for PUT
                    if request.body.strip() and (request.body.startswith('http://') or request.body.startswith('https://')):
                        url = request.body.strip()
                        tasks.append(client.put(url, headers=request.headers))
                    else:
                        # Use httpbin for testing PUT requests
                        url = "https://httpbin.org/put"
                        tasks.append(client.put(url, content=request.body, headers=request.headers))
                else:
                    # Unsupported method
                    tasks.append(Exception(f"Unsupported HTTP method: {request.method}"))
            except Exception as e:
                tasks.append(e)
        
        try:
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            results = []
            for request, response in zip(requests, responses):
                if isinstance(response, Exception):
                    results.append({
                        "status": "error",
                        "detail": str(response),
                        "status_code": 500,
                        "correlation_id": request.correlation_id
                    })
                else:
                    response_headers = dict(response.headers)
                    # Remove content-encoding to prevent decompression issues
                    response_headers.pop('content-encoding', None)
                    results.append({
                        "status": "success",
                        "status_code": response.status_code,
                        "headers": response_headers,
                        "content": response.content.decode('utf-8', errors='replace'),
                        "correlation_id": request.correlation_id
                    })
            
            logger.info(f"Batch processing completed. Returning {len(results)} results")
            return results
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/fetch/batch")
async def get_batch_size() -> BatchResponse:
    """Return the maximum allowed batch size."""
    return BatchResponse(batch_size=settings.BATCH_SIZE)

@app.get("/health")
async def health():
    """Health check endpoint for dequeuer health monitoring."""
    return {"status": "healthy", "message": "URL-fetcher service is ready"}
