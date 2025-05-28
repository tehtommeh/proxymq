from fastapi import FastAPI, Request
import aio_pika
import uuid
from settings import settings
from fastapi.responses import Response
import asyncio
import logging

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

@app.post("/proxy/{service}")
async def proxy(service: str, request: Request):
    correlation_id = str(uuid.uuid4())
    reply_queue = f"reply_{correlation_id}"
    logger.info(f"Received request for service '{service}' with correlation_id '{correlation_id}'")
    try:
        body_bytes = await request.body()
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
                    headers=request.headers,
                ),
                routing_key=f"{service}_requests"
            )
            logger.info(f"Published message to '{service}_requests' with correlation_id '{correlation_id}'")
            # Wait for response
            incoming_message = await reply_q.get(timeout=settings.ENQUEUER_REPLY_TIMEOUT)
            response_body = incoming_message.body
            response_headers = incoming_message.headers or {}
            await incoming_message.ack()
            await reply_q.delete()
            logger.info(f"Received response for correlation_id '{correlation_id}' from service '{service}'")
            return Response(content=response_body, headers=response_headers)
    except Exception as e:
        logger.error(f"Error proxying request for service '{service}' with correlation_id '{correlation_id}': {e}")
        return Response(content=b"Internal Server Error", status_code=500)
