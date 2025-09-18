import aio_pika
import uuid
import asyncio
import logging
import time
import traceback
from typing import Optional, Dict, Any, Union
from prometheus_client import Counter, Histogram
from models import EnqueueResult, ProxyResult
from settings import settings

logger = logging.getLogger("enqueuer")

class Enqueuer:
    """Core enqueuer functionality shared between sync and async modes"""

    def __init__(self,
                 rabbit_pool: aio_pika.pool.Pool[aio_pika.RobustConnection],
                 reply_connection: aio_pika.RobustConnection,
                 reply_channel: aio_pika.RobustChannel,
                 management_channel: aio_pika.RobustChannel,
                 reply_queue: aio_pika.Queue):
        self.rabbit_pool = rabbit_pool
        self.reply_connection = reply_connection
        self.reply_channel = reply_channel
        self.management_channel = management_channel
        self.reply_queue = reply_queue
        self.reply_futures: Dict[str, asyncio.Future[aio_pika.IncomingMessage]] = {}  # correlation_id -> asyncio.Future mapping
        self.future_timestamps: Dict[str, float] = {}  # correlation_id -> creation timestamp mapping
        self.cleanup_task: Optional[asyncio.Task[None]] = None
        
        # Prometheus metrics
        self.requests_total = Counter("enqueuer_requests_total", "Total HTTP requests received", ["service", "method"])
        self.requests_published = Counter("enqueuer_requests_published_total", "Total requests published to RabbitMQ", ["service"])
        self.requests_failed = Counter("enqueuer_requests_failed_total", "Total failed requests", ["service"])
        self.request_latency = Histogram("enqueuer_request_latency_seconds", "Request processing latency", ["service"])
        self.response_codes = Counter("enqueuer_response_codes_total", "HTTP response codes returned", ["service", "status_code"])
        self.no_consumers = Counter("enqueuer_no_consumers_total", "Total requests rejected due to no consumers", ["service"])
        self.queue_not_found = Counter("enqueuer_queue_not_found_total", "Total requests rejected due to queue not found", ["service"])
        self.reply_timeouts = Counter("enqueuer_reply_timeouts_total", "Total reply timeouts", ["service"])

        # Start cleanup task and reply consumer
        self.start_cleanup_task()
        asyncio.create_task(self._start_reply_consumer())


    async def _on_reply_message(self, message: aio_pika.abc.AbstractIncomingMessage):
        """Global reply consumer callback"""
        correlation_id = message.correlation_id
        logger.info(f"Received reply for correlation_id '{correlation_id}' via Direct Reply-To")

        if correlation_id in self.reply_futures:
            future = self.reply_futures.pop(correlation_id)
            if not future.done():
                future.set_result(message)
                logger.info(f"Resolved future for correlation_id '{correlation_id}'")
            else:
                logger.warning(f"Future already done for correlation_id '{correlation_id}'")
        else:
            logger.warning(f"No future found for correlation_id '{correlation_id}' (may have timed out)")

    async def _cleanup_abandoned_futures(self):
        """Background task to clean up abandoned futures to prevent memory leaks"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                current_time = time.time()
                abandoned_futures: list[str] = []

                for correlation_id, future in self.reply_futures.items():
                    # Check if future has been waiting too long (2x timeout)
                    if correlation_id in self.future_timestamps:
                        age = current_time - self.future_timestamps[correlation_id]
                        if age > (settings.ENQUEUER_REPLY_TIMEOUT * 2):
                            abandoned_futures.append(correlation_id)

                # Clean up abandoned futures
                for correlation_id in abandoned_futures:
                    future = self.reply_futures.pop(correlation_id, None)
                    self.future_timestamps.pop(correlation_id, None)
                    if future and not future.done():
                        future.cancel()
                    logger.warning(f"Cleaned up abandoned future for correlation_id '{correlation_id}'")

                if abandoned_futures:
                    logger.info(f"Cleaned up {len(abandoned_futures)} abandoned futures")

            except Exception as e:
                logger.error(f"Error in cleanup_abandoned_futures: {e}")
                await asyncio.sleep(60)

    def start_cleanup_task(self):
        """Start the background cleanup task"""
        self.cleanup_task = asyncio.create_task(self._cleanup_abandoned_futures())

    async def _start_reply_consumer(self):
        """Start consuming from the reply queue"""
        await self.reply_queue.consume(self._on_reply_message, no_ack=True)
        logger.info("Direct Reply-To consumer established")

    async def check_service_availability(self, service: str) -> tuple[bool, Optional[str], Optional[int]]:
        """Check if service queue exists and has consumers"""
        service_queue = f"{service}_requests"

        try:
            if self.management_channel is None:
                raise RuntimeError("Management channel not established")
            service_queue_info = await self.management_channel.declare_queue(service_queue, passive=True)
            consumer_count = service_queue_info.declaration_result.consumer_count
            logger.info(f"Service queue '{service_queue}' has {consumer_count} consumers")

            if consumer_count == 0:
                logger.warning(f"No consumers found for service queue '{service_queue}'")
                self.no_consumers.labels(service=service).inc()
                return False, "No consumers available for this service", 503
                
            return True, None, None
            
        except Exception as e:
            logger.warning(f"Service queue '{service_queue}' does not exist or is inaccessible: {e}")
            self.queue_not_found.labels(service=service).inc()
            return False, "Service queue does not exist", 404

    async def enqueue_message(self, service: str, method: str, body: bytes, headers: Dict[str, Any]) -> EnqueueResult:
        """Enqueue a message to RabbitMQ (used by both sync and async modes)"""
        correlation_id = str(uuid.uuid4())
        service_queue = f"{service}_requests"
        
        try:
            # Update metrics
            self.requests_total.labels(service=service, method=method).inc()
            
            # Check service availability
            available, error_msg, status_code = await self.check_service_availability(service)
            if not available:
                return EnqueueResult(correlation_id=correlation_id, success=False, 
                                   error_message=error_msg, status_code=status_code)
            
            # Add HTTP method to headers
            headers = dict(headers)
            headers["x-http-method"] = method
            
            # Publish message using Direct Reply-To pattern
            if self.reply_channel is None:
                raise RuntimeError("Reply channel not established")
            await self.reply_channel.default_exchange.publish(
                aio_pika.Message(
                    body=body,
                    correlation_id=correlation_id,
                    reply_to="amq.rabbitmq.reply-to",  # Use Direct Reply-To
                    headers=headers,
                ),
                routing_key=service_queue
            )
            
            self.requests_published.labels(service=service).inc()
            logger.info(f"Published message to '{service_queue}' with correlation_id '{correlation_id}' using Direct Reply-To")
            
            return EnqueueResult(correlation_id=correlation_id, success=True)
            
        except Exception as e:
            logger.error(f"Error enqueuing message for service '{service}' with correlation_id '{correlation_id}': {e}")
            logger.error(traceback.format_exc())
            self.requests_failed.labels(service=service).inc()
            return EnqueueResult(correlation_id=correlation_id, success=False, 
                               error_message="Internal server error", status_code=500)

    async def wait_for_response(self, correlation_id: str, service: str) -> ProxyResult:
        """Wait for response in synchronous mode"""
        # Create future for this request and register it
        response_future: asyncio.Future[aio_pika.abc.AbstractIncomingMessage] = asyncio.get_event_loop().create_future()
        self.reply_futures[correlation_id] = response_future
        self.future_timestamps[correlation_id] = time.time()  # Track timestamp for cleanup

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
                
            return ProxyResult(body=response_body, headers=response_headers, status_code=status_code)
            
        except asyncio.TimeoutError:
            # Clean up the future mapping on timeout
            self.reply_futures.pop(correlation_id, None)
            self.future_timestamps.pop(correlation_id, None)
            logger.error(f"Timeout waiting for Direct Reply-To response for correlation_id '{correlation_id}'")
            self.reply_timeouts.labels(service=service).inc()
            raise

    async def shutdown(self):
        """Clean up resources"""
        logger.info("Shutting down enqueuer and closing RabbitMQ pool...")

        # Cancel cleanup task
        if self.cleanup_task is not None:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass

        # Cancel any pending reply futures
        for correlation_id, future in self.reply_futures.items():
            if not future.done():
                future.cancel()
                logger.info(f"Cancelled pending future for correlation_id '{correlation_id}'")
        self.reply_futures.clear()
        self.future_timestamps.clear()

        # Close management channel
        if self.management_channel is not None:
            await self.management_channel.close()
            logger.info("Management channel closed.")

        # Close reply channel and connection
        if self.reply_channel is not None:
            await self.reply_channel.close()
            logger.info("Reply channel closed.")

        if self.reply_connection is not None:
            await self.reply_connection.close()
            logger.info("Reply connection closed.")

        # Close pool
        if self.rabbit_pool is not None:
            await self.rabbit_pool.close()
            logger.info("RabbitMQ pool closed.")


async def create_enqueuer() -> Enqueuer:
    """Factory function to create and initialize an Enqueuer instance"""
    loop = asyncio.get_event_loop()
    logger.info("Creating enqueuer and initializing RabbitMQ pool...")

    # Initialize connection pool
    rabbit_pool = aio_pika.pool.Pool(
        lambda: aio_pika.connect_robust(
            host=settings.RABBITMQ_HOST,
            port=settings.RABBITMQ_PORT,
            loop=loop
        ),
        max_size=settings.ENQUEUER_POOL_SIZE
    )
    logger.info(f"RabbitMQ pool initialized with size {settings.ENQUEUER_POOL_SIZE}")

    # Set up Direct Reply-To consumer
    reply_connection = await aio_pika.connect_robust(
        host=settings.RABBITMQ_HOST,
        port=settings.RABBITMQ_PORT
    )
    reply_channel = await reply_connection.channel()
    await reply_channel.set_qos(prefetch_count=1000)  # Handle high throughput

    # Start consuming from Direct Reply-To pseudo-queue
    logger.info("Setting up Direct Reply-To consumer on amq.rabbitmq.reply-to")
    reply_queue = await reply_channel.declare_queue("amq.rabbitmq.reply-to")

    # Set up management channel for consumer count checks
    management_channel = await reply_connection.channel()
    await management_channel.set_qos(prefetch_count=1)
    logger.info("Management channel established for consumer count checks")

    # Create the enqueuer instance with all dependencies
    enqueuer = Enqueuer(
        rabbit_pool=rabbit_pool,
        reply_connection=reply_connection,
        reply_channel=reply_channel,
        management_channel=management_channel,
        reply_queue=reply_queue
    )

    logger.info("Enqueuer created and initialized successfully")
    return enqueuer
