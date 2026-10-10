-- Variadic calls: select('#', ...), table.pack/unpack, multiple returns (placemat examples, MIT).
local function sum(...)
  local s = 0
  for i = 1, select("#", ...) do s = s + (select(i, ...)) end
  return s
end
local function swap3(a, b, c) return c, b, a end
return { n = 3, run = function(n)
  local total = 0
  local pack, unpack = table.pack, table.unpack
  for _ = 1, n do
    for i = 1, 40000 do
      total = total + sum(i, 1, 2, 3)
      local p = pack(swap3(i, i + 1, i + 2))
      total = total + p.n + unpack(p, 1, 1)
    end
  end
  return total
end }
