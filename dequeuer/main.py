import asyncio
import aio_pika
from aio_pika.pool import Pool
import httpx
from settings import settings
import logging
from typing import Optional
import json
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dequeuer")


class HealthCheckFailedException(Exception):
    """Raised when downstream health check fails during message processing."""
    pass


class MetricsManager:
    def __init__(self, metrics_port: int):
        self.processed_total = Counter("dequeuer_processed_total", "Total messages processed", ["service", "status_code", "batch_size"])
        self.failed_total = Counter("dequeuer_failed_total", "Total failed messages", ["service", "status_code", "batch_size"])
        self.processing_latency = Histogram("dequeuer_processing_latency_seconds", "Message processing latency", ["service", "status_code", "batch_size"])
        
        self.health_check_attempts = Counter("dequeuer_health_check_attempts_total", "Total health check attempts", ["service"])
        self.health_check_success = Counter("dequeuer_health_check_success_total", "Total successful health checks", ["service"])
        self.health_check_duration = Histogram("dequeuer_health_check_duration_seconds", "Health check latency", ["service", "status_code"])
        self.ready_time = Gauge("dequeuer_ready_time_seconds", "Unix timestamp when dequeuer became ready", ["service"])
        
        start_http_server(metrics_port)
        logger.info(f"Prometheus metrics server started on port {metrics_port}")

    def record_processed(self, service: str, status_code: str, batch_size: str = "1"):
        self.processed_total.labels(service=service, status_code=status_code, batch_size=batch_size).inc()

    def record_failed(self, service: str, status_code: str, batch_size: str = "1"):
        self.failed_total.labels(service=service, status_code=status_code, batch_size=batch_size).inc()

    def record_processing_latency(self, service: str, status_code: str, duration: float, batch_size: str = "1"):
        self.processing_latency.labels(service=service, status_code=status_code, batch_size=batch_size).observe(duration)

    def record_health_check_attempt(self, service: str):
        self.health_check_attempts.labels(service=service).inc()

    def record_health_check_success(self, service: str):
        self.health_check_success.labels(service=service).inc()

    def record_health_check_duration(self, service: str, status_code: str, duration: float):
        self.health_check_duration.labels(service=service, status_code=status_code).observe(duration)

    def set_ready_time(self, service: str):
        self.ready_time.labels(service=service).set(time.time())

class HealthChecker:
    def __init__(self, metrics_manager: MetricsManager):
        self.metrics = metrics_manager

    async def get_batch_size(self) -> int:
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

    async def check_downstream_health(self) -> bool:
        """Check if downstream service is healthy. Returns True if healthy, False otherwise."""
        if not settings.HEALTH_CHECK_URL:
            return True
        
        service = settings.SERVICE_NAME
        self.metrics.record_health_check_attempt(service)
        
        start_time = time.time()
        try:
            async with httpx.AsyncClient(timeout=settings.HEALTH_CHECK_TIMEOUT) as client:
                response = await client.get(settings.HEALTH_CHECK_URL)
                duration = time.time() - start_time
                
                self.metrics.record_health_check_duration(service, str(response.status_code), duration)
                
                if response.status_code == 200:
                    self.metrics.record_health_check_success(service)
                    return True
                else:
                    logger.warning(f"Health check failed with status {response.status_code}: {settings.HEALTH_CHECK_URL}")
                    return False
                    
        except Exception as e:
            duration = time.time() - start_time
            self.metrics.record_health_check_duration(service, "error", duration)
            logger.warning(f"Health check failed with error: {e}")
            return False

    async def wait_for_downstream_health(self) -> None:
        """Wait for downstream service to be healthy before processing messages."""
        if not settings.HEALTH_CHECK_URL:
            logger.info("No health check URL configured, skipping health check")
            return
        
        logger.info(f"Starting health check for downstream service at {settings.HEALTH_CHECK_URL}")
        attempt = 0
        
        while True:
            attempt += 1
            is_healthy = await self.check_downstream_health()
            
            if is_healthy:
                logger.info(f"Health check passed for {settings.HEALTH_CHECK_URL} after {attempt} attempts")
                self.metrics.set_ready_time(settings.SERVICE_NAME)
                return
            
            if settings.HEALTH_CHECK_MAX_RETRIES > 0 and attempt >= settings.HEALTH_CHECK_MAX_RETRIES:
                logger.error(f"Health check failed after {attempt} attempts, giving up")
                raise Exception(f"Downstream service health check failed after {attempt} attempts")
            
            logger.info(f"Health check attempt {attempt} failed, retrying in {settings.HEALTH_CHECK_INTERVAL} seconds")
            await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)

    async def wait_until_healthy(self):
        """Loop until downstream service is healthy."""
        while True:
            is_healthy = await self.check_downstream_health()
            if is_healthy:
                logger.info("Downstream service is healthy, ready to process messages")
                return
            
            logger.warning("Downstream service is unhealthy, waiting before retry...")
            await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)

