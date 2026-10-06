-- Arrays: append with t[#t+1] and table.insert, then table.remove from the end (placemat examples, MIT).
return { n = 4, run = function(n)
  local total = 0
  local insert, remove = table.insert, table.remove
  for _ = 1, n do
    local t = {}
    for i = 1, 100000 do t[#t + 1] = i end
    for i = 1, 100000 do insert(t, i) end
    while #t > 100000 do total = total + remove(t) end
    for i = 1, #t do total = total + t[i] end
  end
  return total
end }
