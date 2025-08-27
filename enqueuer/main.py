from fastapi import FastAPI, Request
import aio_pika
import uuid
from settings import settings
from fastapi.responses import Response
import asyncio
import logging
import traceback
from prometheus_client import Counter, Histogram, generate_latest
import time

app = FastAPI()

# Connection pool globals
RABBIT_POOL = None
REPLY_CONNECTION = None  # Dedicated connection for Direct Reply-To
REPLY_CHANNEL = None
REPLY_FUTURES = {}  # correlation_id -> asyncio.Future mapping

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enqueuer")

# Prometheus metrics definitions
REQUESTS_TOTAL = Counter("enqueuer_requests_total", "Total HTTP requests received", ["service", "method"])
REQUESTS_PUBLISHED = Counter("enqueuer_requests_published_total", "Total requests published to RabbitMQ", ["service"])
REQUESTS_FAILED = Counter("enqueuer_requests_failed_total", "Total failed requests", ["service"])
REQUEST_LATENCY = Histogram("enqueuer_request_latency_seconds", "Request processing latency", ["service"])
RESPONSE_CODES = Counter("enqueuer_response_codes_total", "HTTP response codes returned", ["service", "status_code"])
NO_CONSUMERS = Counter("enqueuer_no_consumers_total", "Total requests rejected due to no consumers", ["service"])
QUEUE_NOT_FOUND = Counter("enqueuer_queue_not_found_total", "Total requests rejected due to queue not found", ["service"])
REPLY_TIMEOUTS = Counter("enqueuer_reply_timeouts_total", "Total reply timeouts", ["service"])

# Global reply consumer callback
async def on_reply_message(message: aio_pika.IncomingMessage):
    correlation_id = message.correlation_id
    logger.info(f"Received reply for correlation_id '{correlation_id}' via Direct Reply-To")

    if correlation_id in REPLY_FUTURES:
        future = REPLY_FUTURES.pop(correlation_id)
        if not future.done():
            future.set_result(message)
            logger.info(f"Resolved future for correlation_id '{correlation_id}'")
        else:
            logger.warning(f"Future already done for correlation_id '{correlation_id}'")
    else:
        logger.warning(f"No future found for correlation_id '{correlation_id}' (may have timed out)")

async def setup_direct_reply_consumer():
    """Set up the Direct Reply-To consumer for handling responses"""
    global REPLY_CONNECTION, REPLY_CHANNEL

    # Create dedicated connection for Direct Reply-To (must be same as publish connection)
    REPLY_CONNECTION = await aio_pika.connect_robust(
        host=settings.RABBITMQ_HOST,
        port=settings.RABBITMQ_PORT
    )
    REPLY_CHANNEL = await REPLY_CONNECTION.channel()
    await REPLY_CHANNEL.set_qos(prefetch_count=1000)  # Handle high throughput

    # Start consuming from Direct Reply-To pseudo-queue
    logger.info("Setting up Direct Reply-To consumer on amq.rabbitmq.reply-to")

    # Declare and consume from the Direct Reply-To pseudo-queue
    reply_queue = await REPLY_CHANNEL.declare_queue("amq.rabbitmq.reply-to")
    await reply_queue.consume(on_reply_message, no_ack=True)
    logger.info("Direct Reply-To consumer established")

async def cleanup_abandoned_futures():
    """Background task to clean up abandoned futures to prevent memory leaks"""
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute
            current_time = time.time()
            abandoned_futures = []

            for correlation_id, future in REPLY_FUTURES.items():
                # Check if future has been waiting too long (2x timeout)
                if hasattr(future, '_created_at'):
                    age = current_time - future._created_at
                    if age > (settings.ENQUEUER_REPLY_TIMEOUT * 2):
                        abandoned_futures.append(correlation_id)

            # Clean up abandoned futures
            for correlation_id in abandoned_futures:
                future = REPLY_FUTURES.pop(correlation_id, None)
                if future and not future.done():
                    future.cancel()
                logger.warning(f"Cleaned up abandoned future for correlation_id '{correlation_id}'")

            if abandoned_futures:
                logger.info(f"Cleaned up {len(abandoned_futures)} abandoned futures")

        except Exception as e:
            logger.error(f"Error in cleanup_abandoned_futures: {e}")
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    global RABBIT_POOL
    loop = asyncio.get_event_loop()
    logger.info("Starting up enqueuer and initializing RabbitMQ pool...")
    RABBIT_POOL = aio_pika.pool.Pool(
        lambda: aio_pika.connect_robust(
            host=settings.RABBITMQ_HOST,
            port=settings.RABBITMQ_PORT,
            loop=loop
        ),
        max_size=settings.ENQUEUER_POOL_SIZE
    )
    logger.info(f"RabbitMQ pool initialized with size {settings.ENQUEUER_POOL_SIZE}")

    # Set up Direct Reply-To consumer
    await setup_direct_reply_consumer()
    logger.info("Direct Reply-To consumer setup completed")

    # Start background task to clean up abandoned futures
    asyncio.create_task(cleanup_abandoned_futures())

