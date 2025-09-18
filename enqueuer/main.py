from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from enqueuer import create_enqueuer, Enqueuer
from fastapi.responses import Response
from utils import create_error_response, get_error_message_for_status, MetricsRecorder
import asyncio
import logging
import time
from prometheus_client import generate_latest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enqueuer")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup
    logger.info("Starting up synchronous enqueuer...")
    _app.state.enqueuer = await create_enqueuer()
    _app.state.metrics_recorder = MetricsRecorder(
        _app.state.enqueuer.response_codes,
        _app.state.enqueuer.request_latency,
        _app.state.enqueuer.requests_failed
    )
    logger.info("Synchronous enqueuer startup completed")

    yield

    # Shutdown
    if hasattr(_app.state, 'enqueuer'):
        await _app.state.enqueuer.shutdown()

app = FastAPI(lifespan=lifespan)


# Dependency providers
async def get_enqueuer(request: Request) -> Enqueuer:
    """Get the enqueuer instance from app state"""
    return request.app.state.enqueuer


async def get_metrics_recorder(request: Request) -> MetricsRecorder:
    """Get the metrics recorder instance from app state"""
    return request.app.state.metrics_recorder


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type="text/plain")

@app.api_route("/{service}", methods=["GET", "POST", "PUT"])
async def proxy(
    service: str,
    request: Request,
    enqueuer: Enqueuer = Depends(get_enqueuer),
    metrics_recorder: MetricsRecorder = Depends(get_metrics_recorder)
):
    start = time.time()
    logger.info(f"Received synchronous request for service '{service}'")
    
    try:
        # Get request body and headers
        body_bytes = await request.body()
        headers = dict(request.headers)
        
        # Enqueue the message
        enqueue_result = await enqueuer.enqueue_message(
            service=service,
            method=request.method,
            body=body_bytes,
            headers=headers
        )
        
        if not enqueue_result.success:
            # Handle enqueue failures
            status_code = enqueue_result.status_code or 500
            error_message_detail = enqueue_result.error_message or "Unknown error"
            metrics_recorder.record_response(service, status_code)
            error_message = get_error_message_for_status(status_code)
            return create_error_response(error_message, error_message_detail, status_code)
        
        # Wait for response in synchronous mode
        try:
            proxy_result = await enqueuer.wait_for_response(enqueue_result.correlation_id, service)
            metrics_recorder.record_response(service, proxy_result.status_code)
            return Response(
                content=proxy_result.body, 
                headers=proxy_result.headers, 
                status_code=proxy_result.status_code
            )
        except asyncio.TimeoutError:
            # Handle timeout
            status_code = 504
            metrics_recorder.record_response(service, status_code)
            return create_error_response("Gateway Timeout", "Timeout waiting for service response", status_code)
            
    except Exception as e:
        # Handle unexpected errors
        metrics_recorder.record_failure(service)
        status_code = 500
        metrics_recorder.record_response(service, status_code)
        logger.error(f"Error proxying request for service '{service}': {e}")
        return create_error_response("Internal Server Error", str(e), status_code)
    finally:
        metrics_recorder.record_latency(service, time.time() - start)