import asyncio
import time
from typing import Any, Optional, Tuple, List

try:
    import aiomqtt  # type: ignore
except Exception:  # pragma: no cover
    aiomqtt = None
from google.protobuf import descriptor_pb2, message_factory, descriptor_pool

_RtcmEnvelope = None


class ProtobufSerializer:
    def __init__(self) -> None:
        if _RtcmEnvelope is not None:
            self._cls = _RtcmEnvelope
            return
        fdp = descriptor_pb2.FileDescriptorProto()
        fdp.name = "rtcm_envelope.proto"
        fdp.package = "gnss.v1"
        msg = fdp.message_type.add()
        msg.name = "RtcmEnvelope"
        def add_field(name: str, number: int, ftype: int, label: int = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL):
            fld = msg.field.add()
            fld.name = name
            fld.number = number
            fld.type = ftype
            fld.label = label
        add_field("rtcm", 1, descriptor_pb2.FieldDescriptorProto.TYPE_BYTES)
        add_field("msg_type", 2, descriptor_pb2.FieldDescriptorProto.TYPE_UINT32)
        add_field("station_id", 3, descriptor_pb2.FieldDescriptorProto.TYPE_UINT32)
        add_field("ts_unix_ms", 4, descriptor_pb2.FieldDescriptorProto.TYPE_INT64)
        add_field("crc_errors", 5, descriptor_pb2.FieldDescriptorProto.TYPE_UINT32)
        add_field("mountpoint", 6, descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
        pool = descriptor_pool.DescriptorPool()
        file_desc = pool.Add(fdp)
        factory = message_factory.MessageFactory(pool)
        # Use GetMessageClass for protobuf >= 4.x compatibility
        self._cls = factory.GetMessageClass(file_desc.message_types_by_name["RtcmEnvelope"])  # type: ignore

    def serialize(self, *, rtcm: bytes, msg_type: int, station_id: int, mountpoint: str, crc_errors: int) -> bytes:
        ts_ms = int(time.time() * 1000)
        msg = self._cls()
        msg.rtcm = rtcm
        msg.msg_type = int(msg_type)
        msg.station_id = int(station_id)
        msg.ts_unix_ms = ts_ms
        msg.crc_errors = int(crc_errors)
        msg.mountpoint = str(mountpoint)
        return msg.SerializeToString()

    def deserialize(self, payload: bytes) -> dict:
        msg = self._cls()
        msg.ParseFromString(payload)
        return {
            "msg_type": int(msg.msg_type),
            "station_id": int(msg.station_id),
            "ts_unix_ms": int(msg.ts_unix_ms),
            "crc_errors": int(msg.crc_errors),
            "mountpoint": str(msg.mountpoint),
            "rtcm": bytes(msg.rtcm),
        }


class MQTTManager:
    def __init__(self, *, host: str = "", port: int = 1883, username: str = "", password: str = "", tls: bool = False):
        self.host = host
        self.port = int(port or 1883)
        self.username = username
        self.password = password
        self.tls = tls
        self._client: Optional[Any] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if not self.host or aiomqtt is None:
            return
        async with self._lock:
            if self._client is None:
                self._client = aiomqtt.Client(hostname=self.host, port=self.port, username=self.username or None, password=self.password or None, tls=self.tls or None)
                await self._client.connect()

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None

    async def publish(self, topic: str, payload: bytes) -> None:
        if not self.host or aiomqtt is None:
            return
        if self._client is None:
            await self.start()
        if self._client is None:
            return
        try:
            await self._client.publish(topic, payload)
        except Exception:
            # best-effort
            pass


class IoTRelay:
    def __init__(self, *, mqtt: MQTTManager, serializer: ProtobufSerializer, mountpoint: str):
        self.mqtt = mqtt
        self.serializer = serializer
        self.mountpoint = mountpoint
        self.queue: asyncio.Queue[Tuple[bytes, int, int, int]] = asyncio.Queue(maxsize=4096)
        self._subscribers: List[asyncio.Queue[bytes]] = []
        self._task: Optional[asyncio.Task] = None
        self._closed = False

    def add_websocket_queue(self, q: asyncio.Queue[bytes]) -> None:
        self._subscribers.append(q)

    def remove_websocket_queue(self, q: asyncio.Queue[bytes]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _run(self) -> None:
        topic = f"gnss/v1/{self.mountpoint}/stream"
        while not self._closed:
            rtcm, msg_type, station_id, crc_errors = await self.queue.get()
            try:
                envelope = self.serializer.serialize(rtcm=rtcm, msg_type=msg_type, station_id=station_id, mountpoint=self.mountpoint, crc_errors=crc_errors)
            except Exception:
                continue
            # MQTT
            try:
                await self.mqtt.publish(topic, envelope)
            except Exception:
                pass
            # WebSocket fan-out
            dead: List[asyncio.Queue[bytes]] = []
            for q in self._subscribers:
                try:
                    if q.full():
                        _ = q.get_nowait()
                    q.put_nowait(envelope)
                except Exception:
                    dead.append(q)
            for d in dead:
                self.remove_websocket_queue(d)
