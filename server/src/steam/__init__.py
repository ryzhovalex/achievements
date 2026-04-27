import asyncio
import json
import math
import sys
import time
from typing import Any

import httpx
import datetime as dt

from core import Model
import core
import database



class User(Model):
    id: int
    steam_id: int
    profile_url: str
    avatar32: str
    avatar64: str
    avatar184: str
    username: str
    fullname: str
    current_game_id: int = 0
    current_game_name: str = ""
    register_time: int = 0

class Achievement(Model):
    id: int
    steam_id: str
    name: str
    description: str
    icon: str
    completed: bool
    unlock_time: int


class Game(Model):
    id: int
    steam_id: int
    name: str
    play_time: int
    last_play_time: int
    achievement_ids: list[int]
    icon: str


_sync_in_progress = False
key = core.config_get("steam", "key")
steamid = core.config_get("steam", "steamid")
syncer_task: asyncio.Task
next_update_time: float
last_update_time: float = 0
sync_period = 60 * 60 * 4


def _end_crucial_task(task: asyncio.Task):
    message = f"Crucial steam sync task '{task.get_name()}' has been finished."
    e = None
    if task.cancelled():
        message += " Cancelled."
    elif task.exception() is not None:
        message += f" Exception: {task.exception()}"
        e = task.exception()
    else:
        message += f" Result: {task.result()}"
    core.log_error(message)
    if e:
        raise e


async def syncer():
    global next_update_time, last_update_time
    core.log_info(f"planned steam sync at {dt.datetime.fromtimestamp(next_update_time)}")

    while True:
        time_diff = last_update_time + sync_period - time.time()
        if time_diff > 0:
            await asyncio.sleep(time_diff)

        core.log_info(f"start planned steam sync")
        async with database.transaction() as con:
            await sync(con)
        last_update_time = time.time()
        async with database.transaction() as con:
            await con.execute("UPDATE steam_sync SET last_time = $1", last_update_time)
        next_update_time = last_update_time + sync_period
        core.log_info(f"planned steam sync has been finished, next one will be at {next_update_time}")


