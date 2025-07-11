import asyncio
import aio_pika
from aio_pika.pool import Pool
from aio_pika.exceptions import QueueEmpty
import httpx
from settings import settings
import logging
from typing import List, Dict, Any
import json
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time
import random

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dequeuer")

# Global connection pool and batch size
RABBIT_POOL = None
MAX_BATCH_SIZE = None

# Prometheus metrics definitions
PROCESSED_TOTAL = Counter("dequeuer_processed_total", "Total messages processed", ["service", "status_code", "batch_size"])
FAILED_TOTAL = Counter("dequeuer_failed_total", "Total failed messages", ["service", "status_code", "batch_size"])
PROCESSING_LATENCY = Histogram("dequeuer_processing_latency_seconds", "Message processing latency", ["service", "status_code", "batch_size"])

# Health check metrics
HEALTH_CHECK_ATTEMPTS = Counter("dequeuer_health_check_attempts_total", "Total health check attempts", ["service"])
HEALTH_CHECK_SUCCESS = Counter("dequeuer_health_check_success_total", "Total successful health checks", ["service"])
HEALTH_CHECK_DURATION = Histogram("dequeuer_health_check_duration_seconds", "Health check latency", ["service", "status_code"])
READY_TIME = Gauge("dequeuer_ready_time_seconds", "Unix timestamp when dequeuer became ready", ["service"])

# Start Prometheus metrics server on port 8001
start_http_server(8001)

