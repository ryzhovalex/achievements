MyCharacterAchievements = MyCharacterAchievements or {}

local function Scan(id)
    local id, name, points, completed, month, day, year = GetAchievementInfo(id)
    if id then
        MyCharacterAchievements[id] = {
            ["name"] = name,
            ["points"] = points,
            ["done"] = completed,
            ["date"] = completed and string.format("%04d-%02d-%02d", year, month, day) or nil
        }
    end
end

local function Export()
    MyCharacterAchievements = {}
    local cats = GetCategoryList()
    for _, cat in ipairs(cats) do
        local num = GetCategoryNumAchievements(cat)
        for i = 1, num do
            local id = GetAchievementInfo(cat, i)
            if id then Scan(id) end
        end
    end
end

local f = CreateFrame("Frame")
f:RegisterEvent("PLAYER_LOGOUT")
f:SetScript("OnEvent", Export)
