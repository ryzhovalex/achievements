import asyncio
import configparser
import contextvars
import gzip
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Generic, Self, Sequence, TypeVar

import aiofiles
import colorama
from pydantic import BaseModel, ValidationError

queue = asyncio.Queue()
loop_task: asyncio.Task
domain_log_files = {}
rotation = 10 * 1024 * 1024  # 10MB
log_context = contextvars.ContextVar("log_context")
encoding = "utf-8"
CodePack = tuple[int, bytes]
_user_path: Path
_config: configparser.ConfigParser


class Model(BaseModel):
    def to_bytes(self) -> bytes:
        return json_to_bytes(self.model_dump())

    @classmethod
    def from_bytes(cls, d: bytes) -> Self:
        return bytes_to_model(cls, d)

    # @classmethod
    # def from_record(cls, r: Record) -> Self:
    #     raise NotImplementedError


class ArbitraryModel(Model):
    class Config:
        arbitrary_types_allowed = True


TBaseModel = TypeVar("TBaseModel", bound=BaseModel)
TModel = TypeVar("TModel", bound=Model)
TArbitraryModel = TypeVar("TArbitraryModel", bound=ArbitraryModel)



def log_debug(message: Any):
    print(f"{colorama.Fore.BLUE}DEBUG{colorama.Fore.RESET}: " + str(message), file=sys.stderr)  # noqa: T201


def log_extra(k: str, v: Any):
    d = log_context.get({})
    d[k] = v
    log_context.set(d)


def log_info(message: str, domain: str = "main"):
    save(domain, "info", message, None)


def log_warn(message: str, domain: str = "main"):
    save(domain, "warning", "WARNING: " + message, None)


def log_error(message: str, trace: Exception | None = None, domain: str = "main"):
    """
    Note that for `trace` to work properly we need to catch an exception
    and immediatelly log-trace it using this function, or the traceback data
    will be incorrect.
    """
    trace_id: str | None = None
    trace_path = None

    # Trace error to special storage.
    if trace:
        loc_dir = user(Path("log", "trace"))
        loc_dir.mkdir(parents=True, exist_ok=True)
        trace_id = makeid()
        loc_name = trace_id
        loc = Path(loc_dir, loc_name + ".log")
        with loc.open("w+") as file:
            file.write(f"Trace #{trace_id} for an error '{trace}':\n" + traceback.format_exc())
        trace_path = loc
    save(domain, "error", "ERROR: " + message, trace_id, trace_path)


def save(domain: str, type: str, message: str, trace_id: str | None, trace_path: Path | None = None):
    t = time.time()

    trace_message = ""
    if trace_path:
        trace_message = f" (trace '{trace_path}')"
    elif trace_id:
        trace_message = f" (trace '{trace_id}')"
    message =  f"{message} {trace_message}"

    # Nested into struct message do not need newline.
    message = message.strip()

    ctx = log_context.get({})
    module = ctx.pop("module", "")
    message_data = {
        "time": t,
        "message": message,
        "type": type,
        "module": module,
        "context": ctx,
    }

    console_message = f"[{t:.3f}] {message}"
    console_message = console_message.strip() + "\n"

    if type == "error":
        console_message = console_message.replace("ERROR: ", f"{colorama.Fore.RED}ERROR{colorama.Fore.RESET}: ")
    elif type == "warning":
        console_message = console_message.replace("WARNING: ", f"{colorama.Fore.YELLOW}WARNING{colorama.Fore.RESET}: ")

    print(console_message, sep="", end="", file=sys.stderr)  # noqa: T201
    queue.put_nowait((domain, message_data))


async def _log_loop():
    while True:
        domain, data = await queue.get()
        await _write_file(domain, data)


