-- Integer arithmetic and bitwise operators in a numeric for loop (placemat examples, MIT).
return { n = 8, run = function(n)
  local x = 0
  for _ = 1, n do
    for i = 1, 1000000 do
      x = (x + i * 7) ~ (i >> 3)
      x = x & 0xffffffff
    end
  end
  return x
end }
