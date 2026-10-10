-- Streaming over large float arrays (1 MB each): c[i] = a[i] * k + b[i], then a reduction (placemat examples, MIT).
-- Three large blocks used together: a candidate for L1 set conflicts (the data axis).
local N = 65536
local a, b, c = {}, {}, {}
for i = 1, N do a[i] = i * 0.5; b[i] = (N - i) * 0.25; c[i] = 0.0 end
return { n = 80, run = function(n)
  local s = 0.0
  for r = 1, n do
    local k = r * 0.001
    for i = 1, N do c[i] = a[i] * k + b[i] end
    for i = 1, N, 8 do s = s + c[i] end
  end
  return s
end }