class MessageProcessor:
    def __init__(self, metrics_manager: MetricsManager):
        self.metrics = metrics_manager

    async def process_single_message(self, client: httpx.AsyncClient, message: aio_pika.IncomingMessage, channel: aio_pika.Channel):
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
        batch_size = "1"
        
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
            self.metrics.record_processed(service, str(status_code), batch_size)
            logger.info(f"Response published for correlation_id={correlation_id} with status_code={status_code} to reply_to={message.reply_to}")
        except Exception as e:
            self.metrics.record_failed(service, str(status_code), batch_size)
            logger.error(f"Error processing single message: {e}", exc_info=True)
            
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
            self.metrics.record_processing_latency(service, str(status_code), time.time() - start, batch_size)

class DequeuerService:
    def __init__(self, metrics_manager: MetricsManager):
        self.metrics = metrics_manager
        self.health_checker = HealthChecker(self.metrics)
        self.message_processor = MessageProcessor(self.metrics)
        self.rabbit_pool: Optional[Pool] = None

    async def consume_messages(self, connection: aio_pika.Connection):
        """Consume messages from the queue. Raises HealthCheckFailedException if downstream becomes unhealthy."""
        channel: Optional[aio_pika.Channel] = None
        consumer_tag: Optional[str] = None
        
        try:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=1)
            
            queue_name = f"{settings.SERVICE_NAME}_requests"
            queue = await channel.declare_queue(queue_name, durable=True)
            
            consumer_tag = f"dequeuer-{uuid.uuid4().hex[:8]}"
            logger.info(f"Starting message consumption with consumer tag: {consumer_tag}")
            
            async with httpx.AsyncClient(timeout=settings.DOWNSTREAM_TIMEOUT) as client:
                async for message in queue.iterator(consumer_tag=consumer_tag):
                    correlation_id = getattr(message, 'correlation_id', 'unknown')
                    logger.info(f"Retrieved message with correlation_id={correlation_id}")
                    
                    is_healthy = await self.health_checker.check_downstream_health()
                    if not is_healthy:
                        logger.warning(f"Downstream unhealthy, nacking message {correlation_id}")
                        
                        await message.nack(requeue=True, multiple=False)
                        logger.info(f"Message {correlation_id} nacked and requeued")
                        
                        try:
                            await queue.cancel(consumer_tag)
                            logger.info(f"Consumer {consumer_tag} cancelled")
                        except Exception as e:
                            logger.warning(f"Error cancelling consumer: {e}")
                        
                        raise HealthCheckFailedException("Downstream service became unhealthy during message processing")
                    
                    logger.info(f"Processing message with correlation_id={correlation_id}")
                    async with message.process():
                        await self.message_processor.process_single_message(client, message, channel)
                    
                    logger.info(f"Completed processing message {correlation_id}")
                    
        except HealthCheckFailedException:
            raise
        except Exception as e:
            logger.error(f"Error in consume session: {e}", exc_info=True)
            raise
            
        finally:
            if channel and not channel.is_closed:
                try:
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

    async def process_queue(self):
        """Main processing loop."""
        while True:
            try:
                await self.health_checker.wait_until_healthy()
                
                logger.info("Getting connection from pool...")
                async with self.rabbit_pool.acquire() as connection:
                    logger.info("Connection acquired, starting consumption session")
                    await self.consume_messages(connection)
                    
            except HealthCheckFailedException:
                logger.info("Downstream became unhealthy during processing, will wait for recovery")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(2)

    async def startup(self):
        """Initialize the service and RabbitMQ pool."""
        loop = asyncio.get_event_loop()
        logger.info("Starting up dequeuer and initializing RabbitMQ pool...")
        self.rabbit_pool = Pool(
            lambda: aio_pika.connect_robust(
                host=settings.RABBITMQ_HOST,
                port=settings.RABBITMQ_PORT,
                loop=loop
            ),
            max_size=settings.DEQUEUER_POOL_SIZE
        )
        logger.info(f"RabbitMQ pool initialized with size {settings.DEQUEUER_POOL_SIZE}")

    async def shutdown(self):
        """Clean up resources."""
        logger.info("Shutting down dequeuer and closing RabbitMQ pool...")
        if self.rabbit_pool is not None:
            await self.rabbit_pool.close()
            logger.info("RabbitMQ pool closed.")

    async def run(self):
        """Run the complete dequeuer service."""
        logger.info("Dequeuer service starting...")
        await self.startup()
        try:
            await self.health_checker.wait_for_downstream_health()
            logger.info("Dequeuer is ready to process messages")
            await self.process_queue()
        finally:
            await self.shutdown()
        logger.info("Dequeuer service stopped.")

if __name__ == "__main__":
    async def main():
        metrics_manager = MetricsManager(metrics_port=8001)
        service = DequeuerService(metrics_manager)
        await service.run()
    
    asyncio.run(main())