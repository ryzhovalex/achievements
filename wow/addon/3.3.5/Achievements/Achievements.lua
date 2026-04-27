MyCharacterAchievements = MyCharacterAchievements or {}

local function Scan(id)
    local id, name, points, completed, month, day, year = GetAchievementInfo(id)
    if id and points > 0 then
        MyCharacterAchievements[id] = {
            ["name"] = name,
            ["points"] = points,
            ["done"] = completed,
            ["date"] = completed and string.format("%04d-%02d-%02d", 2000 + year, month, day) or nil
        }
    end
end

local function Export()
    MyCharacterAchievements = {}
    local cats = GetCategoryList()
    for _, catID in ipairs(cats) do
        local num = GetCategoryNumAchievements(catID)
        for i = 1, num do
            local id = GetAchievementInfo(catID, i)
            if id then Scan(id) end
        end
        
        for _, subID in ipairs(cats) do
            local _, parentID = GetCategoryInfo(subID)
            if parentID == catID then
                local numSub = GetCategoryNumAchievements(subID)
                for j = 1, numSub do
                    local id = GetAchievementInfo(subID, j)
                    if id then Scan(id) end
                end
            end
        end
    end
end

local f = CreateFrame("Frame")
f:RegisterEvent("PLAYER_LOGOUT")
f:SetScript("OnEvent", Export)
