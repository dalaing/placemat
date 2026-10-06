-- Closures: creating them, calling them, and updating shared upvalues (placemat examples, MIT).
local function counter(step)
  local c = 0
  return function() c = c + step; return c end, function() return c end
end
return { n = 4, run = function(n)
  local total = 0
  for _ = 1, n do
    for i = 1, 20000 do
      local inc, get = counter(i % 5 + 1)
      for _ = 1, 20 do inc() end
      total = total + get()
    end
  end
  return total
end }
