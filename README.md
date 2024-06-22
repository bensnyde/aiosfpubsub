# aiosfpubsub
An async Python gRPC client for the Salesforce Pub/Sub API.

# usage 
```
import asyncio
from datetime import datetime 


client = Client(**{
    "url": "https://login.salesforce.com",
    "username": "myuser",
    "password": "mypass",
    "grpc_host": "api.pubsub.salesforce.com",
    "grpc_port": 7443,
    "api_version": "57.0"
})

def callback(event, client):
    """
    This is a callback that gets passed to the `Client.subscribe()` method.
    When no events are received within a certain time period, the API's subscribe
    method sends keepalive messages and the latest replay ID through this callback.
    """
    if event.events:
        print("Number of events received in FetchResponse: ", len(event.events))

        for evt in event.events:
            print(f"{evt.event.payload}")
    else:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] The subscription is active.")

    evnt.latest_replay_id

async def main():
    await client.subscribe(
        topic="/event/MyTestTopic__e",
        replay_type="LATEST",
        replay_id=None,
        num_requested=10,
        callback=callback
    )

asyncio.run(main())
```