async def get_batch_size() -> int:
    """Get the maximum batch size from the downstream service."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(settings.BATCH_DOWNSTREAM_URL)
            response.raise_for_status()
            data = response.json()
            return data.get("batch_size", 1)
        except Exception as e:
            logger.error(f"Failed to get batch size from downstream service: {e}")
            return 1

async def wait_for_downstream_health() -> None:
    """Wait for downstream service to be healthy before processing messages."""
    if not settings.HEALTH_CHECK_URL:
        logger.info("No health check URL configured, skipping health check")
        return
    
    logger.info(f"Starting health check for downstream service at {settings.HEALTH_CHECK_URL}")
    service = settings.SERVICE_NAME
    attempt = 0
    
    while True:
        attempt += 1
        HEALTH_CHECK_ATTEMPTS.labels(service=service).inc()
        
        start_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=settings.HEALTH_CHECK_TIMEOUT) as client:
                response = await client.get(settings.HEALTH_CHECK_URL)
                duration = time.time() - start_time
                
                HEALTH_CHECK_DURATION.labels(service=service, status_code=str(response.status_code)).observe(duration)
                
                if response.status_code == 200:
                    logger.info(f"Health check passed for {settings.HEALTH_CHECK_URL} after {attempt} attempts")
                    HEALTH_CHECK_SUCCESS.labels(service=service).inc()
                    READY_TIME.labels(service=service).set(time.time())
                    return
                else:
                    logger.warning(f"Health check failed with status {response.status_code}: {settings.HEALTH_CHECK_URL}")
                    
        except Exception as e:
            duration = time.time() - start_time
            HEALTH_CHECK_DURATION.labels(service=service, status_code="error").observe(duration)
            logger.warning(f"Health check failed with error: {e}")
        
        # Check if we should stop retrying
        if settings.HEALTH_CHECK_MAX_RETRIES > 0 and attempt >= settings.HEALTH_CHECK_MAX_RETRIES:
            logger.error(f"Health check failed after {attempt} attempts, giving up")
            raise Exception(f"Downstream service health check failed after {attempt} attempts")
        
        # Calculate backoff with jitter (exponential backoff with max 60 seconds)
        backoff_base = min(settings.HEALTH_CHECK_INTERVAL * (2 ** min(attempt - 1, 6)), 60)
        jitter = random.uniform(0.1, 0.5)  # 10-50% jitter
        sleep_time = backoff_base * (1 + jitter)
        
        logger.info(f"Health check attempt {attempt} failed, retrying in {sleep_time:.1f} seconds")
        await asyncio.sleep(sleep_time)

async def process_single_message(client: httpx.AsyncClient, message: aio_pika.IncomingMessage, channel: aio_pika.Channel):
    """Process a single message."""
    correlation_id = getattr(message, 'correlation_id', None)
    logger.info(f"Processing single message with correlation_id={correlation_id}")
    headers = getattr(message, 'headers', {}) or {}
    headers.pop('content-length', None)
    headers.pop('host', None)
    
    request_method = headers.pop('x-http-method', 'POST')
    service = settings.SERVICE_NAME
    start = time.time()
    status_code = 500
    batch_size = 1
    try:
        resp = await client.request(
            method=request_method,
            url=settings.DOWNSTREAM_URL,
            content=message.body,
            headers=headers,
            follow_redirects=True
        )
        status_code = resp.status_code
        response_headers = {k: str(v) for k, v in resp.headers.items()}
        response_headers.pop('content-length', None)
        response_headers.pop('transfer-encoding', None)
        
        # Add the HTTP status code to the headers for the enqueuer
        response_headers['x-status-code'] = str(status_code)
        
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=resp.content,
                correlation_id=correlation_id,
                content_type=resp.headers.get('content-type', None),
                headers=response_headers,
            ),
            routing_key=message.reply_to
        )
        PROCESSED_TOTAL.labels(service=service, status_code=str(status_code), batch_size=str(batch_size)).inc()
        logger.info(f"Response published for correlation_id={correlation_id} with status_code={status_code} to reply_to={message.reply_to}")
    except Exception as e:
        FAILED_TOTAL.labels(service=service, status_code=str(status_code), batch_size=str(batch_size)).inc()
        logger.error(f"Error processing single message: {e}")
        # Send error response back to the reply queue
        error_response = {"status": "error", "detail": str(e)}
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(error_response).encode(),
                correlation_id=correlation_id,
                content_type="application/json",
                headers={
                    "x-status-code": "500",
                    "content-type": "application/json"
                }
            ),
            routing_key=message.reply_to
        )
        logger.info(f"Error response published for correlation_id={correlation_id}")
    finally:
        PROCESSING_LATENCY.labels(service=service, status_code=str(status_code), batch_size=str(batch_size)).observe(time.time() - start)

async def process_batch(client: httpx.AsyncClient, messages: List[aio_pika.IncomingMessage], channel: aio_pika.Channel):
    """Process a batch of messages."""
    if not messages:
        return
    
    logger.info(f"Processing batch of {len(messages)} messages")
    batch_requests = []
    
    for msg in messages:
        headers = getattr(msg, 'headers', {}) or {}
        request_method = headers.get('x-http-method', 'POST')
        correlation_id = getattr(msg, 'correlation_id', None)
        
        # Build request structure with method, headers, and body
        request_data = {
            "method": request_method,
            "headers": {k: v for k, v in headers.items() if k not in ['content-length', 'host', 'x-http-method']},
            "body": msg.body.decode() if msg.body else "",
            "correlation_id": correlation_id
        }
        batch_requests.append(request_data)
    
    batch_request = {"requests": batch_requests}
    
    service = settings.SERVICE_NAME
    start = time.time()
    batch_size = len(messages)
    try:
        response = await client.post(
            settings.BATCH_DOWNSTREAM_URL,
            json=batch_request
        )
        response.raise_for_status()
        results = response.json()
        
        # Process each result and send back to respective reply queues
        for msg, result in zip(messages, results):
            correlation_id = getattr(msg, 'correlation_id', None)
            response_headers = result.get("headers", {}) if result.get("status") == "success" else {}
            
            # Add status code to response headers
            if result.get("status") == "success":
                response_headers["x-status-code"] = str(result.get("status_code", 200))
            else:
                response_headers["x-status-code"] = str(result.get("status_code", 500))
            
            # Handle content properly
            if result.get("status") == "success":
                # For successful responses, content should be handled as text/bytes
                response_content = result.get("content", "")
                if isinstance(response_content, str):
                    response_content = response_content.encode('utf-8')
                status_code = result.get("status_code", 200)
            else:
                # For error responses, encode the error message
                response_content = json.dumps(result).encode('utf-8')
                status_code = result.get("status_code", 500)
                response_headers["content-type"] = "application/json"
            
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=response_content,
                    correlation_id=correlation_id,
                    content_type=response_headers.get('content-type', None),
                    headers=response_headers,
                ),
                routing_key=msg.reply_to
            )
            PROCESSED_TOTAL.labels(service=service, status_code=str(status_code), batch_size=str(batch_size)).inc()
            logger.info(f"Batch response published for correlation_id={correlation_id}")
    except Exception as e:
        for msg in messages:
            FAILED_TOTAL.labels(service=service, status_code="500", batch_size=str(batch_size)).inc()
        logger.error(f"Error processing batch: {e}")
        # In case of error, send error response to all messages in batch
        error_response = {"status": "error", "detail": str(e)}
        for msg in messages:
            correlation_id = getattr(msg, 'correlation_id', None)
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(error_response).encode(),
                    correlation_id=correlation_id,
                    content_type="application/json",
                    headers={
                        "x-status-code": 500,  # Internal Server Error
                        "content-type": "application/json"
                    }
                ),
                routing_key=msg.reply_to
            )
    finally:
        # Use status_code 200 if all succeeded, 500 if any failed (approximation)
        status_code = "200"
        PROCESSING_LATENCY.labels(service=service, status_code=status_code, batch_size=str(batch_size)).observe(time.time() - start)

async def process_queue():
    global RABBIT_POOL, MAX_BATCH_SIZE
    
    if settings.BATCH_MODE:
        MAX_BATCH_SIZE = await get_batch_size()
        logger.info(f"Batch mode enabled with max batch size: {MAX_BATCH_SIZE}")
    
    while True:
        try:
            logger.info("Attempting to acquire RabbitMQ connection from pool...")
            async with RABBIT_POOL.acquire() as connection:
                logger.info("RabbitMQ connection acquired. Setting up channel and queue...")
                channel = await connection.channel()
                queue_name = f"{settings.SERVICE_NAME}_requests"
                queue = await channel.declare_queue(queue_name, durable=True)
                logger.info(f"Declared queue '{queue_name}', waiting for messages...")

                async with httpx.AsyncClient() as client:
                    if not settings.BATCH_MODE:
                        # Single message processing mode
                        async for message in queue:
                            async with message.process():
                                await process_single_message(client, message, channel)
                    else:
                        # Batch processing mode
                        while True:
                            batch = []
                            try:
                                for _ in range(MAX_BATCH_SIZE):
                                    try:
                                        message = await queue.get(timeout=0.1)  # Small timeout to collect batch
                                        if message:
                                            batch.append(message)
                                    except QueueEmpty:
                                        break  # No more messages available right now
                                
                                if batch:
                                    # Process all messages in the batch
                                    await process_batch(client, batch, channel)
                                    # Acknowledge all messages in the batch
                                    for message in batch:
                                        await message.ack()
                                else:
                                    # No messages available, small sleep to prevent tight loop
                                    await asyncio.sleep(0.1)
                            except Exception as e:
                                logger.error(f"Error processing batch: {e}", exc_info=True)
                                # For each message in the failed batch, send an error response and ack
                                error_response = {
                                    "status": "error",
                                    "detail": str(e)
                                }
                                for message in batch:
                                    try:
                                        # Send error response back through reply queue
                                        await channel.default_exchange.publish(
                                            aio_pika.Message(
                                                body=json.dumps(error_response).encode(),
                                                correlation_id=message.correlation_id,
                                                content_type="application/json",
                                                headers={
                                                    "x-status-code": 500,  # Internal Server Error
                                                    "content-type": "application/json"
                                                }
                                            ),
                                            routing_key=message.reply_to
                                        )
                                        # Acknowledge the message since we've handled the error
                                        await message.ack()
                                    except Exception as send_error:
                                        logger.error(f"Error sending error response: {send_error}", exc_info=True)
                                await asyncio.sleep(1)  # Wait a bit before processing more messages
            break
        except Exception as e:
            logger.warning(f"Error in process_queue: {e}", exc_info=True)
            await asyncio.sleep(2)

# Pool setup/teardown
async def startup():
    global RABBIT_POOL
    loop = asyncio.get_event_loop()
    logger.info("Starting up dequeuer and initializing RabbitMQ pool...")
    RABBIT_POOL = Pool(
        lambda: aio_pika.connect_robust(
            host=settings.RABBITMQ_HOST,
            port=settings.RABBITMQ_PORT,
            loop=loop
        ),
        max_size=settings.DEQUEUER_POOL_SIZE
    )
    logger.info(f"RabbitMQ pool initialized with size {settings.DEQUEUER_POOL_SIZE}")

async def shutdown():
    global RABBIT_POOL
    logger.info("Shutting down dequeuer and closing RabbitMQ pool...")
    if RABBIT_POOL is not None:
        await RABBIT_POOL.close()
        logger.info("RabbitMQ pool closed.")

if __name__ == "__main__":
    async def main():
        logger.info("Dequeuer service starting...")
        await startup()
        try:
            # Wait for downstream service to be healthy before processing messages
            await wait_for_downstream_health()
            logger.info("Dequeuer is ready to process messages")
            await process_queue()
        finally:
            await shutdown()
        logger.info("Dequeuer service stopped.")
    asyncio.run(main())