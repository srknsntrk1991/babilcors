import asyncio
import os

import aiomqtt

from src.iot import ProtobufSerializer


async def main():
    host = os.environ.get("MQTT_HOST", "127.0.0.1")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    user = os.environ.get("MQTT_USER", "")
    pwd = os.environ.get("MQTT_PASS", "")
    topic = os.environ.get("MQTT_TOPIC", "gnss/v1/KNY1/stream")

    ser = ProtobufSerializer()
    async with aiomqtt.Client(hostname=host, port=port, username=user or None, password=pwd or None) as client:
        await client.subscribe(topic)
        print("Subscribed to", topic)
        async with client.messages() as messages:
            async for message in messages:
                env = ser.deserialize(message.payload)
                print(f"type={env['msg_type']} bytes={len(env['rtcm'])} stid={env['station_id']} mp={env['mountpoint']}")


if __name__ == "__main__":
    asyncio.run(main())
