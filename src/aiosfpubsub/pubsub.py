import io
import logging
import threading
import xml.etree.ElementTree as et
from typing import Any
from urllib.parse import ParseResult, urlparse

import avro.io
import avro.schema
import certifi
import grpc
import requests

import pysfpubsub.pubsub_api_pb2 as pb2
import pysfpubsub.pubsub_api_pb2_grpc as pb2_grpc

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

        """
        Semaphore used for subscriptions. This keeps the subscription stream open
        to receive events and to notify when to send the next FetchRequest.
        See Python Quick Start for more information. 
        https://developer.salesforce.com/docs/platform/pub-sub-api/guide/qs-python-quick-start.html
        There is probably a better way to do this. This is only sample code. Please
        use your own discretion when writing your production Pub/Sub API client.
        Make sure to use only one semaphore per subscribe call if you are planning
        to share the same instance of PubSub.
        """
        self.semaphore: threading.Semaphore = threading.Semaphore(1)

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
        res: requests.models.Response = requests.post(
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

    def release_subscription_semaphore(self) -> None:
        """Release semaphore so FetchRequest can be sent."""
        self.semaphore.release()

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

    def fetch_req_stream(
        self, topic: str, replay_type: str, replay_id: bytes, num_requested: int
    ) -> pb2.FetchRequest:
        """Returns a FetchRequest stream for the Subscribe RPC."""
        while True:
            # Only send FetchRequest when needed. Semaphore release indicates need for new FetchRequest
            self.semaphore.acquire()
            yield self.make_fetch_request(topic, replay_type, replay_id, num_requested)

    def encode(self, schema, payload: dict[str, Any]) -> bytes:
        """
        Uses Avro and the event schema to encode a payload. The `encode()` and
        `decode()` methods are helper functions to serialize and deserialize
        the payloads of events that clients will publish and receive using
        Avro. If you develop an implementation with a language other than
        Python, you will need to find an Avro library in that language that
        helps you encode and decode with Avro. When publishing an event, the
        plaintext payload needs to be Avro-encoded with the event schema for
        the API to accept it. When receiving an event, the Avro-encoded payload
        needs to be Avro-decoded with the event schema for you to read it in
        plaintext.
        """
        schema = avro.schema.parse(schema)
        buf = io.BytesIO()
        encoder = avro.io.BinaryEncoder(buf)
        writer = avro.io.DatumWriter(schema)
        writer.write(payload, encoder)
        return buf.getvalue()

    def decode(self, schema, payload: bytes) -> dict[str, Any]:
        """
        Uses Avro and the event schema to decode a serialized payload. The
        `encode()` and `decode()` methods are helper functions to serialize and
        deserialize the payloads of events that clients will publish and
        receive using Avro. If you develop an implementation with a language
        other than Python, you will need to find an Avro library in that
        language that helps you encode and decode with Avro. When publishing an
        event, the plaintext payload needs to be Avro-encoded with the event
        schema for the API to accept it. When receiving an event, the
        Avro-encoded payload needs to be Avro-decoded with the event schema for
        you to read it in plaintext.
        """
        schema = avro.schema.parse(schema)
        buf = io.BytesIO(payload)
        decoder = avro.io.BinaryDecoder(buf)
        reader = avro.io.DatumReader(schema)
        ret = reader.read(decoder)
        return ret

    def get_topic(self, topic_name: str) -> pb2.TopicInfo:
        """Uses GetTopic RPC to retrieve topic given topic_name."""
        return self.stub.GetTopic(
            pb2.TopicRequest(topic_name=topic_name), metadata=self.metadata
        )

    def get_schema_json(self, schema_id: str):
        """Uses GetSchema RPC to retrieve schema given a schema ID."""
        # If the schema is not found in the dictionary, get the schema and store it in the dictionary
        if (
            schema_id not in self.json_schema_dict
            or self.json_schema_dict[schema_id] == None
        ):
            res = self.stub.GetSchema(
                pb2.SchemaRequest(schema_id=schema_id), metadata=self.metadata
            )
            self.json_schema_dict[schema_id] = res.schema_json

        return self.json_schema_dict[schema_id]

    def format_payload(self, schema, schema_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Format event payload for publish."""
        return {
            "schema_id": schema_id,
            "payload": self.encode(schema, payload),
        }

    def subscribe(
        self,
        topic: str,
        replay_type: str,
        replay_id: bytes,
        num_requested: int,
        callback,
    ) -> None:
        """
        Calls the Subscribe RPC defined in the proto file and accepts a
        client-defined callback to handle any events that are returned by the
        API. It uses a semaphore to prevent the Python client from closing the
        connection prematurely (this is due to the way Python's GRPC library is
        designed and may not be necessary for other languages--Java, for
        example, does not need this).
        """
        sub_stream = self.stub.Subscribe(
            self.fetch_req_stream(topic, replay_type, replay_id, num_requested),
            metadata=self.metadata,
        )
        logger.info(f"> Subscribed to {topic}")
        for event in sub_stream:
            callback(event, self)

    def publish(self, topic_name: str, events: dict[str, Any]) -> None:
        """Publishes event to the specified Platform Event topic."""
        return self.stub.Publish(
            self.pb2.PublishRequest(
                topic_name=topic_name,
                events=events,
            ),
            metadata=self.metadata,
        )