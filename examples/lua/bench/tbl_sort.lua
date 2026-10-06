-- table.sort on pseudo-random integers, with the default order and with a comparator (placemat examples, MIT).
return { n = 2, run = function(n)
  local total = 0
  for r = 1, n do
    local t, u = {}, {}
    local x = 12345 + r
    for i = 1, 40000 do
      x = (x * 1103515245 + 12345) & 0x7fffffff
      t[i] = x
      u[i] = x % 1000
    end
    table.sort(t)
    table.sort(u, function(a, b) return a > b end)
    total = total + t[1] + t[#t] + u[1] + u[20000]
  end
  return total
end }
