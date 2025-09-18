from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from settings import settings
from enqueuer import create_enqueuer
from utils import (create_error_response, create_json_response, extract_auth_token, 
                   create_job_data, MetricsRecorder)
from models import JobData, JobStatus, JobCreationResponse
import asyncio
import logging
import time
import json
import redis.asyncio as redis
from prometheus_client import generate_latest, Counter
from typing import Dict, Any, Optional

# Global instances
enqueuer = None
redis_client = None
metrics_recorder = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enqueuer_async")

# Additional metrics for async mode
ASYNC_JOBS_CREATED = Counter("enqueuer_async_jobs_created_total", "Total async jobs created", ["service"])
ASYNC_JOBS_COMPLETED = Counter("enqueuer_async_jobs_completed_total", "Total async jobs completed", ["service", "status"])
ASYNC_JOBS_RETRIEVED = Counter("enqueuer_async_jobs_retrieved_total", "Total async job results retrieved", ["service"])

async def setup_redis():
    """Initialize Redis connection for job storage"""
    global redis_client
    redis_url = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
    if settings.REDIS_PASSWORD:
        redis_url = f"redis://:{settings.REDIS_PASSWORD}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
    
    redis_client = redis.from_url(redis_url, decode_responses=True)
    logger.info(f"Redis client initialized for {settings.REDIS_HOST}:{settings.REDIS_PORT}")

async def store_job_result(job_id: str, service: str, status: JobStatus, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None, auth_token: Optional[str] = None):
    """Store job result in Redis"""
    job_data = create_job_data(job_id, service, status, result, error, auth_token)
    
    # Store job data in Redis hash with TTL
    job_key = f"job:{job_id}"
    await redis_client.hset(job_key, mapping=job_data.to_redis_dict())
    await redis_client.expire(job_key, settings.ASYNC_JOB_TTL)
    
    logger.info(f"Stored job {job_id} with status {status}")

async def get_job_result(job_id: str) -> Optional[JobData]:
    """Retrieve job result from Redis"""
    job_key = f"job:{job_id}"
    redis_data = await redis_client.hgetall(job_key)
    
    if not redis_data:
        return None
        
    return JobData.from_redis_dict(redis_data)

async def process_async_response(correlation_id: str, service: str, auth_token: Optional[str] = None):
    """Background task to wait for async response and store in Redis"""
    try:
        logger.info(f"Starting background processing for job {correlation_id}")
        
        # Wait for response
        proxy_result = await enqueuer.wait_for_response(correlation_id, service)
        
        # Store successful result
        result_data = {
            "body": proxy_result.body.decode('utf-8', errors='replace'),
            "headers": proxy_result.headers,
            "status_code": proxy_result.status_code
        }
        
        await store_job_result(
            job_id=correlation_id,
            service=service,
            status=JobStatus.COMPLETED,
            result=result_data,
            auth_token=auth_token
        )
        
        ASYNC_JOBS_COMPLETED.labels(service=service, status="success").inc()
        logger.info(f"Successfully completed async job {correlation_id}")
        
    except asyncio.TimeoutError:
        # Store timeout error
        await store_job_result(
            job_id=correlation_id,
            service=service,
            status=JobStatus.FAILED,
            error="Timeout waiting for service response",
            auth_token=auth_token
        )
        ASYNC_JOBS_COMPLETED.labels(service=service, status="timeout").inc()
        logger.error(f"Async job {correlation_id} timed out")
        
    except Exception as e:
        # Store other errors
        await store_job_result(
            job_id=correlation_id,
            service=service,
            status=JobStatus.FAILED,
            error=str(e),
            auth_token=auth_token
        )
        ASYNC_JOBS_COMPLETED.labels(service=service, status="error").inc()
        logger.error(f"Async job {correlation_id} failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global enqueuer, redis_client, metrics_recorder
    logger.info("Starting up asynchronous enqueuer...")
    
    # Initialize enqueuer
    enqueuer = await create_enqueuer()
    
    # Initialize Redis
    await setup_redis()
    
    # Initialize metrics recorder
    metrics_recorder = MetricsRecorder(enqueuer.response_codes, enqueuer.request_latency, enqueuer.requests_failed)
    
    logger.info("Asynchronous enqueuer startup completed")
    
    yield
    
    # Shutdown
    if enqueuer:
        await enqueuer.shutdown()
        
    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed")

app = FastAPI(lifespan=lifespan)

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type="text/plain")

@app.post("/{service}")
async def enqueue_async(service: str, request: Request):
    """Enqueue a job asynchronously and return job ID immediately"""
    start = time.time()
    logger.info(f"Received asynchronous request for service '{service}'")
    
    try:
        # Get request body and headers
        body_bytes = await request.body()
        headers = dict(request.headers)
        
        # Extract auth token for job access control
        auth_token = extract_auth_token(headers, settings.ASYNC_AUTH_HEADER)
        
        # If authentication is required, reject requests without auth tokens
        if settings.ASYNC_REQUIRE_AUTH and not auth_token:
            logger.warning(f"Async job creation rejected for service '{service}' - authentication required but no auth token provided")
            metrics_recorder.record_response(service, 401)
            raise HTTPException(
                status_code=401, 
                detail="Unauthorized: Authentication required for async job creation"
            )
        
        # Enqueue the message
        enqueue_result = await enqueuer.enqueue_message(
            service=service,
            method=request.method,
            body=body_bytes,
            headers=headers
        )
        
        if not enqueue_result.success:
            # Handle enqueue failures
            metrics_recorder.record_response(service, enqueue_result.status_code)
            raise HTTPException(
                status_code=enqueue_result.status_code,
                detail=enqueue_result.error_message
            )
        
        # Store initial job state with auth token
        await store_job_result(
            job_id=enqueue_result.correlation_id,
            service=service,
            status=JobStatus.PENDING,
            auth_token=auth_token
        )
        
        # Start background processing with auth token
        asyncio.create_task(process_async_response(enqueue_result.correlation_id, service, auth_token))
        
        ASYNC_JOBS_CREATED.labels(service=service).inc()
        metrics_recorder.record_response(service, 202)
        
        # Create response using Pydantic model
        response = JobCreationResponse(job_id=enqueue_result.correlation_id)
        return create_json_response(response.model_dump(), 202)
        
    except HTTPException:
        raise
    except Exception as e:
        # Handle unexpected errors
        metrics_recorder.record_failure(service)
        metrics_recorder.record_response(service, 500)
        logger.error(f"Error processing async request for service '{service}': {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        metrics_recorder.record_latency(service, time.time() - start)

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str, request: Request):
    """Retrieve job status and result"""
    logger.info(f"Retrieving job status for {job_id}")
    
    try:
        job_data = await get_job_result(job_id)
        
        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Check auth token if one was stored with the job
        if job_data.auth_token:
            # Extract auth token from request headers
            request_headers = dict(request.headers)
            request_auth_token = extract_auth_token(request_headers, settings.ASYNC_AUTH_HEADER)
            
            if request_auth_token != job_data.auth_token:
                logger.warning(f"Unauthorized access attempt for job {job_id}")
                raise HTTPException(status_code=403, detail="Forbidden: Invalid or missing authentication token")
            
        # Count retrieval metrics
        ASYNC_JOBS_RETRIEVED.labels(service=job_data.service).inc()
        
        return job_data.to_api_response()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")