-- Method calls through metatables and __index chains (placemat examples, MIT).
local Point = {}
Point.__index = Point
function Point.new(x, y) return setmetatable({ x = x, y = y }, Point) end
function Point:add(o) return Point.new(self.x + o.x, self.y + o.y) end
function Point:norm1() return math.abs(self.x) + math.abs(self.y) end
local Point3 = setmetatable({}, { __index = Point })
Point3.__index = Point3
function Point3.new(x, y, z) local p = Point.new(x, y); p.z = z; return setmetatable(p, Point3) end
function Point3:norm1() return Point.norm1(self) + math.abs(self.z) end
return { n = 3, run = function(n)
  local total = 0
  for _ = 1, n do
    local acc = Point.new(0, 0)
    for i = 1, 60000 do
      local p = Point3.new(i, -i, i % 3)
      acc = acc:add(p)
      total = total + p:norm1()
    end
    total = total + acc:norm1()
  end
  return total
end }
