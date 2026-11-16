-- vp_load_log.lua — Valkyrie Profile (SLUS-01156) resource/text load logger for PCSX-Redux.
--
-- Arms non-pausing breakpoints on the load pipeline reverse-engineered in
-- VP_DIALOGUE_FORMAT.md §1.1, and logs every event to console + a file so you can
-- just play and collect the resource->container->sector table.
--
-- HOW TO RUN
--   PCSX-Redux ▸ Debug ▸ Show Lua console  (or the Lua editor), then paste this whole
--   file, or:  dofile('C:/path/to/vp_load_log.lua')
--   Play through a scene/dialog. Watch the console and vp_load_log.txt.
--   Stop & clear breakpoints with:  vpStop()
--   After you see a [CdRead] line, note its caller= address (the streaming wrapper that
--   holds the sector) and add:  vpWatch(0xCALLER, 'streamer')   to log its a0..a3 (the sector).
--
-- Addresses (EXE resident 0x80010000.., engine overlay @0x8002F824):
--   decompress_resource   0x800122c0   (a1=container, a2=dst, a3=entry index)
--   open_resource_stream  0x8001ab48   (a2=container, a1=descriptor, a3=base)
--   CdRead (libcd)        0x8002714c   (a0=nsectors, a1=buf, a2=mode) — sector set by a
--                                       prior CdlSetloc in the *caller*, so log the caller too.

local LOGFILE = 'vp_load_log.txt'        -- relative to PCSX-Redux's working dir; use an
                                         -- absolute path if you prefer.

