-- N-body simulation: float arithmetic on table fields (placemat examples, MIT; after the Benchmarks Game's algorithm).
local sqrt = math.sqrt
local PI = math.pi
local SOLAR_MASS = 4 * PI * PI
local DPY = 365.24
local function bodies()
  return {
    { x = 0, y = 0, z = 0, vx = 0, vy = 0, vz = 0, mass = SOLAR_MASS },
    { x = 4.84143144246472090e+00, y = -1.16032004402742839e+00, z = -1.03622044471123109e-01,
      vx = 1.66007664274403694e-03 * DPY, vy = 7.69901118419740425e-03 * DPY, vz = -6.90460016972063023e-05 * DPY,
      mass = 9.54791938424326609e-04 * SOLAR_MASS },
    { x = 8.34336671824457987e+00, y = 4.12479856412430479e+00, z = -4.03523417114321381e-01,
      vx = -2.76742510726862411e-03 * DPY, vy = 4.99852801234917238e-03 * DPY, vz = 2.30417297573763929e-05 * DPY,
      mass = 2.85885980666130812e-04 * SOLAR_MASS },
    { x = 1.28943695621391310e+01, y = -1.51111514016986312e+01, z = -2.23307578892655734e-01,
      vx = 2.96460137564761618e-03 * DPY, vy = 2.37847173959480950e-03 * DPY, vz = -2.96589568540237556e-05 * DPY,
      mass = 4.36624404335156298e-05 * SOLAR_MASS },
    { x = 1.53796971148509165e+01, y = -2.59193146099879641e+01, z = 1.79258772950371181e-01,
      vx = 2.68067772490389322e-03 * DPY, vy = 1.62824170038242295e-03 * DPY, vz = -9.51592254519715870e-05 * DPY,
      mass = 5.15138902046611451e-05 * SOLAR_MASS },
  }
end
local function advance(bs, nb, dt)
  for i = 1, nb do
    local bi = bs[i]
    local bix, biy, biz, bimass = bi.x, bi.y, bi.z, bi.mass
    local bivx, bivy, bivz = bi.vx, bi.vy, bi.vz
    for j = i + 1, nb do
      local bj = bs[j]
      local dx, dy, dz = bix - bj.x, biy - bj.y, biz - bj.z
      local d2 = dx * dx + dy * dy + dz * dz
      local mag = dt / (d2 * sqrt(d2))
      local bm = bj.mass * mag
      bivx, bivy, bivz = bivx - dx * bm, bivy - dy * bm, bivz - dz * bm
      bm = bimass * mag
      bj.vx, bj.vy, bj.vz = bj.vx + dx * bm, bj.vy + dy * bm, bj.vz + dz * bm
    end
    bi.vx, bi.vy, bi.vz = bivx, bivy, bivz
    bi.x, bi.y, bi.z = bix + dt * bivx, biy + dt * bivy, biz + dt * bivz
  end
end
local function energy(bs, nb)
  local e = 0
  for i = 1, nb do
    local bi = bs[i]
    e = e + 0.5 * bi.mass * (bi.vx * bi.vx + bi.vy * bi.vy + bi.vz * bi.vz)
    for j = i + 1, nb do
      local bj = bs[j]
      local dx, dy, dz = bi.x - bj.x, bi.y - bj.y, bi.z - bj.z
      e = e - bi.mass * bj.mass / sqrt(dx * dx + dy * dy + dz * dz)
    end
  end
  return e
end
return { n = 4, run = function(n)
  local e = 0
  for _ = 1, n do
    local bs = bodies()
    for _ = 1, 10000 do advance(bs, #bs, 0.01) end
    e = e + energy(bs, #bs)
  end
  return e
end }
