from fastapi import FastAPI, Request
import aio_pika
import uuid
from settings import settings
from fastapi.responses import Response
import asyncio
import logging
import traceback

app = FastAPI()

# Connection pool globals
RABBIT_POOL = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enqueuer")

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

@app.api_route("/{service}", methods=["GET", "POST", "PUT"])
async def proxy(service: str, request: Request):
    correlation_id = str(uuid.uuid4())
    reply_queue = f"reply_{correlation_id}"
    service_queue = f"{service}_requests"
    logger.info(f"Received request for service '{service}' with correlation_id '{correlation_id}'")
    try:
        body_bytes = await request.body()
        # Add HTTP method to headers
        headers = dict(request.headers)
        headers["x-http-method"] = request.method
        # Use a pooled connection
        async with RABBIT_POOL.acquire() as connection:
            channel = await connection.channel()
            logger.info(f"Channel acquired for service '{service}', declaring reply queue '{reply_queue}'")
            # Declare reply queue (auto-delete)
            reply_q = await channel.declare_queue(reply_queue, exclusive=True, auto_delete=True)
            # Publish request to service queue
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=body_bytes,
                    correlation_id=correlation_id,
                    reply_to=reply_queue,
                    headers=headers,
                ),
                routing_key=service_queue
            )
            logger.info(f"Published message to '{service_queue}' with correlation_id '{correlation_id}'")
            # Wait for response using a consumer
            logger.info(f"Waiting for response on reply queue '{reply_queue}' with timeout {settings.ENQUEUER_REPLY_TIMEOUT}s (consume mode)")
            response_future = asyncio.get_event_loop().create_future()
            
            async def on_message(message: aio_pika.IncomingMessage):
                if not response_future.done():
                    response_future.set_result(message)
            
            consumer_tag = await reply_q.consume(on_message)
            try:
                incoming_message = await asyncio.wait_for(response_future, timeout=settings.ENQUEUER_REPLY_TIMEOUT)
                logger.info(f"Received message from reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
                response_body = incoming_message.body
                response_headers = incoming_message.headers or {}
                await incoming_message.ack()
                logger.info(f"Acknowledged message from reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
            finally:
                await reply_q.cancel(consumer_tag)
                logger.info(f"Cancelled consumer on reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
                await reply_q.delete()
                logger.info(f"Deleted reply queue '{reply_queue}' for correlation_id '{correlation_id}' (consume mode)")
            return Response(content=response_body, headers=response_headers)
    except Exception as e:
        logger.error(f"Error proxying request for service '{service}' with correlation_id '{correlation_id}': {e}")
        logger.error(traceback.format_exc())
        return Response(content=b"Internal Server Error", status_code=500)
