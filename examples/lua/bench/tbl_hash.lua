-- Hash tables with string keys: insert, look up, iterate with pairs (placemat examples, MIT).
return { n = 2, run = function(n)
  local total = 0
  for _ = 1, n do
    local t = {}
    for i = 1, 50000 do t["key" .. i] = i end
    for i = 1, 50000, 3 do total = total + (t["key" .. i] or 0) end
    for k, v in pairs(t) do if v % 1000 == 0 then total = total + #k end end
  end
  return total
end }
