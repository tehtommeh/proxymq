# URL Fetcher

This is a basic FastAPI application that acts a little like a proxy.

Given a POST request to `/fetch`, it will extract the URL from the request body,
and do a GET request to the URL, returning the response.

There will be another endpoint `/fetch/batch` which accepts a POST request containing
a JSON-array of request bodies matching the POST to `/fetch`. It will batch-process
these requests in the same way as a single POST request to `/fetch`.

`/fetch/batch` will also accept a GET request, which will return a JSON with a `batch_size`
number, which is the size of batches that will be accepted at the POST `/fetch/batch` endpoint.