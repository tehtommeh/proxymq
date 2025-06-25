# IDEAS.md

## 1. Dead Letter Queue (DLQ) Handling

### Overview
- Ensure that failed messages (e.g., if a microservice dies mid-processing) are not lost and can be retried or inspected later.
- If a dequeuer or microservice crashes before acknowledging a message, RabbitMQ will requeue it. However, repeated failures can cause messages to get stuck in a retry loop unless a DLQ is used.

### Best Practices & Options
- **Dead Letter Exchange (DLX):**
  - Configure the main queue with a DLX and a `x-dead-letter-routing-key`.
  - Set a `x-max-delivery-count` (or use a custom header/counter) to limit retries.
  - After N failed delivery attempts, RabbitMQ moves the message to the DLQ.
- **Message TTL:**
  - Optionally, set a TTL on the queue or per-message, so messages that can't be processed in time are dead-lettered.
- **Manual Nack/Reject:**
  - If processing fails, explicitly `nack()` the message with `requeue=False` to send it to the DLQ.

### Implementation Suggestions
- Configure DLQ for each service queue in deployment/queue declaration.
- Track delivery attempts (RabbitMQ can do this with `x-death` header).
- Monitor the DLQ and alert if messages accumulate.
- Optionally, build a DLQ reprocessor to inspect, replay, or discard dead-lettered messages.

### Benefits
- Prevents message loss.
- Enables debugging and replay of failed requests.
- Avoids infinite retry loops.

---

## 2. Prometheus Metrics and Alerting

### What to Instrument
- **Enqueuer:**
  - Number of requests received, published, and failed.
  - Time from HTTP request to message published.
  - Time from request to response (end-to-end latency).
  - Response codes returned to clients.
- **Dequeuer:**
  - Number of messages processed, failed, and acked/nacked.
  - Processing time per message/batch.
  - Time from message enqueue to dequeue (queue latency).
  - Downstream response codes.
- **RabbitMQ:**
  - Queue length, message rates, consumer counts, DLQ size, etc. (via RabbitMQ Prometheus exporter).

### How to Add Prometheus Metrics
- Use the `prometheus_client` library in both enqueuer and dequeuer.
- Expose a `/metrics` endpoint in each service (FastAPI makes this easy).
- Use counters, histograms, and gauges for extensibility.
- Example metrics:
  - `enqueuer_requests_total{service=...}`
  - `enqueuer_request_latency_seconds{service=...}`
  - `dequeuer_processed_total{service=...}`
  - `dequeuer_processing_latency_seconds{service=...}`
  - `rabbitmq_queue_length{queue=...}` (from RabbitMQ exporter)

### Extensibility
- Use labels/tags for service, queue, status, etc.
- Add new metrics as needed—Prometheus is schema-less.

### Alerting
- Set up Prometheus Alertmanager rules for:
  - High queue length (backlog)
  - High error rates
  - High DLQ size
  - Slow processing times

### Benefits
- Provides observability into system health and performance.
- Enables autoscaling (e.g., with KEDA) based on real metrics.
- Early detection of issues via alerting.

---

## 3. Timeouts and Circuit Breakers

### Timeouts
- **Enqueuer:**
  - Timeout waiting for a reply from the dequeuer/microservice (already present as `ENQUEUER_REPLY_TIMEOUT`).
  - Timeout for publishing to RabbitMQ.
- **Dequeuer:**
  - Timeout for downstream HTTP requests (should be set in `httpx`).
  - Timeout for batch processing.
- **RabbitMQ:**
  - Message TTL (time a message can stay in the queue before being dead-lettered).

### Circuit Breakers
- **Purpose:** Prevent overwhelming a failing downstream service by temporarily blocking requests after repeated failures.
- **How to Implement:**
  - Use a library like `pybreaker` in the dequeuer.
  - Track failures to the downstream microservice; after N failures, open the circuit and fail fast for a cooldown period.
  - Optionally, expose circuit breaker state via metrics.

### What's Useful
- **Timeouts:** Prevent stuck requests and resource leaks.
- **Circuit breakers:** Protect microservices and provide fast failure, improving system stability.
- **Combine with retries:** Retry failed requests a limited number of times before dead-lettering.

### Benefits
- Prevents resource exhaustion and cascading failures.
- Improves system resilience and reliability.
- Provides fast feedback to clients when downstream is unhealthy.

---

## Summary Table

| Enhancement         | What to Do                                                                 | Benefits                                 |
|---------------------|----------------------------------------------------------------------------|------------------------------------------|
| DLQ                 | Configure DLX, max delivery, monitor DLQ, reprocessor                      | No lost messages, easier debugging       |
| Prometheus Metrics  | Add metrics endpoints, instrument key events, use RabbitMQ exporter        | Observability, autoscaling, alerting     |
| Timeouts            | Set timeouts for all network/queue ops                                     | Prevent stuck resources, fast failure    |
| Circuit Breakers    | Use pybreaker or similar in dequeuer, expose state in metrics              | Prevent overload, improve resilience     | 