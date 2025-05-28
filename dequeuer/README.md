# Dequeuer

This service acts as an AMQP-to-HTTP proxy for processing requests from a RabbitMQ queue and forwarding them to a downstream HTTP API. It is intended to be used as a sidecar to a downstream service (e.g., addition or subtraction).

## How it works
- Listens to a RabbitMQ queue named `{SERVICE_NAME}_requests`.
- For each message, makes an HTTP POST to the downstream API (`DOWNSTREAM_URL`).
- Publishes the response to the reply queue for the original requester.

## Environment Variables
- `RABBITMQ_HOST`: Hostname of the RabbitMQ server (default: `rabbitmq`).
- `RABBITMQ_PORT`: Port of the RabbitMQ server (default: `5672`).
- `SERVICE_NAME`: Name of the service (e.g., `addition`, `subtraction`).
- `DOWNSTREAM_URL`: URL of the downstream HTTP API to forward requests to.

## Running Locally
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Docker
This service is intended to be run as a container. See the main project `README.md` for usage. 