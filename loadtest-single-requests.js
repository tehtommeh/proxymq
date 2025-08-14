import http from 'k6/http';
import { check } from 'k6';

export let options = {
  vus: 2,
  duration: '60s',
};

export default function () {
  const url = 'http://localhost:8888/test_service';
  const payload = JSON.stringify({
    data: '1'
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const response = http.post(url, payload, params);

  // Add checks to validate the response
  check(response, {
    'status is 200': (r) => r.status === 200,
    'response time < 500ms': (r) => r.timings.duration < 500,
  });
}
