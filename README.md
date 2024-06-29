# aiosfpubsub
An async Python gRPC client for the Salesforce Pub/Sub API.

https://github.com/forcedotcom/pub-sub-api/blob/main/pubsub_api.proto

# install
```bash
pip install aiosfpubsub
```

# usage 
```python
import asyncio
from aiosfpubsub import Client
from datetime import datetime 


def callback(event, client):
    """
    This is a callback that gets passed to the `Client.subscribe()` method.
    When no events are received within a certain time period, the API's subscribe
    method sends keepalive messages and the latest replay ID through this callback.
    """
    if event.events:
        print("Number of events received in FetchResponse: ", len(event.events))

        for evt in event.events:
            # Get the event payload and schema, then decode the payload
            payload_bytes = evt.event.payload
            json_schema = client.get_schema_json(evt.event.schema_id)
            decoded_event = client.decode(json_schema, payload_bytes)
            print(decoded_event)
    else:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] The subscription is active.")

async def main():
    client = Client(**{
        "url": "https://login.salesforce.com",
        "username": "myuser",
        "password": "mypass",
        "grpc_host": "api.pubsub.salesforce.com",
        "grpc_port": 7443,
        "api_version": "57.0"
    })
    client.auth()
    await client.subscribe(
        topic="/event/My_Event__e",
        replay_type="EARLIEST",
        replay_id=None,
        num_requested=10,
        callback=callback
    )
if __name__ == "__main__":
    asyncio.run(main())
```