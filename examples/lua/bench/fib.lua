-- Recursive Fibonacci: calls, returns, integer compare and add (placemat examples, MIT).
local function fib(n)
  if n < 2 then return n end
  return fib(n - 1) + fib(n - 2)
end
return { n = 20, run = function(n)
  local s = 0
  for _ = 1, n do s = s + fib(25) end
  return s
end }
