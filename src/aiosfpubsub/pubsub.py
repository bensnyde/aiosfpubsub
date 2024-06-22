import asyncio
import logging
import xml.etree.ElementTree as et
from typing import Any
from urllib.parse import ParseResult, urlparse

import avro.io
import avro.schema
import certifi
import grpc
import httpx

import aiosfpubsub.pubsub_api_pb2 as pb2
import aiosfpubsub.pubsub_api_pb2_grpc as pb2_grpc

logger = logging.getLogger(__name__)

with open(certifi.where(), "rb") as f:
    secure_channel_credentials = grpc.ssl_channel_credentials(f.read())


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
        api_version: str = "57.0"
    ) -> None:
        self.url: str = url
        self.username: str = username
        self.password: str = password
        self.metadata: tuple[tuple[str, str]] | None = None
        grpc_host: str = grpc_host
        grpc_port: int = grpc_port
        pubsub_url: str = f"{grpc_host}:{grpc_port}"
        channel = grpc.secure_channel(pubsub_url, secure_channel_credentials)
        self.stub = pb2_grpc.PubSubStub(channel)
        self.session_id: str | None = None
        self.pb2: pb2 = pb2
        self.apiVersion: str = api_version

    def auth(self):
        """
        Sends a login request to the Salesforce SOAP API to retrieve a session
        token. The session token is bundled with other identifying information
        to create a tuple of metadata headers, which are needed for every RPC
        call.
        """
        url_suffix: str = f"/services/Soap/u/{self.apiVersion}/"
        headers: dict[str, str] = {"content-type": "text/xml", "SOAPAction": "Login"}
        xml: tuple[str] = (
            "<soapenv:Envelope xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/' "
            + "xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' "
            + "xmlns:urn='urn:partner.soap.sforce.com'><soapenv:Body>"
            + "<urn:login><urn:username><![CDATA["
            + self.username
            + "]]></urn:username><urn:password><![CDATA["
            + self.password
            + "]]></urn:password></urn:login></soapenv:Body></soapenv:Envelope>"
        )
        res: httpx.models.Response = httpx.post(
            str(self.url) + url_suffix, data=xml, headers=headers
        )
        res_xml: et.Element = et.fromstring(res.content.decode("utf-8"))[0][0][0]

        try:
            url_parts: ParseResult = urlparse(res_xml[3].text)
            self.url = "{}://{}".format(url_parts.scheme, url_parts.netloc)
            self.session_id = res_xml[4].text
        except IndexError:
            logger.error(
                f"An exception occurred. Check the response XML below: {res.__dict__}",
                exc_info=True,
            )

        # Get org ID from UserInfo
        uinfo = res_xml[6]
        # Org ID
        self.tenant_id: str = uinfo[8].text

        # Set metadata headers
        self.metadata = (
            ("accesstoken", self.session_id),
            ("instanceurl", self.url),
            ("tenantid", self.tenant_id),
        )

    async def fetch_req_stream(self, topic, replay_type, replay_id, num_requested):
        while True:
            yield self.make_fetch_request(topic, replay_type, replay_id, num_requested)
            await asyncio.sleep(5)  # Wait for 5 seconds before sending the next FetchRequest

    async def subscribe(self, topic, replay_type, replay_id, num_requested, callback: Callable):
        async for event in self.stub.Subscribe(
            self.fetch_req_stream(topic, replay_type, replay_id, num_requested),
            metadata=self.metadata,
        ):
            callback(event, self)

    def make_fetch_request(
        self, topic: str, replay_type: str, replay_id: bytes, num_requested: int
    ) -> pb2.FetchRequest:
        """Creates a FetchRequest per the proto file."""
        replay_preset: pb2.ReplayPreset | None = None
        match replay_type:
            case "LATEST":
                replay_preset = pb2.ReplayPreset.LATEST
            case "EARLIEST":
                replay_preset = pb2.ReplayPreset.EARLIEST
            case "CUSTOM":
                replay_preset = pb2.ReplayPreset.CUSTOM
            case _:
                raise ValueError("Invalid Replay Type " + replay_type)
        return pb2.FetchRequest(
            topic_name=topic,
            replay_preset=replay_preset,
            replay_id=replay_id if replay_id else None,
            num_requested=num_requested,
        )

    async def get_topic(self, topic_name: str) -> pb2.TopicInfo:
        """Uses GetTopic RPC to retrieve topic given topic_name."""
        return await self.stub.GetTopic(
            pb2.TopicRequest(topic_name=topic_name), metadata=self.metadata
        )

    async def get_schema_json(self, schema_id: str):
        """Uses GetSchema RPC to retrieve schema given a schema ID."""
        # If the schema is not found in the dictionary, get the schema and store it in the dictionary
        if (
            schema_id not in self.json_schema_dict
            or self.json_schema_dict[schema_id] == None
        ):
            res = await self.stub.GetSchema(
                pb2.SchemaRequest(schema_id=schema_id), metadata=self.metadata
            )
            self.json_schema_dict[schema_id] = res.schema_json

        return self.json_schema_dict[schema_id]
