-- Coroutine switching: a generator with yield/resume, and a producer-consumer pipeline (placemat examples, MIT).
local function gen(m)
  return coroutine.wrap(function() for i = 1, m do coroutine.yield(i) end end)
end
local function filter(src)
  return coroutine.wrap(function()
    for v in src do if v % 3 ~= 0 then coroutine.yield(v * 2) end end
  end)
end
return { n = 4, run = function(n)
  local total = 0
  for _ = 1, n do
    for v in filter(gen(100000)) do total = total + v end
    local co = coroutine.create(function(a) while true do a = coroutine.yield(a + 1) end end)
    local x = 0
    for _ = 1, 50000 do local _, y = coroutine.resume(co, x); x = y end
    total = total + x
  end
  return total
end }
