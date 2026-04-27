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


from aiohttp import web

async def handle_upload(request):
    try:
        data = await request.json()
        
        
        print(f"Received {len(data)} achievements: {data}")
        
        return web.Response(status=200, text="OK")
    except Exception as e:
        return web.Response(status=400, text=str(e))

app = web.Application()
app.add_routes([web.post('/wow/upload', handle_upload)])

def main():
    asyncio.run(_main())

    web.run_app(app, port=8065)

if __name__ == "__main__":
    main()