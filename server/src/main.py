import asyncio
import signal

import core
core.init("achievements-server")

import database
import steam



async def _main():
    await core.ainit()
    await database.init()
    await steam.init()

    stop_event = asyncio.Event()

    def signal_handler(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        print("Received shutdown signal, stopping...")
    finally:
        print("Cleaning up resources...")



def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()