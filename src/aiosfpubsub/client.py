import asyncio
import io
import logging
from typing import Any, AsyncGenerator, Callable
from urllib.parse import urlparse

import avro.io
import avro.schema
import certifi
import grpc
import httpx
from defusedxml import ElementTree as et

import aiosfpubsub.pubsub_api_pb2 as pb2
import aiosfpubsub.pubsub_api_pb2_grpc as pb2_grpc

logger = logging.getLogger(__name__)


class Client:
    """Class with helpers to use the Salesforce Pub/Sub API."""

    json_schema_dict: dict[str, Any] = {}

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        grpc_host: str,
        grpc_port: int,
        api_version: str = "57.0",
    ) -> None:
        self.url: str = url
        self.username: str = username
        self.password: str = password
        self.metadata: tuple[tuple[str, str]] | None = None
        grpc_host: str = grpc_host
        grpc_port: int = grpc_port
        self.pubsub_url: str = f"{grpc_host}:{grpc_port}"
        channel = grpc.secure_channel(
            self.pubsub_url, self._default_channel_credentials()
        )
        self.stub = pb2_grpc.PubSubStub(channel)
        self.session_id: str | None = None
        self.pb2: pb2 = pb2
        self.api_version: str = api_version

        # self.auth()

    @staticmethod
    def _default_channel_credentials() -> grpc.ChannelCredentials:
        with open(certifi.where(), "rb") as f:
            return grpc.ssl_channel_credentials(f.read())

    def _generate_auth_xml(self) -> str:
        """Generate XML for authentication request."""
        return (
            f"<soapenv:Envelope xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/' "
            "xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' "
            "xmlns:urn='urn:partner.soap.sforce.com'><soapenv:Body>"
            f"<urn:login><urn:username><![CDATA[{self.username}]]></urn:username>"
            f"<urn:password><![CDATA[{self.password}]]></urn:password></urn:login>"
            "</soapenv:Body></soapenv:Envelope>"
        )

    def _process_auth_response(self, response_content: bytes) -> None:
        """Process the authentication response."""
        try:
            response_xml = et.fromstring(response_content.decode("utf-8"))
            result = response_xml.find(".//{*}result")
            self.url = urlparse(result.find("{*}serverUrl").text).netloc
            self.session_id = result.find("{*}sessionId").text
            self.tenant_id = result.find("{*}userInfo/{*}organizationId").text
            self.metadata = (
                ("accesstoken", self.session_id),
                ("instanceurl", self.url),
                ("tenantid", self.tenant_id),
            )
        except Exception as e:
            logger.error("Error processing auth response", exc_info=e)
            raise

    def auth(self) -> None:
        try:
            response: httpx.Response = httpx.post(
                f"{self.url}/services/Soap/u/{self.api_version}/",
                content=self._generate_auth_xml(),
                headers={"content-type": "text/xml", "SOAPAction": "Login"},
            )
            self._process_auth_response(response.content)
        except httpx.HTTPStatusError as e:
            logger.error("Failed to authenticate", exc_info=e)
            raise

    async def fetch_req_stream(
        self, topic: str, replay_type: str, replay_id: bytes, num_requested: int
    ) -> AsyncGenerator[pb2.FetchRequest, None]:
        while True:
            yield self.make_fetch_request(topic, replay_type, replay_id, num_requested)
            await asyncio.sleep(5)

    async def subscribe(
        self,
        topic: str,
        replay_type: str,
        replay_id: bytes,
        num_requested: int,
        callback: Callable[[pb2.ConsumerEvent, "Client"], None],
    ) -> None:
        async with grpc.aio.secure_channel(
            self.pubsub_url, self._default_channel_credentials()
        ) as channel:
            stub = pb2_grpc.PubSubStub(channel)
            async for event in stub.Subscribe(
                self.fetch_req_stream(topic, replay_type, replay_id, num_requested),
                metadata=self.metadata,
            ):
                callback(event, self)

    def make_fetch_request(
        self, topic: str, replay_type: str, replay_id: bytes, num_requested: int
    ) -> pb2.FetchRequest:
        """Creates a FetchRequest per the proto file."""
        replay_preset = getattr(pb2.ReplayPreset, replay_type, None)
        if replay_preset is None:
            raise ValueError(f"Invalid Replay Type: {replay_type}")
        return pb2.FetchRequest(
            topic_name=topic,
            replay_preset=replay_preset,
            replay_id=replay_id if replay_id else None,
            num_requested=num_requested,
        )

    def decode(self, schema: str, payload: bytes) -> dict[str, Any]:
        """Uses Avro and the event schema to decode a serialized payload."""
        schema = avro.schema.parse(schema)
        buf = io.BytesIO(payload)
        decoder = avro.io.BinaryDecoder(buf)
        reader = avro.io.DatumReader(schema)
        return reader.read(decoder)

    def get_topic(self, topic_name: str) -> pb2.TopicInfo:
        """Uses GetTopic RPC to retrieve topic given topic_name."""
        return self.stub.GetTopic(
            pb2.TopicRequest(topic_name=topic_name), metadata=self.metadata
        )

    def get_schema_json(self, schema_id: str):
        """Uses GetSchema RPC to retrieve schema given a schema ID."""
        if (
            schema_id not in self.json_schema_dict
            or self.json_schema_dict[schema_id] is None
        ):
            res = self.stub.GetSchema(
                pb2.SchemaRequest(schema_id=schema_id), metadata=self.metadata
            )
            self.json_schema_dict[schema_id] = res.schema_json
        return self.json_schema_dict[schema_id]