async def _write_file(domain: str, data: dict):
    try:
        path = user(Path("log", f"{domain}.log"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        size = os.path.getsize(path)
        if size >= rotation:
            i = 1
            while True:
                backup_path = user(f"log/{domain}_{i}.log.gz")
                if not backup_path.exists():
                    with path.open("rb") as f1, gzip.open(backup_path, "wb") as f2:
                        shutil.copyfileobj(f1, f2)
                    if domain in domain_log_files:
                        await domain_log_files[domain].close()
                        del domain_log_files[domain]
                    path.unlink()
                    path.touch(exist_ok=True)
                    break
                i += 1

        if domain not in domain_log_files:
            file = await aiofiles.open(path, "a")
            domain_log_files[domain] = file
        else:
            file = domain_log_files[domain]

        dump = json.dumps(data)
        dump = dump.strip()
        dump += "\n"
        await file.write(dump)
        # flush is needed to update file immediatelly for the external software
        await file.flush()
    except Exception as e:
        print(f"{colorama.Fore.RED}CRITICAL{colorama.Fore.RESET}: Failed to write to a log file with an error (+traceback): {e}")
        traceback.print_exc()


def cwd(p: str | Path) -> Path:
    return Path(Path.cwd(), p)


def user(p: str | Path) -> Path:
    # @todo disallow path outs
    return Path(_user_path, p)


def source(p: str | Path) -> Path:
    return Path(Path(__file__).parent.parent, p)


def bytes_to_model(model_type: type[TModel], input: bytes) -> TModel:
    try:
        return model_type.model_validate(bytes_to_json(input))
    except ValidationError as e:
        raise Exception("Invalid model data") from e


def convert_enums(data: Any) -> Any:
    if isinstance(data, dict):
        new = {}
        for k, v in data.items():
            new[k] = _convert_enums_v(v)
        return new
    elif isinstance(data, (list, tuple, set)):
        r = []
        for x in data:
            r.append(convert_enums(x))
        return r
    else:
        return data


def _convert_enums_v(v: Any) -> Any:
    final_v = v
    if isinstance(v, Enum):
        final_v = v.value
    elif isinstance(v, dict):
        final_v = convert_enums(v)
    elif isinstance(v, (list, tuple, set)):
        final_v = []
        for x in v:
            final_v.append(_convert_enums_v(x))
    return final_v


def bytes_to_string(input: bytes) -> str:
    return input.decode(encoding)


def string_to_bytes(input: str) -> bytes:
    return input.encode(encoding)


def models_to_bytes(models: Sequence[BaseModel]) -> bytes:
    return json.dumps([x.model_dump() for x in models]).encode(encoding)


def model_to_bytes(model: BaseModel) -> bytes:
    return model.model_dump_json().encode(encoding)


def json_to_bytes(input: Any) -> bytes:
    return json.dumps(convert_enums(input)).encode(encoding)


def bytes_to_json(input: bytes) -> Any:
    if input == bytes():
        return {}
    return json.loads(input.decode(encoding))


def float_to_bytes(input: float) -> bytes:
    return struct.pack("<f", input)


def bytes_to_float(input: bytes) -> float:
    return struct.unpack("<f", input)[0]


def int_to_bytes(input: int, size: int, signed: bool) -> bytes:
    return input.to_bytes(size, byteorder="little", signed=signed)


def bytes_to_int(input: bytes, signed: bool) -> int:
    return int.from_bytes(input, byteorder="little", signed=signed)


def adaptively_to_bytes(input: Any, signed: bool):
    if isinstance(input, str):
        return string_to_bytes(input)
    elif isinstance(input, int):
        return int_to_bytes(input, 8, signed)
    elif isinstance(input, bytes):
        return input
    else:
        raise TypeError("Unsupported data type")


def unwrap_coded_structure(input: bytes) -> tuple[int, bytes]:
    """
    Unwraps bytes structure consisting of 2 leading bytes of integer code, and rest of the bytes as payload.

    Returns tuple of code and payload.
    """
    if len(input) < 2:
        raise Exception("too short coded structure")
    code = struct.unpack("<H", input[:2])[0]
    payload = bytes()
    if len(input) > 2:
        payload = input[2:]
    return code, payload


class Reader:
    def __init__(self, b: bytes):
        self.i = 0
        self.b = b

    def read(self, size: int) -> bytes:
        r = self.b[self.i:self.i+size]
        self.i += size
        if len(r) == 0:
            raise StopIteration
        return r

    def read_int(self, size: int, signed: bool) -> int:
        return bytes_to_int(self.read(size), signed)

    def read_string(self, size: int) -> str:
        return bytes_to_string(self.read(size))


class Vector2:
    def __init__(self, x: float, y: float):
        self.x: float = x
        self.y: float = y


T = TypeVar("T")
class Signal(Generic[T]):
    def __init__(self):
        self._listeners = []

    def connect(self, listener: Callable[[T], Awaitable[None]]):
        """Connect a listener to this signal."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def disconnect(self, listener: Callable[[T], Awaitable[None]]):
        """Disconnect a listener from this signal."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def emit(self, value: T):
        """Emit the signal, calling all connected listeners."""
        for listener in self._listeners:
            await listener(value)


def call(command: str, dir: os.PathLike | str | None = None) -> tuple[str, str, int]:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            cwd=dir,
        )
        return (result.stdout, result.stderr, result.returncode)
    except subprocess.CalledProcessError as e:
        return (e.stdout, e.stderr, e.returncode)


def makeid() -> str:
    """Creates unique id.

    Returns:
        Id created.
    """
    return uuid.uuid4().hex


def random_float(min: float, max: float) -> float:
    return random.uniform(min, max)


def random_float_rounded(min: float, max: float, r: int) -> float:
    return round(random_float(min, max), r)


def random_vector2(v1: Vector2, v2: Vector2) -> Vector2:
    x = random_float(v1.x, v2.x)
    y = random_float(v1.y, v2.y)
    return Vector2(x, y)

def random_vector2_from_float_lists(min: list[float], max: list[float]) -> Vector2:
    min_vector = Vector2(min[0], min[1])
    max_vector = Vector2(max[0], max[1])
    return random_vector2(min_vector, max_vector)


def config_get(section: str, key: str, default: str = "") -> str:
    return _config.get(section, key, fallback=default)


def init(project_name: str):

    # Location
    homedir = Path.home()
    # Define user dir:
    # * `~/appdata/roaming/PROJECT` on Windows.
    # * `~/.PROJECT` on Linux/MacOS.
    global _user_path
    if os.name == "nt":  # Windows.
        _user_path = Path(homedir, "AppData", "Roaming", project_name)
    else:  # Linux or macOS.
        _user_path = Path(homedir, "."+project_name)
    _user_path.mkdir(parents=True, exist_ok=True)


    # Config
    path = user("user.cfg")
    path.touch(exist_ok=True)

    global _config
    _config = configparser.ConfigParser()
    _config.read(path, "utf-8")


    # Log
    rotation_str = config_get("log", "rotation", "10MB")
    rotation_suffix = rotation_str[-2:]
    global rotation
    rotation = int(rotation_str.removesuffix(rotation_suffix), 10)
    match rotation_suffix:
        case "MB":
            rotation *= 1024 * 1024
        case "KB":
            rotation *= 1024
        case _:
            raise Exception(f"unrecognized rotation suffix '{rotation_suffix}', supported: MB, KB")


async def ainit():
    global loop_task
    loop_task = asyncio.create_task(_log_loop())