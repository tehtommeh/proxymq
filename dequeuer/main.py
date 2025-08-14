import asyncio
import aio_pika
from aio_pika.pool import Pool
from aio_pika.exceptions import QueueEmpty
import httpx
from settings import settings
import logging
from typing import List, Optional
import json
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time
import uuid

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dequeuer")

# Global connection pool and batch size
RABBIT_POOL = None
MAX_BATCH_SIZE = None
LAST_HEALTH_CHECK = None

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

async def check_downstream_health() -> bool:
    """Check if downstream service is healthy. Returns True if healthy, False otherwise."""
    if not settings.HEALTH_CHECK_URL:
        return True  # No health check configured, assume healthy
    
    service = settings.SERVICE_NAME
    HEALTH_CHECK_ATTEMPTS.labels(service=service).inc()
    
    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=settings.HEALTH_CHECK_TIMEOUT) as client:
            response = await client.get(settings.HEALTH_CHECK_URL)
            duration = time.time() - start_time
            
            HEALTH_CHECK_DURATION.labels(service=service, status_code=str(response.status_code)).observe(duration)
            
            if response.status_code == 200:
                HEALTH_CHECK_SUCCESS.labels(service=service).inc()
                return True
            else:
                logger.warning(f"Health check failed with status {response.status_code}: {settings.HEALTH_CHECK_URL}")
                return False
                
    except Exception as e:
        duration = time.time() - start_time
        HEALTH_CHECK_DURATION.labels(service=service, status_code="error").observe(duration)
        logger.warning(f"Health check failed with error: {e}")
        return False

async def wait_for_downstream_health() -> None:
    """Wait for downstream service to be healthy before processing messages."""
    if not settings.HEALTH_CHECK_URL:
        logger.info("No health check URL configured, skipping health check")
        return
    
    logger.info(f"Starting health check for downstream service at {settings.HEALTH_CHECK_URL}")
    attempt = 0
    
    while True:
        attempt += 1
        is_healthy = await check_downstream_health()
        
        if is_healthy:
            logger.info(f"Health check passed for {settings.HEALTH_CHECK_URL} after {attempt} attempts")
            READY_TIME.labels(service=settings.SERVICE_NAME).set(time.time())
            return
        
        # Check if we should stop retrying
        if settings.HEALTH_CHECK_MAX_RETRIES > 0 and attempt >= settings.HEALTH_CHECK_MAX_RETRIES:
            logger.error(f"Health check failed after {attempt} attempts, giving up")
            raise Exception(f"Downstream service health check failed after {attempt} attempts")
        
        # Use fixed interval instead of exponential backoff
        logger.info(f"Health check attempt {attempt} failed, retrying in {settings.HEALTH_CHECK_INTERVAL} seconds")
        await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)

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
        logger.error(f"Error processing single message: {e}", exc_info=True)
        # Send error response back to the reply queue
        error_response = {"status": "error", "detail": str(e), "type": type(e).__name__}
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

async def wait_until_healthy():
    """Loop until downstream service is healthy."""
    while True:
        is_healthy = await check_downstream_health()
        if is_healthy:
            logger.info("Downstream service is healthy, ready to process messages")
            return
        
        logger.warning("Downstream service is unhealthy, waiting before retry...")
        await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)

async def consume_messages_with_health_check(connection: aio_pika.Connection):
    """
    Consume messages with health checking. Returns True if should restart, False if error.
    """
    channel: Optional[aio_pika.Channel] = None
    consumer_tag: Optional[str] = None
    
    try:
        # Create a new channel for this consumption session
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        
        queue_name = f"{settings.SERVICE_NAME}_requests"
        queue = await channel.declare_queue(queue_name, durable=True)
        
        # Generate unique consumer tag
        consumer_tag = f"dequeuer-{uuid.uuid4().hex[:8]}"
        logger.info(f"Starting message consumption with consumer tag: {consumer_tag}")
        
        async with httpx.AsyncClient(timeout=settings.DOWNSTREAM_TIMEOUT) as client:
            # Use the iterator with explicit consumer tag
            async for message in queue.iterator(consumer_tag=consumer_tag):
                correlation_id = getattr(message, 'correlation_id', 'unknown')
                logger.info(f"Retrieved message with correlation_id={correlation_id}")
                
                # Check if downstream is healthy before processing
                is_healthy = await check_downstream_health()
                if not is_healthy:
                    logger.warning(f"Downstream unhealthy, nacking message {correlation_id}")
                    
                    # Nack the message first
                    await message.nack(requeue=True, multiple=False)
                    logger.info(f"Message {correlation_id} nacked and requeued")
                    
                    # Cancel the consumer explicitly
                    try:
                        await queue.cancel(consumer_tag)
                        logger.info(f"Consumer {consumer_tag} cancelled")
                    except Exception as e:
                        logger.warning(f"Error cancelling consumer: {e}")
                    
                    # Return True to indicate we should restart after waiting
                    return True
                
                # Process the message
                logger.info(f"Processing message with correlation_id={correlation_id}")
                async with message.process():
                    await process_single_message(client, message, channel)
                
                logger.info(f"Completed processing message {correlation_id}")
        
        # Normal completion (shouldn't happen as iterator is infinite)
        return False
        
    except Exception as e:
        logger.error(f"Error in consume session: {e}", exc_info=True)
        return False
        
    finally:
        # Clean up: close the channel if it exists
        if channel and not channel.is_closed:
            try:
                # Cancel consumer if it's still active
                if consumer_tag and hasattr(channel, '_consumers') and consumer_tag in getattr(channel, '_consumers', {}):
                    try:
                        queue_name = f"{settings.SERVICE_NAME}_requests"
                        queue = await channel.get_queue(queue_name)
                        await queue.cancel(consumer_tag)
                    except Exception as e:
                        logger.debug(f"Consumer already cancelled or error during cancel: {e}")
                
                await channel.close()
                logger.info("Channel closed")
            except Exception as e:
                logger.warning(f"Error closing channel: {e}")

async def process_queue():
    global RABBIT_POOL
    
    while True:
        try:
            # Step 1: Wait until downstream is healthy
            await wait_until_healthy()
            
            # Step 2: Get connection from pool
            logger.info("Getting connection from pool...")
            async with RABBIT_POOL.acquire() as connection:
                logger.info("Connection acquired, starting consumption session")
                
                # Step 3: Consume messages (this function handles its own channel)
                should_restart = await consume_messages_with_health_check(connection)
                
                if should_restart:
                    logger.info("Consumption session ended due to health check failure, will restart after health recovery")
                    # Small delay to ensure clean disconnection
                    await asyncio.sleep(0.5)
                    # Loop will continue and wait for health again
                else:
                    logger.warning("Consumption session ended unexpectedly, restarting...")
                    await asyncio.sleep(2)
                    
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
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
            # Initial health check
            await wait_for_downstream_health()
            logger.info("Dequeuer is ready to process messages")
            await process_queue()
        finally:
            await shutdown()
        logger.info("Dequeuer service stopped.")
    
    asyncio.run(main())