async def sync(con: database.Connection):
    global _sync_in_progress
    if _sync_in_progress:
        raise Exception("cannot start sync: another sync in progress")

    _sync_in_progress = True

    try:
        complete_time = time.time()
        completion_id = await con.fetch_first_value("INSERT INTO steam_completion (complete_time, completion, completed, perfect, total) VALUES ($1, 0, 0, 0, 0) RETURNING id", complete_time)

        games = []

        raw_user = (await request_steam(f"ISteamUser/GetPlayerSummaries/v0002/?key={key}&steamids={steamid}", {}))["response"]["players"][0]
        user = User(
            id = 0,
            steam_id = raw_user["steamid"],
            username = raw_user["profileurl"].split("/")[-2],
            fullname = raw_user["personaname"],
            profile_url = raw_user["profileurl"],
            avatar32 = raw_user["avatar"],
            avatar64 = raw_user["avatarmedium"],
            avatar184 = raw_user["avatarfull"],
            # current_game_id = raw_user["gameid"],
            # current_game_name = raw_user["gameextrainfo"],
            # register_time = raw_user["timecreated"],
        )
        exists = await con.try_fetch_first("SELECT id FROM steam_user")
        if exists is not None:
            await con.execute(
                "UPDATE steam_user SET profile_url = $1, avatar32 = $2, avatar64 = $3, avatar184 = $4, username = $5, fullname = $6, current_game_id = $7, current_game_name = $8, register_time = $9 WHERE steam_id = $10",
                user.profile_url, user.avatar32, user.avatar64, user.avatar184, user.username, user.fullname, user.current_game_id, user.current_game_name, user.register_time, user.steam_id,
            )
        else:
            await con.execute(
                "INSERT INTO steam_user (steam_id, profile_url, avatar32, avatar64, avatar184, username, fullname, current_game_id, current_game_name, register_time) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                user.steam_id, user.profile_url, user.avatar32, user.avatar64, user.avatar184, user.username, user.fullname, user.current_game_id, user.current_game_name, user.register_time,
            )

        r = await request_steam(f"IPlayerService/GetOwnedGames/v1/?key={key}&steamid={steamid}&include_appinfo=true&include_played_free_games=true", {})
        games = r["response"]["games"]

        raw_achievements = []
        for raw_game in games:

            game = Game(
                id = 0,
                steam_id = raw_game["appid"],
                name = raw_game["name"],
                play_time = raw_game["playtime_forever"] * 60,
                last_play_time = raw_game["rtime_last_played"],
                achievement_ids = [],
                icon = raw_game["img_icon_url"],
            )

            row = await con.try_fetch_first("SELECT * FROM steam_game WHERE steam_id = $1", game.steam_id)
            if row:
                for col, new_value in [
                    ("name", game.name),
                    ("play_time", game.play_time),
                    ("last_play_time", game.last_play_time),
                    ("icon", game.icon),
                ]:
                    if row[col] == new_value:
                        continue
                    await con.execute(
                        f"UPDATE steam_game SET {col} = $1 WHERE steam_id = $2",
                        new_value, game.steam_id,
                    )
                    await con.execute(f"INSERT INTO steam_completion_update_modification (target_table, target_column, old_value, new_value, completion_id) VALUES ('game', $1, $2, $3, $4)", col, core.adaptively_to_bytes(row[col], False), core.adaptively_to_bytes(new_value, False), completion_id)
            else:
                await con.execute(
                    "INSERT INTO steam_game (steam_id, name, play_time, last_play_time, icon) VALUES ($1, $2, $3, $4, $5)",
                    game.steam_id, game.name, game.play_time, game.last_play_time, game.icon,
                )
                row = await con.fetch_first("SELECT * FROM steam_game WHERE steam_id = $1", game.steam_id)
                payload = row.to_dict()
                await con.execute(f"INSERT INTO steam_completion_insert_modification (target_table, payload, completion_id) VALUES ('game', $1, $2)", json.dumps(payload), completion_id)

            # stat = await request_steam(f"ISteamUserStats/GetUserStatsForGame/v0002/?key={key}&steamid={steamid}&appid={game['appid']}", {})
            raw_stat = await request_steam(f"ISteamUserStats/GetPlayerAchievements/v0001/?key={key}&steamid={steamid}&appid={game.steam_id}&l=en", {})
            raw_achievements.append(raw_stat)
            stat = []
            if raw_stat != {}:
                stat = raw_stat["playerstats"].get("achievements", [])
            for s in stat:
                achievement = Achievement(
                    id = 0,
                    steam_id = s["apiname"],
                    name = s["name"],
                    description = s["description"],
                    icon = "",  # @todo find out
                    completed = s["achieved"],
                    unlock_time = s["unlocktime"],
                )

                row = await con.try_fetch_first("SELECT * FROM steam_achievement achievement JOIN steam_game game ON game.id = achievement.game_id WHERE achievement.steam_id = $1 AND game.steam_id = $2", achievement.steam_id, game.steam_id)
                if row:
                    for col, new_value in [
                        ("name", achievement.name),
                        ("description", achievement.description),
                        ("icon", achievement.icon),
                        ("completed", achievement.completed),
                        ("unlock_time", achievement.unlock_time)
                    ]:
                        if row[col] == new_value:
                            continue
                        await con.execute(f"UPDATE steam_achievement SET {col} = $1 WHERE steam_id = $2 AND game_id = (SELECT id FROM steam_game WHERE steam_id = $3)", new_value, achievement.steam_id, game.steam_id)
                        await con.execute(f"INSERT INTO steam_completion_update_modification (target_table, target_column, old_value, new_value, completion_id) VALUES ('achievement', $1, $2, $3, $4)", col, core.adaptively_to_bytes(row[col], False), core.adaptively_to_bytes(new_value, False), completion_id)
                else:
                    await con.execute(
                        "INSERT INTO steam_achievement (steam_id, name, description, icon, completed, unlock_time, game_id) VALUES ($1, $2, $3, $4, $5, $6, (SELECT id FROM steam_game WHERE steam_id = $7))",
                        achievement.steam_id, achievement.name, achievement.description, achievement.icon, achievement.completed, achievement.unlock_time, game.steam_id,
                    )
                    row = await con.fetch_first("SELECT * FROM steam_achievement WHERE steam_id = $1 AND game_id = (SELECT id FROM steam_game WHERE steam_id = $2)", achievement.steam_id, game.steam_id)
                    payload = dict(row)
                    await con.execute(f"INSERT INTO steam_completion_insert_modification (target_table, payload, completion_id) VALUES ('achievement', $1, $2)", json.dumps(payload), completion_id)

        completion = 0.0
        completed_achievements = 0
        total_achievements = 0
        perfect = 0
        perfect_map = {}
        perfect_game_ids = []

        rows = await con.fetch("SELECT game_id, completed FROM steam_achievement ORDER BY id DESC")
        for row in rows:
            total_achievements += 1
            if row.completed:
                completed_achievements += 1

            if row.game_id not in perfect_map:
                perfect_map[row.game_id] = 0

            if row.completed:
                perfect_map[row.game_id] -= 1
            perfect_map[row.game_id] += 1

        for k, v in perfect_map.items():
            assert v >= 0
            if v == 0:
                perfect += 1
                perfect_game_ids.append(k)

        placeholders = ", ".join(f"${i+1}" for i, _ in enumerate(perfect_game_ids))
        await con.execute(f"UPDATE steam_game SET perfect = true WHERE id IN ({placeholders})", *perfect_game_ids)

        if total_achievements > 0:
            completion = completed_achievements / total_achievements

        records = await con.fetch("SELECT game_id, completed FROM steam_achievement")
        data = {}
        for record in records:
            if record.game_id not in data:
                # [completed, total]
                data[record.game_id] = [0, 0]
            if record.completed:
                data[record.game_id][0] += 1
            data[record.game_id][1] += 1
        average_completion = 0
        for v in data.values():
            average_completion += v[0] / v[1]
        average_completion /= len(data)

        await con.execute("UPDATE steam_completion SET completion = $1, completed = $2, total = $3, perfect = $4, average_completion = $5 WHERE id = $6", completion, completed_achievements, total_achievements, perfect, average_completion, completion_id)


        await _update_achievement_stats(con)


        core.log_info(f"sync: finished: loaded {total_achievements}, completed {completed_achievements}, completion {(completion*100):.1f}%, perfect {perfect}")
    finally:
        _sync_in_progress = False