@app.on_event("shutdown")
async def shutdown_event():
    global RABBIT_POOL, REPLY_CONNECTION, REPLY_CHANNEL, REPLY_FUTURES
    logger.info("Shutting down enqueuer and closing RabbitMQ pool...")

    # Cancel any pending reply futures
    for correlation_id, future in REPLY_FUTURES.items():
        if not future.done():
            future.cancel()
            logger.info(f"Cancelled pending future for correlation_id '{correlation_id}'")
    REPLY_FUTURES.clear()

    # Close reply channel and connection
    if REPLY_CHANNEL is not None:
        await REPLY_CHANNEL.close()
        logger.info("Reply channel closed.")

    if REPLY_CONNECTION is not None:
        await REPLY_CONNECTION.close()
        logger.info("Reply connection closed.")

    # Close pool
    if RABBIT_POOL is not None:
        await RABBIT_POOL.close()
        logger.info("RabbitMQ pool closed.")

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type="text/plain")

@app.api_route("/{service}", methods=["GET", "POST", "PUT"])
async def proxy(service: str, request: Request):
    start = time.time()
    REQUESTS_TOTAL.labels(service=service, method=request.method).inc()
    correlation_id = str(uuid.uuid4())
    service_queue = f"{service}_requests"
    logger.info(f"Received request for service '{service}' with correlation_id '{correlation_id}'")
    status_code = 500
    try:
        body_bytes = await request.body()
        headers = dict(request.headers)
        headers["x-http-method"] = request.method
        # Use the dedicated Direct Reply-To channel for publishing (CRITICAL requirement)
        logger.info(f"Using Direct Reply-To channel for service '{service}' request")

        # Check if there are any consumers for the service queue using a temporary channel
        async with RABBIT_POOL.acquire() as temp_connection:
            temp_channel = await temp_connection.channel()
            try:
                service_queue_info = await temp_channel.declare_queue(service_queue, passive=True)
                consumer_count = service_queue_info.declaration_result.consumer_count
                logger.info(f"Service queue '{service_queue}' has {consumer_count} consumers")

                if consumer_count == 0:
                    logger.warning(f"No consumers found for service queue '{service_queue}'")
                    NO_CONSUMERS.labels(service=service).inc()
                    RESPONSE_CODES.labels(service=service, status_code="503").inc()
                    return Response(
                        content=b'{"error": "Service unavailable", "detail": "No consumers available for this service"}',
                        status_code=503,
                        headers={"content-type": "application/json"}
                    )
            except Exception as e:
                logger.warning(f"Service queue '{service_queue}' does not exist or is inaccessible: {e}")
                QUEUE_NOT_FOUND.labels(service=service).inc()
                RESPONSE_CODES.labels(service=service, status_code="404").inc()
                return Response(
                    content=b'{"error": "Service not found", "detail": "Service queue does not exist"}',
                    status_code=404,
                    headers={"content-type": "application/json"}
                )

        # Create future for this request and register it
        response_future = asyncio.get_event_loop().create_future()
        response_future._created_at = time.time()  # Add timestamp for cleanup
        REPLY_FUTURES[correlation_id] = response_future

        # Publish message using Direct Reply-To pattern on the SAME channel as consumer
        await REPLY_CHANNEL.default_exchange.publish(
            aio_pika.Message(
                body=body_bytes,
                correlation_id=correlation_id,
                reply_to="amq.rabbitmq.reply-to",  # Use Direct Reply-To
                headers=headers,
            ),
            routing_key=service_queue
        )
        REQUESTS_PUBLISHED.labels(service=service).inc()
        logger.info(f"Published message to '{service_queue}' with correlation_id '{correlation_id}' using Direct Reply-To")

        # Wait for response via Direct Reply-To consumer
        logger.info(f"Waiting for Direct Reply-To response with correlation_id '{correlation_id}' and timeout {settings.ENQUEUER_REPLY_TIMEOUT}s")
        try:
            incoming_message = await asyncio.wait_for(response_future, timeout=settings.ENQUEUER_REPLY_TIMEOUT)
            logger.info(f"Received Direct Reply-To response for correlation_id '{correlation_id}'")
            response_body = incoming_message.body
            response_headers = incoming_message.headers or {}
            # Properly handle the status code from the dequeuer response
            status_code_str = response_headers.pop('x-status-code', '200')
            try:
                status_code = int(status_code_str)
            except (ValueError, TypeError):
                logger.warning(f"Invalid status code '{status_code_str}' received, defaulting to 200")
                status_code = 200
            # No need to ack - Direct Reply-To uses auto_ack=True
        except asyncio.TimeoutError:
            # Clean up the future mapping on timeout
            REPLY_FUTURES.pop(correlation_id, None)
            logger.error(f"Timeout waiting for Direct Reply-To response for correlation_id '{correlation_id}'")
            REPLY_TIMEOUTS.labels(service=service).inc()
            status_code = 504  # Gateway Timeout
            RESPONSE_CODES.labels(service=service, status_code=str(status_code)).inc()
            return Response(
                content=b'{"error": "Gateway Timeout", "detail": "Timeout waiting for service response"}',
                status_code=status_code,
                headers={"content-type": "application/json"}
            )

        # Success case - this moved outside the try block
        RESPONSE_CODES.labels(service=service, status_code=str(status_code)).inc()
        return Response(content=response_body, headers=response_headers, status_code=int(status_code))
    except Exception as e:
        # Clean up any remaining future mapping
        REPLY_FUTURES.pop(correlation_id, None)
        REQUESTS_FAILED.labels(service=service).inc()
        RESPONSE_CODES.labels(service=service, status_code=str(status_code)).inc()
        logger.error(f"Error proxying request for service '{service}' with correlation_id '{correlation_id}': {e}")
        logger.error(traceback.format_exc())
        return Response(content=b"Internal Server Error", status_code=500)
    finally:
        REQUEST_LATENCY.labels(service=service).observe(time.time() - start)
