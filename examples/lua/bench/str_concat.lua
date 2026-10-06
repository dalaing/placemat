-- String building: '..' on short strings, then table.concat (placemat examples, MIT).
return { n = 8, run = function(n)
  local total = 0
  for _ = 1, n do
    local t = {}
    for i = 1, 20000 do
      t[#t + 1] = "item" .. i .. ";" .. (i % 7)
    end
    local s = table.concat(t, ",")
    local u = ""
    for i = 1, 2000 do u = u .. "x" end
    total = total + #s + #u
  end
  return total
end }
