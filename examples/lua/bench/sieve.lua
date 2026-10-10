-- Sieve of Eratosthenes over a boolean array of 500k entries (8 MB) (placemat examples, MIT).
return { n = 6, run = function(n)
  local count = 0
  for _ = 1, n do
    local N = 500000
    local composite = {}
    for i = 1, N do composite[i] = false end
    for i = 2, N do
      if not composite[i] then
        count = count + 1
        for j = i * i, N, i do composite[j] = true end
      end
    end
  end
  return count
end }
