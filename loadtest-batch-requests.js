import http from 'k6/http';
import { check } from 'k6';

export let options = {
  vus: 5,
  duration: '10s',
};

export default function () {
  const url = 'http://localhost:8888/url_fetcher';
  const payload = "https://httpbin.org/get";

  const response = http.get(url, payload);

  // Add checks to validate the response
  check(response, {
    'status is 200': (r) => r.status === 200,
    'response time < 500ms': (r) => r.timings.duration < 500,
  });
}