-- On each load the breakpoint *stashes* the container bytes into memory (a fast ffi.string
-- copy — no file I/O in the callback, so it can't destabilize play). Capturing at the load
-- instant avoids the stale-buffer problem: the container RAM is reused across scenes, so a
-- manual dump after the fact often catches the wrong resource. Run vpFlush() to write the
-- stash to files; vpDump(addr) is a one-off manual capture of whatever is resident now.
local DUMP_CONTAINERS = true
local DUMP_PREFIX = 'container_'          -- output file name prefix (in PCSX-Redux's cwd)
local DUMP_MAX = 0x10000                  -- cap each capture (64 KB is plenty to pin a sector)
local CODECS = true                       -- also break on the raw LZ codecs (catches the lazy/
                                          -- streamed dialogue decode); set false if it gets noisy

local ffi = require('ffi')
local mem = PCSX.getMemPtr()             -- uint8_t* over the 2 MB main RAM
local bps = {}
local dumped = {}                        -- containers already written this session
local logf = io.open(LOGFILE, 'a')
local lastline = nil

-- PCSX-Redux Lua is LuaJIT (5.1): no '&' operator, so mask via modulo. Addresses are
-- non-negative, so (a % 0x200000) == (a & 0x1fffff) — the offset into the 2 MB main RAM.
local function r8(a)  return mem[a % 0x200000] end
local function r32(a)
  local o = a % 0x200000
  return mem[o] + mem[o+1]*256 + mem[o+2]*65536 + mem[o+3]*16777216
end
local function rstr(a, n)                 -- printable run, stops at first non-ASCII
  local t = {}
  for i = 0, n-1 do
    local c = mem[(a+i) % 0x200000]
    if c < 32 or c > 126 then break end
    t[#t+1] = string.char(c)
  end
  return table.concat(t)
end
local function gpr() return PCSX.getRegisters().GPR.n end
local function hx(v) return string.format('0x%08x', v % 0x100000000) end

local function emit(s)
  if s == lastline then return end        -- squelch identical repeats
  lastline = s
  print(s)
  if logf then logf:write(s, '\n'); logf:flush() end
end

local STASH = {}                          -- addr -> { bytes, typ, len, caller }  (captured at load)
local codecSeen = {}                      -- LZ codec sources already logged (dedupe)

local function clampLen(off, len)
  if not len or len <= 0 or len > DUMP_MAX then len = DUMP_MAX end
  if off + len > 0x200000 then len = 0x200000 - off end          -- clamp to RAM
  return len
end

local function writeRam(addr, len, tag)   -- file write — call OUTSIDE breakpoints only
  local off = addr % 0x200000
  len = clampLen(off, len)
  local path = string.format('%s0x%08x%s.bin', DUMP_PREFIX, addr % 0x100000000, tag or '')
  local f = io.open(path, 'wb')
  if not f then return nil end
  f:write(ffi.string(mem + off, len)); f:close()
  return path, len
end

-- Runs inside the breakpoint: copy the live container bytes into memory now (no file I/O).
local function maybeStash(c, caller)
  if not DUMP_CONTAINERS or STASH[c] then return end
  if rstr(c, 3) ~= 'SLZ' then return end
  local off = c % 0x200000
  local len = clampLen(off, r32(c + 8) + 16)        -- +8 uncompressed length + 16B header
  STASH[c] = { bytes = ffi.string(mem + off, len), typ = r8(c + 3), len = len, caller = caller }
  emit(string.format('   stashed %s (%d bytes) — run vpFlush() to write it', hx(c), len))
end

-- Write every stashed container to a file, then clear the stash so the next scene starts
-- fresh. Console-safe (does the file I/O outside any breakpoint).
function vpFlush()
  local n = 0
  for addr, e in pairs(STASH) do
    local path = string.format('%s0x%08x.bin', DUMP_PREFIX, addr % 0x100000000)
    local f = io.open(path, 'wb')
    if f then
      f:write(e.bytes); f:close(); n = n + 1
      print(string.format('wrote %s (%d bytes, type %d, caller %s)',
            path, #e.bytes, e.typ, hx(e.caller)))
    end
  end
  print('flushed ' .. n .. ' container(s); stash cleared.')
  STASH = {}
end

-- One-off manual capture of whatever is resident at addr right now (console-safe).
function vpDump(addr, len)
  local path, n = writeRam(addr, len, '_manual')
  print(path and ('wrote ' .. path .. ' (' .. n .. ' bytes)') or 'dump failed (bad path?)')
end

-- per-event handlers (return false => do NOT pause the emulator) -----------------
local function onDecompress()
  local r = gpr(); local c = r.a1
  emit(string.format('[decompress] caller=%s container=%s magic=%q type=%d len=%d index=%d dst=%s',
        hx(r.ra), hx(c), rstr(c,3), r8(c+3), r32(c+8), r.a3, hx(r.a2)))
  maybeStash(c, r.ra)
  return false
end

local function onOpenStream()
  local r = gpr(); local c = r.a2
  emit(string.format('[open/stream] caller=%s container=%s magic=%q type=%d len=%d descriptor=%s base=%s',
        hx(r.ra), hx(c), rstr(c,3), r8(c+3), r32(c+8), hx(r.a1), hx(r.a3)))
  maybeStash(c, r.ra)
  return false
end

local function onCdRead()
  local r = gpr()
  emit(string.format('[CdRead] caller=%s nsectors=%d buf=%s mode=%s  (sector = prior CdlSetloc in caller)',
        hx(r.ra), r.a0, hx(r.a1), hx(r.a2)))
  return false
end

-- Raw LZ codec entry (a1=src payload, a2=dst). The dispatcher calls these with
-- src = container+16, so the SLZ container base is src-16 — stash that for matching.
local function onLz(tag)
  return function()
    local r = gpr()
    local src = r.a1
    local base = src % 0x100000000 - 16
    local isslz = base >= 0 and rstr(base, 3) == 'SLZ'
    local key = isslz and base or src         -- one line per container (or per src)
    if not codecSeen[key] then
      codecSeen[key] = true
      emit(string.format('[%s] caller=%s src=%s dst=%s%s', tag, hx(r.ra), hx(src), hx(r.a2),
            isslz and string.format('  container=%s type=%d len=%d', hx(base), r8(base+3), r32(base+8)) or ''))
      if isslz then maybeStash(base, r.ra) end
    end
    return false
  end
end

-- arming -------------------------------------------------------------------------
local function arm(addr, name, cb)
  local bp = PCSX.addBreakpoint(addr, 'Exec', 4, name, cb)
  bps[#bps+1] = bp
  emit('  + ' .. name .. ' @ ' .. hx(addr))
end

-- public: log any function's entry (a0..a3 + caller). Use for the streaming wrapper
-- once you learn its address from a [CdRead] caller= line.
function vpWatch(addr, name)
  arm(addr, name or ('watch_'..hx(addr)), function()
    local r = gpr()
    emit(string.format('[%s @%s] caller=%s a0=%s a1=%s a2=%s a3=%s',
          name or 'watch', hx(addr), hx(r.ra), hx(r.a0), hx(r.a1), hx(r.a2), hx(r.a3)))
    return false
  end)
end

-- public: remove all breakpoints and close the log.
function vpStop()
  for _, bp in ipairs(bps) do
    pcall(function() bp:remove() end)     -- method name varies by Redux version;
    pcall(function() bp:disable() end)    -- try both, then let GC reap the handle.
  end
  bps = {}
  collectgarbage()                        -- Redux breakpoint handles are removed on GC
  if logf then logf:flush(); logf:close(); logf = nil end
  print('VP load logger stopped; breakpoints cleared.')
end

emit('=== VP load logger armed ' .. os.date() .. ' ===')
arm(0x800122c0, 'decompress_resource', onDecompress)
arm(0x8001ab48, 'open_resource_stream', onOpenStream)
arm(0x8002714c, 'CdRead',               onCdRead)
if CODECS then
  arm(0x800123cc, 'lz1', onLz('lz1'))     -- single-stream LZ
  arm(0x80012874, 'lz2', onLz('lz2'))     -- multi-stream LZ
end
print("Logging to '" .. LOGFILE .. "'. Play a scene. vpStop() to end; vpWatch(addr,name) to add one.")
