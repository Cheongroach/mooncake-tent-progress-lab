"""Exercise the real TENT C ABI; coordinator carries metadata/barriers only."""

import argparse
import ctypes as C
import json
import os
import socket
import socketserver
import threading
import time
from pathlib import Path


class Request(C.Structure):
    _fields_ = [
        ("opcode", C.c_int),
        ("source", C.c_void_p),
        ("target_id", C.c_uint64),
        ("target_offset", C.c_uint64),
        ("length", C.c_uint64),
        ("priority", C.c_int),
        ("transport_hint", C.c_int),
    ]


class Status(C.Structure):
    _fields_ = [("status", C.c_int), ("transferred_bytes", C.c_uint64)]


class MemoryOptions(C.Structure):
    _fields_ = [
        ("location", C.c_char * 64),
        ("permission", C.c_int),
        ("transport_type", C.c_int),
        ("shm_path", C.c_char * 256),
        ("shm_offset", C.c_size_t),
        ("internal", C.c_int),
    ]


class Coordinator(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6
    allow_reuse_address = True
    daemon_threads = False

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def __init__(self, address):
        super().__init__(address, Handler)
        self.values = {}
        self.condition = threading.Condition()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(180)
        line = self.rfile.readline(1 << 20)
        if not line:
            return
        msg = json.loads(line)
        with self.server.condition:
            if msg["op"] == "set":
                if msg["key"] in self.server.values:
                    result = {"error": "duplicate key " + msg["key"]}
                else:
                    self.server.values[msg["key"]] = msg["value"]
                    self.server.condition.notify_all()
                    result = {"value": True}
            else:
                ready = self.server.condition.wait_for(lambda: msg["key"] in self.server.values, timeout=150)
                result = (
                    {"value": self.server.values[msg["key"]]}
                    if ready
                    else {"error": "barrier timed out: " + msg["key"]}
                )
        self.wfile.write(json.dumps(result).encode() + b"\n")


class Store:
    def __init__(self, host, port, rank, world):
        self.address, self.rank, self.world = (host, port), rank, world

    def call(self, op, key, value=None):
        deadline = time.monotonic() + 150
        while True:
            try:
                sock = socket.create_connection(self.address, timeout=3)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        with sock:
            sock.settimeout(180)
            sock.sendall(json.dumps(dict(op=op, key=key, value=value)).encode() + b"\n")
            with sock.makefile("rb") as reader:
                response = json.loads(reader.readline(1 << 20))
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["value"]

    def barrier(self, stage):
        self.call("set", f"{stage}/{self.rank}", True)
        for rank in range(self.world):
            self.call("get", f"{stage}/{rank}")


class Engine:
    def __init__(self, library, transport, host, device):
        self.lib = C.CDLL(library)
        ptr, u64, size = C.c_void_p, C.c_uint64, C.c_size_t
        signatures = {
            "tent_set_config": (None, [C.c_char_p, C.c_char_p]),
            "tent_create_engine": (ptr, []),
            "tent_available": (C.c_int, [ptr]),
            "tent_destroy_engine": (None, [ptr]),
            "tent_segment_name": (C.c_int, [ptr, ptr, size]),
            "tent_open_segment": (C.c_int, [ptr, C.POINTER(u64), C.c_char_p]),
            "tent_close_segment": (C.c_int, [ptr, u64]),
            "tent_register_memory_ex": (C.c_int, [ptr, ptr, size, C.POINTER(MemoryOptions)]),
            "tent_unregister_memory": (C.c_int, [ptr, ptr, size]),
            "tent_allocate_batch": (u64, [ptr, size]),
            "tent_free_batch": (C.c_int, [ptr, u64]),
            "tent_submit": (C.c_int, [ptr, u64, C.POINTER(Request), size]),
            "tent_overall_status": (C.c_int, [ptr, u64, C.POINTER(Status)]),
        }
        for name, (restype, argtypes) in signatures.items():
            fn = getattr(self.lib, name)
            fn.restype, fn.argtypes = restype, argtypes
        self.transport = {"tcp": 7, "rdma": 1, "ascend_direct": 8}[transport]
        config = {
            "metadata_type": "p2p",
            "metadata_servers": "P2PHANDSHAKE",
            "rpc_server_hostname": host,
            "rpc_server_port": "0",
            "rpc_server_threads": "2",
            "log_level": "warning",
            "merge_requests": "false",
            "enable_auto_failover_on_poll": "false",
            "enable_progress_worker": "false",
        }
        if transport == "rdma":
            config["transports/rdma/num_lanes"] = os.environ.get("NET_RDMA_LANES", "1")
        for name in ("tcp", "rdma", "shm", "io_uring", "bufio", "hp_tcp", "ascend_direct"):
            config[f"transports/{name}/enable"] = str(name == transport).lower()
        for key, value in config.items():
            self.lib.tent_set_config(key.encode(), value.encode())
        self.handle = self.lib.tent_create_engine()
        if not self.handle or not self.lib.tent_available(self.handle):
            raise RuntimeError("TENT initialization failed")
        self.location = f"npu:{device}" if transport == "ascend_direct" else "cpu:0"
        name = C.create_string_buffer(1024)
        self.check(self.lib.tent_segment_name(self.handle, name, len(name)))
        self.name = name.value.decode()

    @staticmethod
    def check(result):
        if result != 0:
            raise RuntimeError(f"TENT returned {result}; inspect native log")

    def register(self, address, size):
        options = MemoryOptions()
        options.location, options.permission = self.location.encode(), 2
        options.transport_type = self.transport
        self.check(self.lib.tent_register_memory_ex(self.handle, address, size, C.byref(options)))

    def open(self, name):
        segment = C.c_uint64()
        self.check(self.lib.tent_open_segment(self.handle, C.byref(segment), name.encode()))
        return segment.value

    def transfer(self, requests, window):
        # Separate batch per request: every op consumes a backend group credit.
        batches = []
        started = time.perf_counter_ns()
        for request in requests:
            batch = self.lib.tent_allocate_batch(self.handle, 1)
            if not batch:
                raise RuntimeError("allocateBatch failed")
            self.check(self.lib.tent_submit(self.handle, batch, C.byref(request), 1))
            batches.append((batch, request.length))
            if len(batches) == window:
                self.drain(batches)
                batches.clear()
        self.drain(batches)
        return (time.perf_counter_ns() - started) / 1e9

    def drain(self, batches):
        deadline = time.monotonic() + 60
        for batch, expected in batches:
            state = Status()
            while True:
                self.check(self.lib.tent_overall_status(self.handle, batch, C.byref(state)))
                if state.status == 4:
                    if state.transferred_bytes != expected:
                        raise RuntimeError(f"completed byte count {state.transferred_bytes} != {expected}")
                    break
                if state.status not in (0, 1):
                    raise RuntimeError(f"terminal transfer status {state.status}")
                if time.monotonic() > deadline:
                    raise TimeoutError("completion deadline")
                time.sleep(0)
            self.check(self.lib.tent_free_batch(self.handle, batch))


class Buffer:
    def __init__(self, size, npu, device):
        self.size, self.npu = size, npu
        if npu:
            import torch
            import torch_npu  # noqa: F401

            self.torch = torch
            torch.npu.set_device(device)
            self.data = torch.empty(size, dtype=torch.uint8, device=f"npu:{device}")
            self.address = self.data.data_ptr()
        else:
            self.data = C.create_string_buffer(size)
            self.address = C.addressof(self.data)

    def fill(self, value):
        if self.npu:
            self.data.fill_(value)
            self.torch.npu.synchronize()
        else:
            C.memset(self.address, value, self.size)

    def verify(self, offset, count, value):
        if self.npu:
            ok = bool((self.data[offset : offset + count] == value).all().item())
        else:
            ok = C.string_at(self.address + offset, count) == bytes([value]) * count
        if not ok:
            raise AssertionError(f"payload mismatch offset={offset} bytes={count} expected={value}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--library", required=True)
    p.add_argument("--transport", choices=("tcp", "rdma", "ascend_direct"), required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--master", default="127.0.0.1")
    p.add_argument("--port", type=int, default=29841)
    p.add_argument("--rank", type=int, default=0)
    p.add_argument("--world", type=int, default=2)
    p.add_argument("--local-rank", type=int, default=0)
    p.add_argument("--local-world", type=int, default=1)
    p.add_argument("--sizes", default="4096,65536,1048576")
    p.add_argument("--windows", default="1,32,128")
    p.add_argument("--ops", type=int, default=128)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--patterns", default="pair,rotate")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if not (0 <= args.rank < args.world and args.world >= 2 and args.world % 2 == 0):
        raise ValueError("an even world >= 2 and a valid rank are required")
    sizes = [int(x) for x in args.sizes.split(",")]
    windows = [int(x) for x in args.windows.split(",")]
    if min(sizes + windows + [args.ops, args.repeats]) <= 0:
        raise ValueError("dimensions must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    coordinator = None
    if args.rank == 0:
        coordinator = Coordinator(("", args.port))
        threading.Thread(target=coordinator.serve_forever, daemon=True).start()
    store = Store(args.master, args.port, args.rank, args.world)
    half, initiator = args.world // 2, args.rank < args.world // 2
    stride = max(sizes)
    buffer = Buffer(half * args.ops * stride, args.transport == "ascend_direct", args.local_rank)
    engine = Engine(args.library, args.transport, args.host, args.local_rank)
    engine.register(buffer.address, buffer.size)
    metadata = {
        "rank": args.rank,
        "engine": engine.name,
        "address": buffer.address,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "hostname": socket.gethostname(),
        "transport": args.transport,
        "pid": os.getpid(),
    }
    store.call("set", f"rank/{args.rank}", metadata)
    peers = [store.call("get", f"rank/{rank}") for rank in range(args.world)]
    (args.output / f"topology-{args.rank}.json").write_text(json.dumps(peers, indent=2))
    if args.local_world == half and args.world > 2:
        if len({item["boot_id"] for item in peers}) != 2:
            raise AssertionError("expected two physical hosts")
    segments = {r: engine.open(peers[r]["engine"]) for r in range(half, args.world)} if initiator else {}
    store.barrier("ready")
    case = 0
    with (args.output / f"samples-{args.rank}.jsonl").open("w", buffering=1) as stream:
        for pattern in args.patterns.split(","):
            if pattern not in ("pair", "rotate"):
                raise ValueError(pattern)
            for opcode in (1, 0):
                for size in sizes:
                    for window in windows:
                        for repeat in range(args.repeats + 1):
                            stage = f"case-{case}"

                            def value(rank):
                                return 1 + (rank * 17 + case * 29) % 254

                            def target(source, op):
                                return half + (source if pattern == "pair" else (source + op) % half)

                            buffer.fill(value(args.rank) if initiator == (opcode == 1) else 0)
                            store.barrier(stage + "-initialized")
                            elapsed = None
                            if initiator:
                                requests = []
                                for op in range(args.ops):
                                    peer = target(args.rank, op)
                                    offset = (args.rank * args.ops + op) * stride
                                    requests.append(
                                        Request(
                                            opcode,
                                            buffer.address + offset,
                                            segments[peer],
                                            peers[peer]["address"] + offset,
                                            size,
                                            1,
                                            engine.transport,
                                        )
                                    )
                                elapsed = engine.transfer(requests, window)
                            store.barrier(stage + "-transferred")
                            if opcode == 1 and not initiator:
                                for source in range(half):
                                    for op in range(args.ops):
                                        if target(source, op) == args.rank:
                                            buffer.verify((source * args.ops + op) * stride, size, value(source))
                            elif opcode == 0 and initiator:
                                for op in range(args.ops):
                                    buffer.verify(
                                        (args.rank * args.ops + op) * stride, size, value(target(args.rank, op))
                                    )
                            record = dict(
                                event="sample",
                                rank=args.rank,
                                transport=args.transport,
                                pattern=pattern,
                                operation="write" if opcode else "read",
                                size=size,
                                window=window,
                                ops=args.ops,
                                repeat=repeat,
                                warmup=repeat == 0,
                                seconds=elapsed,
                                verified=True,
                            )
                            stream.write(json.dumps(record) + "\n")
                            print(json.dumps(record), flush=True)
                            store.barrier(stage + "-verified")
                            case += 1
    store.barrier("finished")
    for segment in segments.values():
        engine.check(engine.lib.tent_close_segment(engine.handle, segment))
    store.barrier("segments-closed")
    engine.check(engine.lib.tent_unregister_memory(engine.handle, buffer.address, buffer.size))
    engine.lib.tent_destroy_engine(engine.handle)
    store.barrier("engines-destroyed")
    # Arrival alone is insufficient: peers may still be reading barrier keys.
    # Each peer publishes departure only after its final barrier returns.
    store.call("set", f"departed/{args.rank}", True)
    if coordinator:
        for rank in range(args.world):
            store.call("get", f"departed/{rank}")
        coordinator.shutdown()
        coordinator.server_close()  # Join handlers that are sending final ACKs.
    print(json.dumps({"event": "complete", "rank": args.rank, "cases": case}), flush=True)


if __name__ == "__main__":
    main()
