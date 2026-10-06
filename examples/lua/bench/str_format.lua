-- string.format with integers, strings and floats (placemat examples, MIT).
return { n = 4, run = function(n)
  local total = 0
  local fmt = string.format
  for _ = 1, n do
    for i = 1, 30000 do
      local s = fmt("%d:%s:%.3f:%5x", i, "abc", i / 7, i)
      total = total + #s
    end
  end
  return total
end }
