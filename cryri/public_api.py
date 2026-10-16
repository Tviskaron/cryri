"""Optional, authenticated access to the documented MLSpace Public API.

Credentials and short-lived access tokens stay in process memory.
"""

import time
from urllib.parse import urlparse

from cryri import api
from cryri.api import ApiError


class PublicClient:
    def __init__(self, client_id, client_secret, workspace_id, api_key,
                 origin="https://api.ai.cloud.ru"):
        parsed = urlparse(origin)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ApiError(0, "MLSPACE_API_URL must be an HTTPS API origin without embedded credentials.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ApiError(0, "MLSPACE_API_URL must be an origin, such as https://api.ai.cloud.ru.")
        self.origin = origin.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.workspace_id = workspace_id
        self.api_key = api_key
        self._token = None
        self._token_deadline = 0

    def _authenticate(self):
        response = api._request(
            "POST", "/service_auth", base_url=self.origin + "/public/v2",
            headers={}, json={"client_id": self.client_id, "client_secret": self.client_secret},
            retries=0,
        )
        with response:
            if response.status_code != 200:
                raise ApiError(response.status_code, "MLSpace Public API authentication failed.")
            try:
                token = response.json()["token"]["access_token"]
                if not isinstance(token, str) or not token:
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise ApiError(502, "MLSpace authentication returned no access token.")
        self._token = token
        # Refresh conservatively, and also refresh once on an explicit 401.
        self._token_deadline = time.monotonic() + 180

    def request(self, method, path, version=2, **kwargs):
        if not self._token or time.monotonic() >= self._token_deadline:
            self._authenticate()

        def send():
            return api._request(
                method, path, base_url="{}/public/v{}".format(self.origin, version),
                headers={
                    "authorization": self._token,
                    "x-workspace-id": self.workspace_id,
                    "x-api-key": self.api_key,
                },
                **kwargs,
            )

        response = send()
        if response.status_code == 401:
            response.close()
            self._authenticate()
            response = send()
        if not 200 <= response.status_code < 300:
            with response:
                # Do not expose credential material if an upstream error echoes it.
                detail = response.text[:4000]
                for secret in [self.client_secret, self.api_key, self._token]:
                    if secret:
                        detail = detail.replace(secret, "****")
                raise ApiError(response.status_code, detail)
        return response

    def json(self, method, path, version=2, **kwargs):
        with self.request(method, path, version=version, **kwargs) as response:
            try:
                return response.json()
            except ValueError:
                raise ApiError(502, "MLSpace Public API returned invalid JSON.")
