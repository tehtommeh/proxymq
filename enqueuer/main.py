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

@app.on_event("shutdown")
async def shutdown_event():
    global RABBIT_POOL
    logger.info("Shutting down enqueuer and closing RabbitMQ pool...")
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
    reply_queue = f"reply_{correlation_id}"
    service_queue = f"{service}_requests"
    logger.info(f"Received request for service '{service}' with correlation_id '{correlation_id}'")
    status_code = 500
    try:
        body_bytes = await request.body()
        headers = dict(request.headers)
        headers["x-http-method"] = request.method
        async with RABBIT_POOL.acquire() as connection:
            channel = await connection.channel()
            logger.info(f"Channel acquired for service '{service}', declaring reply queue '{reply_queue}'")
            
            # Check if there are any consumers for the service queue
            try:
                service_queue_info = await channel.declare_queue(service_queue, passive=True)
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
            
            reply_q = await channel.declare_queue(reply_queue, exclusive=True, auto_delete=True)
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=body_bytes,
                    correlation_id=correlation_id,
                    reply_to=reply_queue,
                    headers=headers,
                ),
                routing_key=service_queue
            )
            REQUESTS_PUBLISHED.labels(service=service).inc()
            logger.info(f"Published message to '{service_queue}' with correlation_id '{correlation_id}'")
            response_future = asyncio.get_event_loop().create_future()
            
            async def on_message(message: aio_pika.IncomingMessage):
                if not response_future.done():
                    response_future.set_result(message)
            
            consumer_tag = await reply_q.consume(on_message)
            logger.info(f"Waiting for response on reply queue '{reply_queue}' with timeout {settings.ENQUEUER_REPLY_TIMEOUT}s (consume mode)")
            try:
                incoming_message = await asyncio.wait_for(response_future, timeout=settings.ENQUEUER_REPLY_TIMEOUT)
                logger.info(f"Received message from reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
                response_body = incoming_message.body
                response_headers = incoming_message.headers or {}
                # Properly handle the status code from the dequeuer response
                status_code_str = response_headers.pop('x-status-code', '200')
                try:
                    status_code = int(status_code_str)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid status code '{status_code_str}' received, defaulting to 200")
                    status_code = 200
                await incoming_message.ack()
                logger.info(f"Acknowledged message from reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
            finally:
                await reply_q.cancel(consumer_tag)
                logger.info(f"Cancelled consumer on reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
                await reply_q.delete()
                logger.info(f"Deleted reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
            RESPONSE_CODES.labels(service=service, status_code=str(status_code)).inc()
            return Response(content=response_body, headers=response_headers, status_code=int(status_code))
    except Exception as e:
        REQUESTS_FAILED.labels(service=service).inc()
        RESPONSE_CODES.labels(service=service, status_code=str(status_code)).inc()
        logger.error(f"Error proxying request for service '{service}' with correlation_id '{correlation_id}': {e}")
        logger.error(traceback.format_exc())
        return Response(content=b"Internal Server Error", status_code=500)
    finally:
        REQUEST_LATENCY.labels(service=service).observe(time.time() - start)
