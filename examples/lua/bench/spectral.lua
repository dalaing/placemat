-- Spectral norm: nested loops, a small function call per element, float division (placemat examples, MIT).
local function A(i, j)
  local ij = i + j - 1
  return 1.0 / (ij * (ij - 1) * 0.5 + i)
end
local function Av(x, y, N)
  for i = 1, N do
    local a = 0
    for j = 1, N do a = a + x[j] * A(i, j) end
    y[i] = a
  end
end
local function Atv(x, y, N)
  for i = 1, N do
    local a = 0
    for j = 1, N do a = a + x[j] * A(j, i) end
    y[i] = a
  end
end
return { n = 6, run = function(n)
  local s = 0
  for _ = 1, n do
    local N = 120
    local u, v, t = {}, {}, {}
    for i = 1, N do u[i] = 1 end
    for _ = 1, 10 do Av(u, t, N); Atv(t, v, N); Av(v, t, N); Atv(t, u, N) end
    local vBv, vv = 0, 0
    for i = 1, N do vBv = vBv + u[i] * v[i]; vv = vv + v[i] * v[i] end
    s = s + math.sqrt(vBv / vv)
  end
  return s
end }
