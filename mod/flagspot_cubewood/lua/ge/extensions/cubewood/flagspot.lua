-- Flag this spot (dev-only, Belfast QA). Loads as extension "cubewood_flagspot".
-- On flag(): appends one JSON line to flagspot/flags.jsonl in the BeamNG user folder
-- and takes a screenshot into flagspot/shots/. Lua 5.1 (LuaJIT).

local M = {}
local logTag = 'cubewood_flagspot'

local DIR = 'flagspot/'
local SHOT_DIR = DIR .. 'shots/'
local FLAGS_FILE = DIR .. 'flags.jsonl'
local DEBOUNCE_S = 1.0        -- a wheel button can chatter; ignore repeats inside this window
local SHOT_CHECK_S = 4.0      -- warn if the screenshot file is still missing after this long

local counter = 0
local lastFlagClock = -1000
local pendingChecks = {}      -- { rel = path, due = os.clock() }

local function round(v, dp)
  local m = 10 ^ (dp or 2)
  return math.floor(v * m + 0.5) / m
end

local function ensureDirs()
  if not FS:directoryExists(SHOT_DIR) then FS:directoryCreate(SHOT_DIR, true) end
end

local function vehicleInfo(veh)
  local info = { model = veh:getJBeamFilename() }
  local ok, vd = pcall(function() return extensions.core_vehicle_manager.getVehicleData(veh:getID()) end)
  if ok and vd and vd.config and vd.config.partConfigFilename then
    info.config = tostring(vd.config.partConfigFilename):match('([^/\\]+)%.pc$') or tostring(vd.config.partConfigFilename)
  end
  return info
end

local function feedback(n)
  -- the HTML message only shows on the desktop window; the sound is what the driver hears in the headset
  pcall(function() ui_message('Flag ' .. n .. ' saved', 3, 'flagspot') end)
  pcall(function() Engine.Audio.playOnce('AudioGui', 'event:>UI>Missions>Info_Open') end)
end

-- Bound to the input action. Returns the record (also useful when called from the MCP run_lua tool).
local function flag(note)
  local now = os.clock()
  if now - lastFlagClock < DEBOUNCE_S then
    log('I', logTag, 'ignored repeat press')
    return nil
  end

  local veh = getPlayerVehicle(0)
  if not veh then
    log('W', logTag, 'no player vehicle, nothing flagged')
    pcall(function() ui_message('Flag not saved: no vehicle', 3, 'flagspot') end)
    return nil
  end
  lastFlagClock = now
  counter = counter + 1
  ensureDirs()

  local pos = veh:getPosition()
  local dir = veh:getDirectionVector()
  local heading = math.deg(math.atan2(dir.x, dir.y)) % 360   -- 0 = north (+Y), clockwise
  local speed = veh:getVelocity():length()                   -- m/s
  local camPos = core_camera and core_camera.getPosition() or nil

  local stamp = os.date('%Y%m%d_%H%M%S')
  local shotRel = SHOT_DIR .. 'flag_' .. stamp .. '_' .. counter
  local shotFile = shotRel .. '.jpg'

  local vr = false
  pcall(function() vr = render_openxr and render_openxr.isSessionRunning() or false end)

  local rec = {
    schema = 1,
    time_utc = os.date('!%Y-%m-%dT%H:%M:%SZ'),
    time_local = os.date('%Y-%m-%dT%H:%M:%S'),
    level = getCurrentLevelIdentifier(),
    vehicle = vehicleInfo(veh),
    pos = { x = round(pos.x), y = round(pos.y), z = round(pos.z) },
    heading_deg = round(heading, 1) % 360,
    speed_ms = round(speed),
    speed_mph = round(speed * 2.2369363),
    camera_pos = camPos and { x = round(camPos.x), y = round(camPos.y), z = round(camPos.z) } or nil,
    vr_session = vr,
    screenshot = shotFile,                                   -- relative to the user folder
    screenshot_abs = (FS:getUserPath() .. shotFile):gsub('\\', '/'),
    note = note and tostring(note) or '',
  }

  -- screenshot first so a failed capture is recorded as such
  local ok, id = pcall(function()
    return require('screenshot').createScreenshotTracked({ filename = shotRel, writeJPG = true, superSampling = 1 })
  end)
  if not ok or not id or id == 0 then
    log('W', logTag, 'screenshot failed: ' .. tostring(id))
    rec.screenshot = nil
    rec.screenshot_abs = nil
    rec.screenshot_error = ok and 'createScreenshot returned no job' or tostring(id)
  else
    pendingChecks[#pendingChecks + 1] = { rel = shotFile, due = now + SHOT_CHECK_S }
  end

  local f = io.open(FLAGS_FILE, 'a')
  if not f then
    log('E', logTag, 'cannot open ' .. FLAGS_FILE .. ' for append')
    return nil
  end
  f:write(jsonEncode(rec), '\n')
  f:close()

  log('I', logTag, string.format('flag %d at %.1f,%.1f,%.1f heading %.0f speed %.1f m/s level %s',
    counter, pos.x, pos.y, pos.z, heading, speed, tostring(rec.level)))
  feedback(counter)
  return rec
end

local function onUpdate()
  if #pendingChecks == 0 then return end
  local now = os.clock()
  for i = #pendingChecks, 1, -1 do
    local p = pendingChecks[i]
    if now >= p.due then
      if not FS:fileExists(p.rel) then
        log('W', logTag, 'screenshot file missing after ' .. SHOT_CHECK_S .. ' s: ' .. p.rel)
      end
      table.remove(pendingChecks, i)
    end
  end
end

M.flag = flag
M.onUpdate = onUpdate
M.flagsFile = FLAGS_FILE

return M