async def _update_achievement_stats(con: database.Connection):
    core.log_info("updating achievement stats")
    rows = await con.fetch("SELECT id, steam_id FROM steam_game")
    for row in rows:
        game_id = row.id
        game_steam_id = row.steam_id

        data = {}

        r = await request_steam(f"ISteamUserStats/GetGlobalAchievementPercentagesForApp/v0002/?gameid={game_steam_id}", {})
        for ach in r.get("achievementpercentages", {}).get("achievements", []):
            ach_steam_id = ach["name"]
            ach_rarity = ach["percent"]
            if ach_steam_id not in data:
                data[ach_steam_id] = {}
            data[ach_steam_id]["rarity"] = ach_rarity

        r = await request_steam(f"ISteamUserStats/GetSchemaForGame/v2/?key={key}&appid={game_steam_id}", {})
        for ach in r.get("game", {}).get("availableGameStats", {}).get("achievements", []):
            ach_steam_id = ach["name"]
            ach_hidden = ach["hidden"]
            ach_icon = ach["icon"]
            ach_icon_gray = ach["icongray"]

            if ach_steam_id not in data:
                data[ach_steam_id] = {}

            data[ach_steam_id]["name"] = ach.get("displayName", "")
            data[ach_steam_id]["description"] = ach.get("description", "")
            data[ach_steam_id]["hidden"] = bool(ach_hidden)
            data[ach_steam_id]["icon"] = ach_icon
            data[ach_steam_id]["icon_gray"] = ach_icon_gray

        for ach_steam_id, ach in data.items():
            try:
                rarity = float(ach.get("rarity", None))
            except Exception as e:
                rarity = None

            await con.execute("UPDATE steam_achievement SET rarity = $1, hidden = $2, icon = $3, icon_gray = $4, name = $5, description = $6 WHERE steam_id = $7 AND game_id = $8", rarity, ach.get("hidden", False), ach.get("icon", ""), ach.get("icon_gray", None), ach.get("name", ""), ach.get("description", ""), ach_steam_id, game_id)

_cached_achievements = {}

async def request_steam(route: str, default: Any, *, log_error: bool = False) -> Any:
    global _cached_achievements

    # if build.debug:
    #     cache_name = None
    #     if route.startswith("ISteamUser/GetPlayerSummaries/v0002"):
    #         cache_name = "user"
    #     elif route.startswith("IPlayerService/GetOwnedGames/v1"):
    #         cache_name = "games"
    #     elif route.startswith("ISteamUserStats/GetPlayerAchievements/v0001"):
    #         cache_name = "achievements"

    #     if cache_name:
    #         requested_game_steam_id = ""
    #         if cache_name == "achievements":
    #             match = re.search(r"&appid=(\d+)", route)
    #             assert match
    #             requested_game_steam_id = match.group(1)
    #         if cache_name == "achievements" and cached_achievements:
    #             r = cached_achievements.get(requested_game_steam_id, default)
    #             return r
    #         async with aiofiles.open(location.source(f"data/{cache_name}.json")) as f:
    #             content = json.loads(await f.read())
    #             if cache_name == "achievements":
    #                 cached_achievements = content
    #                 return cached_achievements.get(requested_game_steam_id, default)
    #             return content


    global current_parallel_requests
    global max_parallel_requests

    addr = "https://api.steampowered.com/"

    async with httpx.AsyncClient() as client:

        try:
            r = await client.get(addr + route)
            if r.status_code >= 400:
                if log_error:
                    response_text = r.text
                    core.log_error(f"request to '{route}' resulted in a response #{r.status_code} with an error: {response_text}")
                return default
            return r.json()
        except Exception as e:
            core.log_error(f"request to '{route}' resulted in error: {e}")
            return default


async def init():
    global syncer_task, last_update_time, next_update_time
    async with database.transaction() as con:
        last_update_time = await con.fetch_first_value("SELECT last_time FROM steam_sync")
        next_update_time = last_update_time + sync_period
    syncer_task = asyncio.create_task(syncer())
    syncer_task.add_done_callback(_end_crucial_task)