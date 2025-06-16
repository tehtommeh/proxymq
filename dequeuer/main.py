import asyncio
import aio_pika
from aio_pika.pool import Pool
import httpx
from settings import settings
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dequeuer")

# Global connection pool
RABBIT_POOL = None

async def process_queue():
    global RABBIT_POOL
    while True:
        try:
            logger.info("Attempting to acquire RabbitMQ connection from pool...")
            # Try to acquire a connection from the pool
            async with RABBIT_POOL.acquire() as connection:
                logger.info("RabbitMQ connection acquired. Setting up channel and queue...")
                channel = await connection.channel()
                queue_name = f"{settings.SERVICE_NAME}_requests"
                queue = await channel.declare_queue(queue_name, durable=True)
                logger.info(f"Declared queue '{queue_name}', waiting for messages...")

                async with httpx.AsyncClient() as client:
                    async for message in queue:
                        async with message.process():
                            correlation_id = getattr(message, 'correlation_id', None)
                            logger.info(f"Received message with correlation_id={correlation_id}")
                            # Get all headers from message properties
                            headers = getattr(message, 'headers', {}) or {}
                            # Remove 'content-length' and 'host' because the HTTP client will set them correctly
                            headers.pop('content-length', None)
                            headers.pop('host', None)
                            
                            # Forward the request to the real downstream API
                            request_method = headers.pop('x-http-method', 'POST')
                            logger.debug(f"Forwarding {request_method} request to downstream: {settings.DOWNSTREAM_URL}")
                            resp = await client.request(
                                method=request_method,
                                url=settings.DOWNSTREAM_URL,
                                content=message.body,
                                headers=headers,
                                follow_redirects=True
                            )
                            # Prepare response headers (convert to dict, remove hop-by-hop headers)
                            response_headers = dict(resp.headers)
                            response_headers.pop('content-length', None)
                            response_headers.pop('transfer-encoding', None)
                            # Publish the response to the reply queue
                            logger.info(f"Publishing response to reply queue '{message.reply_to}' for correlation_id={correlation_id}")
                            await channel.default_exchange.publish(
                                aio_pika.Message(
                                    body=resp.content,
                                    correlation_id=correlation_id,
                                    content_type=resp.headers.get('content-type', None),
                                    headers=response_headers,
                                ),
                                routing_key=message.reply_to
                            )
                            logger.info(f"Response published for correlation_id={correlation_id}")
            break
        except Exception as e:
            logger.warning(f"Waiting for RabbitMQ in dequeuer... Exception: {e}")
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
            await process_queue()
        finally:
            await shutdown()
        logger.info("Dequeuer service stopped.")
    asyncio.run(main())