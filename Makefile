# Docker registry configuration
ENQUEUER_IMAGE := 192.168.2.1:5000/enqueuer:latest
DEQUEUER_IMAGE := 192.168.2.1:5000/dequeuer:latest
URL_FETCHER_IMAGE := 192.168.2.1:5000/url-fetcher:latest
TEST_SERVICE_IMAGE := 192.168.2.1:5000/test-service:latest

# Build targets
build-push-enqueuer:
	docker build -t $(ENQUEUER_IMAGE) ./enqueuer
	docker push $(ENQUEUER_IMAGE)

build-push-dequeuer:
	docker build -t $(DEQUEUER_IMAGE) ./dequeuer
	docker push $(DEQUEUER_IMAGE)

build-push-url-fetcher:
	docker build -t $(URL_FETCHER_IMAGE) ./url-fetcher
	docker push $(URL_FETCHER_IMAGE)

build-push-test-service:
	docker build -t $(TEST_SERVICE_IMAGE) ./test-service
	docker push $(TEST_SERVICE_IMAGE)

build-push-all: build-push-enqueuer build-push-dequeuer build-push-url-fetcher build-push-test-service
