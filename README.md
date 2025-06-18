# proxymq

proxymq is a set of lightweight proxies for bridging HTTP APIs and RabbitMQ message queues. It enables asynchronous, decoupled communication between services using a request/reply pattern, and is designed to be used as sidecars for microservices.

## Components

### Enqueuer
- **Role:** HTTP-to-AMQP proxy
- **Description:** Accepts HTTP requests at `/{service}` and forwards the request to a RabbitMQ queue named `{service}_requests`. Waits for a response on a reply queue and returns it to the HTTP client.
- **Intended use:** As a sidecar to the main API service, allowing HTTP clients to interact with backend services via RabbitMQ.

### Dequeuer
- **Role:** AMQP-to-HTTP proxy
- **Description:** Listens to a RabbitMQ queue named `{SERVICE_NAME}_requests`, forwards each message to a downstream HTTP API, and publishes the response to the reply queue for the original requester.
- **Intended use:** As a sidecar to a downstream service (e.g., addition or subtraction), enabling it to process requests from RabbitMQ and respond via HTTP.

## Environment Variables

### Enqueuer
- `RABBITMQ_HOST`: Hostname of the RabbitMQ server (default: `rabbitmq`)
- `RABBITMQ_PORT`: Port of the RabbitMQ server (default: `5672`)
- `ENQUEUER_REPLY_TIMEOUT`: Timeout (in seconds) to wait for a reply from the downstream service (default: `30`)
- `ENQUEUER_POOL_SIZE`: Connection pool size for RabbitMQ (default: `10`)

### Dequeuer
- `RABBITMQ_HOST`: Hostname of the RabbitMQ server (default: `rabbitmq`)
- `RABBITMQ_PORT`: Port of the RabbitMQ server (default: `5672`)
- `SERVICE_NAME`: Name of the service (e.g., `addition`, `subtraction`)
- `DOWNSTREAM_URL`: URL of the downstream HTTP API to forward requests to
- `DEQUEUER_POOL_SIZE`: Connection pool size for RabbitMQ (default: `10`)

## Docker & Docker Compose

Both services are intended to be run as containers. The recommended way to run the full stack (including RabbitMQ) is with Docker Compose.
See `docker-compose.yml`.

### Usage

To start all services:
```bash
docker-compose up --build
```

- The Enqueuer will be available at [http://localhost:8000/proxy/{service}](http://localhost:8000/proxy/{service})
- The RabbitMQ management UI will be at [http://localhost:15672](http://localhost:15672) (default user/pass: guest/guest)

You can override environment variables in the `docker-compose.yml` as needed for your use case.

Once running, you can test the enqueueing and dequeueing:

```
curl -v http://localhost:8000/google
curl -v http://localhost:8000/
curl -v -H 'Content-Type: application/json' -d '{"url": "https://www.google.com/"}' http://localhost:8000/url_fetcher
```

You can also check out the RabbitMQ dashboard at http://localhost:15672/

## More Information

See the respective subdirectory READMEs ([enqueuer/README.md](enqueuer/README.md), [dequeuer/README.md](dequeuer/README.md)) for more details on each service, including local development and advanced configuration.
