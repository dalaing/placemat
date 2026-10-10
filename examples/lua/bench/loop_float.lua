-- Floating-point loop: Mandelbrot iteration counts over a grid (placemat examples, MIT).
return { n = 12, run = function(n)
  local total = 0
  for _ = 1, n do
    local size = 120
    for y = 0, size - 1 do
      local ci = 2.0 * y / size - 1.0
      for x = 0, size - 1 do
        local cr = 2.5 * x / size - 2.0
        local zr, zi, k = 0.0, 0.0, 0
        while k < 50 and zr * zr + zi * zi < 4.0 do
          zr, zi = zr * zr - zi * zi + cr, 2.0 * zr * zi + ci
          k = k + 1
        end
        total = total + k
      end
    end
  end
  return total
end }
